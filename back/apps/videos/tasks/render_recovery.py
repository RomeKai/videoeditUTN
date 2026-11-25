"""
Recovery of paper-edit renders whose render reservation stayed RESERVED.

Only approvals from before the review rendered clip by clip: since then an
approval renders each segment as its own clip (render_clip_task) and never
queues render_video_segments. This module, and render_video_segments, can go
once no project approved the old way still has its render reservation
RESERVED.

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
    RENDERING, RENDER_RECOVERY_MAX_REQUEUES
    re-queues by this routine already               -> report only (requeue_total_cap)
    RENDERING, idle for its backoff window          -> re-queue render_video_segments
    RENDERING, idle, but within that window         -> report only (never_started)
    A RENDERING project with an export saved (it cannot happen: the export
    is saved together with COMPLETED) is only reported, never failed.

    A re-queue is recorded (``render_recovery_requeues``, the re-queues in a
    row that never started, ``render_recovery_requeues_total`` and the time),
    so the project is not re-queued again before that message could start.
    Each re-queue in a row that never started doubles the idle window, up to
    RENDER_RECOVERY_MAX_BACKOFF_SECONDS; a start resets it. A render that
    never starts is never failed: its message may only be waiting for a
    render worker (down or backlogged), and a FAILED paper edit cannot be
    rendered again. If every copy of the message is gone (the broker lost
    them), the next re-queue brings it back. Only renders that started and
    lost their worker too many times fail and are refunded.

    The total cap bounds a loop the lost-starts cap does not see: a re-queued
    start that ends in a handled error (so it closes its start) and whose
    retry message is lost resets the re-queues in a row every time. Past
    RENDER_RECOVERY_MAX_REQUEUES re-queues the project is only reported, so an
    operator decides; it is never failed nor refunded by this routine.

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
from apps.videos.tasks.ingestion import APPROVED_RENDER, _is_whole_video
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
# The idle window of a render that never started doubles with each re-queue
# in a row, up to this (24 h), or RENDER_STUCK_AFTER_SECONDS if that is longer.
RENDER_RECOVERY_MAX_BACKOFF_SECONDS = 24 * 3600
# Re-queues this routine makes for one project, ever. Renders that start and
# lose their worker need at most RENDER_SEGMENTS_MAX_REDELIVERIES re-queues
# before the lost-starts cap fails them; twice that, plus six backoff steps for
# a message that never starts (the window reaches 24 h at the fourth try:
# about eight days of tries with the default threshold). Past it: report only.
RENDER_RECOVERY_MAX_REQUEUES = 2 * RENDER_SEGMENTS_MAX_REDELIVERIES + 6
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
        'requeues_total': _count(metadata, 'render_recovery_requeues_total'),
        'idle_s': (now - max(times)).total_seconds() if times else None,
    }


def backoff_window(stuck_after, requeues):
    """
    Idle seconds before the next re-queue of a render that was re-queued
    ``requeues`` times in a row without starting: RENDER_STUCK_AFTER_SECONDS,
    doubled with each of them, up to RENDER_RECOVERY_MAX_BACKOFF_SECONDS.
    """
    base = stuck_after.total_seconds()
    ceiling = max(base, RENDER_RECOVERY_MAX_BACKOFF_SECONDS)
    return min(base * (2 ** min(requeues, 32)), ceiling)


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
    if snapshot['requeues_total'] >= RENDER_RECOVERY_MAX_REQUEUES:
        return REPORT, "requeue_total_cap"
    if snapshot['requeues'] == 0:
        return REQUEUE, "no_render_left"
    if snapshot['idle_s'] is not None and snapshot['idle_s'] < backoff_window(stuck_after, snapshot['requeues']):
        # Re-queued and never started: waiting out its backoff window.
        return REPORT, "never_started"
    return REQUEUE, "never_started"


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
                return SKIP, "gone", None, "skipped"
            reservation = _reservation(project)
            now = timezone.now()
            snapshot = _snapshot(project, reservation, now)
            action, reason = decide(project, reservation, snapshot, stuck_after)
            if action == REQUEUE:
                metadata = dict(project.metadata or {})
                metadata['render_recovery_requeues'] = snapshot['requeues'] + 1
                metadata['render_recovery_requeues_total'] = snapshot['requeues_total'] + 1
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
        result = _RESULTS[action]
        if action == CHARGE and not _settle_render_reservation(project_id, charge=True):
            # Not charged by this call: settled meanwhile, or the wallet
            # failed (render.settle_failed). The result says which.
            settled = Transaction.objects.filter(
                pk=(project.metadata or {}).get('render_reservation_id'),
            ).exclude(status=Transaction.Status.RESERVED).exists()
            action, result = (SKIP, "already_settled") if settled else (ERROR, "error=settle_failed")
            if action == ERROR:
                logger.error("render.recovery_error project_id=%s error=settle_failed", project_id)
        elif action == FAIL:
            _fail_render(project_id, STUCK_CODE)
    if action in _ACTS:
        logger.warning(
            "render.recovery_%s project_id=%s reason=%s unfinished_starts=%d requeues=%d "
            "requeues_total=%d",
            _RESULTS[action], project_id, reason, snapshot['starts'], snapshot['requeues'],
            snapshot['requeues_total'],
        )
    return action, reason, snapshot, result


def _candidates():
    """
    RENDERING or COMPLETED projects whose render reservation is still RESERVED,
    approved before the review rendered clip by clip. An approval with clip ids
    (``render_clip_ids``) renders with render_clip_task and is settled by what
    its clips deliver (finalize_project_render): it is never one of these.
    """
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
        ).exclude(
            metadata__has_key=APPROVED_RENDER.clip_ids_key,
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
                action, reason, snapshot, result = _recover(project_id, stuck_after)
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
                "render.recovery_%s project_id=%s reason=%s unfinished_starts=%d requeues=%d "
                "requeues_total=%d",
                "not_started" if reason == "never_started" else "reported", project_id, reason,
                snapshot['starts'], snapshot['requeues'], snapshot['requeues_total'],
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
