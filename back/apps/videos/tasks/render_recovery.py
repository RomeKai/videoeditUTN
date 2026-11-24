"""
Recovery of paper-edit renders whose render reservation stayed RESERVED.

Why they exist
    render_video_segments acks late, so a worker that dies mid-render gets
    its message back. Two cases never come back: a worker killed by the hard
    time limit (RENDER_SEGMENTS_TIME_LIMIT), because Celery acks the message
    of a task it killed for its time limit; and a redelivery after a broker
    blip that then loses its worker (an OOM), because the reject refers to a
    delivery tag the broker already restored. A render that was never queued
    (the approval's ``delay`` failed) is stuck too. All of them leave the
    project RENDERING with its render reservation RESERVED. A hard kill
    between saving the export and charging leaves it COMPLETED, still
    RESERVED.

Candidates
    Projects RENDERING or COMPLETED whose render reservation (``metadata
    ['render_reservation_id']``) is RESERVED. One is left alone while a
    worker holds its project lock, or while something happened within
    RENDER_STUCK_AFTER_SECONDS: a start (``metadata['render_started_at']``),
    the approval (the reservation's ``created_at``) or a re-queue by this
    routine (``metadata['render_recovery_requeued_at']``). Whole-video
    projects never are: they render through render_clip_task.

What it does, deciding again under the project lock and the row lock
    COMPLETED with its export (final_export_r2_key) -> charge: delivered, never refunded
    RENDERING with more lost starts than
    RENDER_SEGMENTS_MAX_REDELIVERIES (the rule
    render_video_segments applies at its start)    -> fail: _fail_render with
                                                      render_stuck, reservation returned
    RENDERING, fewer re-queues in a row that never
    started than RENDER_SEGMENTS_MAX_REDELIVERIES  -> re-queue render_video_segments
    RENDERING past that cap                         -> report only (never_started)
    A RENDERING project with an export saved (it cannot happen: the export
    is saved together with COMPLETED) is only reported, never failed.

    A re-queue is recorded (``render_recovery_requeues`` and the time), so the
    project is not re-queued again before that message could start; a start
    after the last re-queue resets the count. A render that never starts is
    never failed: its message may only be waiting for a render worker (down
    or backlogged), and a FAILED paper edit cannot be rendered again. It is
    reported (``render.recovery_not_started``) until it starts. Only renders
    that started and lost their worker too many times fail and are refunded.

Safety
    The re-queued delivery renders only a project that is RENDERING with its
    reservation RESERVED, under the lock, so a duplicate message never renders
    or charges twice. A delivered render is charged and never refunded. Every
    decision is taken first without locks (a recent or busy project is skipped
    without touching its lock) and again under the locks before acting.
    Without ``apply`` nothing is written and no lock is taken.

Runs every 15 minutes from CELERY_BEAT_SCHEDULE (task recover_stuck_renders)
and from ``manage.py recover_stuck_renders``. Logs and output carry ids,
counts and states only.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.payments.models import Transaction
from apps.videos.models import VideoProject
from apps.videos.services.ai.project_lock import (
    ProjectLockedError,
    project_lock,
    project_lock_held,
)
from apps.videos.tasks.ingestion import _is_whole_video
from apps.videos.tasks.render import (
    RENDER_SEGMENTS_MAX_REDELIVERIES,
    _fail_render,
    _settle_render_reservation,
    render_video_segments,
)
from backend.celery import app as celery_app

logger = logging.getLogger(__name__)

CHARGE, REQUEUE, FAIL, REPORT, SKIP, ERROR = "charge", "requeue", "fail", "report", "skip", "error"
STUCK_CODE = "render_stuck"
# Actions that write: only these take the locks.
_ACTS = (CHARGE, REQUEUE, FAIL)
_RESULTS = {
    CHARGE: "charged", REQUEUE: "requeued", FAIL: "failed", REPORT: "reported", SKIP: "skipped",
}


def _count(metadata, key):
    try:
        return max(0, int(metadata.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _when(value):
    """A datetime from an ISO string in metadata, or None."""
    try:
        parsed = parse_datetime(str(value)) if value else None
    except ValueError:
        return None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _reservation(project):
    """{'status', 'created_at'} of the project's render reservation, or None."""
    reservation_id = (project.metadata or {}).get('render_reservation_id')
    if not reservation_id:
        return None
    try:
        return Transaction.objects.filter(pk=reservation_id).values('status', 'created_at').first()
    except ValidationError:
        return None  # not a transaction id


def _snapshot(project, reservation, now):
    """What the decision and the output read: counters and idle seconds."""
    metadata = project.metadata or {}
    started_at = _when(metadata.get('render_started_at'))
    requeued_at = _when(metadata.get('render_recovery_requeued_at'))
    requeues = _count(metadata, 'render_recovery_requeues')
    if requeued_at and started_at and started_at >= requeued_at:
        requeues = 0  # the last re-queue started: none in a row is pending
    times = [moment for moment in (
        reservation['created_at'] if reservation else None, started_at, requeued_at,
    ) if moment]
    return {
        'starts': _count(metadata, 'render_unfinished_starts'),
        'requeues': requeues,
        'idle_s': (now - max(times)).total_seconds() if times else None,
    }


def decide(project, reservation, snapshot, stuck_after):
    """(action, reason) for one candidate, from its row, its reservation and its snapshot."""
    if project is None:
        return SKIP, "gone"
    if _is_whole_video(project):
        return SKIP, "whole_video"
    if reservation is None:
        return SKIP, "no_reservation"
    if reservation['status'] != Transaction.Status.RESERVED:
        return SKIP, "reservation_settled"
    if project.status not in (VideoProject.Status.RENDERING, VideoProject.Status.COMPLETED):
        return SKIP, "not_rendering"
    if snapshot['idle_s'] is not None and snapshot['idle_s'] < stuck_after.total_seconds():
        return SKIP, "recent"
    if project.status == VideoProject.Status.COMPLETED:
        if project.final_export_r2_key:
            # Saved, then its worker died before charging: delivered, so charged.
            return CHARGE, "delivered_unpaid"
        return SKIP, "not_delivered"
    if project.final_export_r2_key:
        # Unreachable (the export is saved with COMPLETED): never refund it.
        return REPORT, "export_saved"
    if snapshot['starts'] > RENDER_SEGMENTS_MAX_REDELIVERIES:
        # The same rule render_video_segments applies when it starts.
        return FAIL, "lost_starts"
    if snapshot['requeues'] < RENDER_SEGMENTS_MAX_REDELIVERIES:
        return REQUEUE, "no_render_left"
    return REPORT, "never_started"


def _preview(project_id, stuck_after):
    """The decision without taking any lock or writing anything."""
    if project_lock_held(project_id):
        return SKIP, "lock_held", None
    project = VideoProject.objects.filter(pk=project_id).first()
    if project is None:
        return SKIP, "gone", None
    reservation = _reservation(project)
    snapshot = _snapshot(project, reservation, timezone.now())
    action, reason = decide(project, reservation, snapshot, stuck_after)
    return action, reason, snapshot


def _recover(project_id, stuck_after):
    """
    Decides again and acts while holding the project lock (no render can
    start, save or charge meanwhile) and the row lock. Raises
    ProjectLockedError when a worker took the project in between.
    """
    with project_lock(project_id, heartbeat=False):
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().filter(pk=project_id).first()
            if project is None:
                return SKIP, "gone", None
            reservation = _reservation(project)
            now = timezone.now()
            snapshot = _snapshot(project, reservation, now)
            action, reason = decide(project, reservation, snapshot, stuck_after)
            if action == REQUEUE:
                metadata = dict(project.metadata or {})
                metadata['render_recovery_requeues'] = snapshot['requeues'] + 1
                metadata['render_recovery_requeued_at'] = now.isoformat()
                VideoProject.objects.filter(pk=project_id).update(metadata=metadata)
                # Published inside the transaction: if the broker refuses it,
                # the record rolls back and the next run tries again. The new
                # delivery is a fresh one (no lock header, no retries spent);
                # if it arrives before this lock is released, it re-queues
                # itself like any copy that finds the lock busy.
                render_video_segments.apply_async(args=[str(project_id)])
        # Settled once the row lock is released (the wallet takes its own
        # locks), still under the project lock.
        if action == CHARGE:
            _settle_render_reservation(project_id, charge=True)
        elif action == FAIL:
            _fail_render(project_id, STUCK_CODE)
    if action in _ACTS:
        logger.warning(
            "render.recovery_%s project_id=%s reason=%s unfinished_starts=%d requeues=%d",
            _RESULTS[action], project_id, reason, snapshot['starts'], snapshot['requeues'],
        )
    return action, reason, snapshot


def _candidates():
    """RENDERING or COMPLETED projects whose render reservation is still RESERVED."""
    reserved = [
        str(pk) for pk in Transaction.objects.filter(
            status=Transaction.Status.RESERVED,
        ).values_list('pk', flat=True)
    ]
    if not reserved:
        return []
    return list(
        VideoProject.objects.filter(
            status__in=[VideoProject.Status.RENDERING, VideoProject.Status.COMPLETED],
            metadata__render_reservation_id__in=reserved,
        ).order_by('created_at', 'id').values_list('pk', flat=True)
    )


def recover_stuck_renders_now(apply=False):
    """
    Finds the stuck paper edits and, with ``apply``, recovers each one. One
    failure never stops the run. Returns one dict per candidate: ``project``,
    ``action``, ``reason``, ``result`` and, when read, ``starts``,
    ``requeues`` and ``idle_s``.
    """
    stuck_after = timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS)
    rows = []
    for project_id in _candidates():
        snapshot = None
        try:
            action, reason, snapshot = _preview(project_id, stuck_after)
            if apply and action in _ACTS:
                action, reason, snapshot = _recover(project_id, stuck_after)
                result = _RESULTS[action]
            elif action in _ACTS:
                result = "dry_run"
            else:
                result = _RESULTS[action]
        except ProjectLockedError:
            action, reason, result = SKIP, "lock_held", "skipped"
        except Exception as exc:
            # The class name only, and on to the next project.
            action, reason, result = ERROR, "-", f"error={type(exc).__name__}"
            logger.error(
                "render.recovery_error project_id=%s error=%s", project_id, type(exc).__name__,
            )
        if action == REPORT:
            logger.warning(
                "render.recovery_%s project_id=%s unfinished_starts=%d requeues=%d",
                "not_started" if reason == "never_started" else "reported", project_id,
                snapshot['starts'], snapshot['requeues'],
            )
        rows.append({
            'project': project_id, 'action': action, 'reason': reason, 'result': result,
            **(snapshot or {}),
        })

    totals = {name: sum(1 for row in rows if row['action'] == name) for name in _RESULTS}
    errors = sum(1 for row in rows if row['action'] == ERROR)
    logger.info(
        "render.recovery_done mode=%s candidates=%d requeue=%d charge=%d fail=%d report=%d "
        "skip=%d errors=%d",
        "apply" if apply else "dry_run", len(rows), totals[REQUEUE], totals[CHARGE],
        totals[FAIL], totals[REPORT], totals[SKIP], errors,
    )
    return rows


@celery_app.task(name='apps.videos.tasks.recover_stuck_renders', ignore_result=True)
def recover_stuck_renders():
    """Recovers stuck paper-edit renders (CELERY_BEAT_SCHEDULE, every 15 min)."""
    return len(recover_stuck_renders_now(apply=True))
