"""
Recovery of renders whose reservation stayed RESERVED with nothing to bring
them back. Two kinds:

- Paper edits approved before the review rendered clip by clip (one export
  of every segment, render_video_segments). Most of this docstring is about
  them. That part, and render_video_segments, can go once no project
  approved the old way still has its render reservation RESERVED.
- Clip by clip renders: an approved review (``render_clip_ids``), an
  auto-render or a render asked for after the project ended (CLIP_RENDER:
  a clip, a retry or a rerender, whole videos included), at render_dispatched
  with that reservation RESERVED (see "Clip by clip" below).

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
    RENDER_RECOVERY_MAX_REQUEUES re-queues a paper edit is only reported, so
    an operator decides; it is never failed nor refunded by this routine (a
    clip-by-clip render is settled instead, see below).

Safety
    The re-queued delivery renders only a project that is RENDERING with its
    reservation RESERVED, under the lock, so a duplicate message never renders
    or charges twice. A delivered render is charged and never refunded. Every
    decision is taken first without locks (a recent or busy project is skipped
    without touching its lock) and again under the locks before acting.
    Without ``apply`` nothing is written and no lock is taken.

Clip by clip
    A clip whose message was lost (the approval's ``delay`` failed and so did
    the resume it queued, or that resume ran out of retries; a broker that
    lost its queue) stays DRAFT, and one
    whose worker died after a lost redelivery stays RENDERING under a dead
    owner: either way the project never closes and its reservation is never
    settled. Once nothing happened for RENDER_STUCK_AFTER_SECONDS (no clip
    render started, ``metadata['render_clip_started_at']``, since the
    reservation, nor since a re-queue of this routine), the clips that
    reservation pays for are re-queued. A clip render has no heartbeat, and
    needs none: every claim records its start before rendering, and the task's
    hard time limit (RENDER_CLIP_TIME_LIMIT) is shorter than that threshold,
    so a clip still RENDERING by then has a dead owner. DRAFT ones go as they
    are, RENDERING ones with a takeover right pinned to their current owner (the
    ``render_takeover`` header), so a dead owner's clip is taken over and one
    that keeps killing its worker fails at RENDER_CLIP_MAX_STARTS. The same
    backoff as the paper edits applies. With every paid clip final and the
    fan-out still open (a close that failed), it is closed, which settles the
    reservation.

    Past the total cap (RENDER_RECOVERY_MAX_REQUEUES) a clip-by-clip render is
    not left reserved forever, as a paper edit is: once the backoff window of
    its last re-queue is over too, the clips it pays for that never started
    (DRAFT, or RENDERING under the dead owner the snapshot saw) fail, the
    project records ``render_stuck`` and the fan-out closes. That close
    settles the reservation by the usual rule: the share delivered is
    charged, the rest returned. A clip claimed meanwhile is left to its owner.
    A re-queue that the broker accepts clears the error code of the dispatch
    that could not queue it.

Runs every 15 minutes from CELERY_BEAT_SCHEDULE (task recover_stuck_renders)
and from ``manage.py recover_stuck_renders``. Logs and output carry ids,
counts and states only.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.payments.models import Transaction
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage, record_error
from apps.videos.services.ai.project_lock import (
    ProjectLockedError,
    project_lock,
    project_lock_held,
)
from apps.videos.tasks.ingestion import (
    APPROVED_RENDER, AUTO_RENDER, CLIP_RENDER, _is_whole_video, _paid_clip_renders, _pays_for,
    _queue_clip_renders,
)
from apps.videos.tasks.render import (
    RENDER_SEGMENTS_MAX_REDELIVERIES,
    RENDER_TAKEOVER_HEADER,
    TakeoverRight,
    _fail_render,
    _fail_unclaimed_clip,
    _settle_render_reservation,
    finalize_project_render,
    render_clip_task,
    render_video_segments,
)
from backend.celery import app as celery_app

logger = logging.getLogger(__name__)

CHARGE, REQUEUE, FAIL, REPORT, SKIP, ERROR = "charge", "requeue", "fail", "report", "skip", "error"
CLOSE = "close"
# What a candidate is: a paper edit of before (render_video_segments) or a
# render clip by clip (an approved review or an auto-render).
PAPER_EDIT, CLIPS = "paper_edit", "clips"
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
_ACTS = (CHARGE, REQUEUE, FAIL, CLOSE)
_RESULTS = {
    CHARGE: "charged", REQUEUE: "requeued", FAIL: "failed", CLOSE: "closed", REPORT: "reported",
    SKIP: "skipped",
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


# --- Clip by clip (approved reviews and auto-renders) ------------------------

def _paid_reserved(project):
    """
    The clip-by-clip renders of the project (approval, auto-render) whose
    reservation is still RESERVED, as ``[(paid, created_at)]``.
    """
    metadata = project.metadata or {}
    reserved = []
    for paid in _paid_clip_renders(metadata):
        try:
            created_at = Transaction.objects.filter(
                pk=metadata[paid.reservation_key], status=Transaction.Status.RESERVED,
            ).values_list('created_at', flat=True).first()
        except ValidationError:
            created_at = None  # not a transaction id
        if created_at is not None:
            reserved.append((paid, created_at))
    return reserved


def _open_clips(project, paid_renders):
    """(DRAFT ids, [(RENDERING id, owner token)]) of the clips those reservations pay for."""
    metadata = project.metadata or {}
    rows = VideoClip.objects.filter(
        project_id=project.pk, status__in=[VideoClip.Status.DRAFT, VideoClip.Status.RENDERING],
    ).values_list('pk', 'status', 'render_claim_token', 'render_unfinished_starts')
    paid_rows = [
        row for row in rows if any(_pays_for(metadata, paid, row[0]) for paid, _created in paid_renders)
    ]
    drafts = [pk for pk, status, _token, _starts in paid_rows if status == VideoClip.Status.DRAFT]
    rendering = [(pk, token) for pk, status, token, _starts in paid_rows if status == VideoClip.Status.RENDERING]
    starts = max((count for _pk, _status, _token, count in paid_rows), default=0)
    return drafts, rendering, starts


def _clip_snapshot(project, paid_renders, now):
    """What the decision and the output read, for a clip-by-clip render."""
    metadata = project.metadata or {}
    started_at = _when(metadata.get('render_clip_started_at'))
    requeued_at = _when(metadata.get('render_recovery_requeued_at'))
    requeues = _count(metadata, 'render_recovery_requeues')
    if requeued_at and started_at and started_at >= requeued_at:
        requeues = 0  # a render started after the last re-queue
    drafts, rendering, starts = _open_clips(project, paid_renders)
    times = [moment for moment in (
        *(created for _paid, created in paid_renders), started_at, requeued_at,
    ) if moment]
    return {
        'starts': starts,
        'requeues': requeues,
        'requeues_total': _count(metadata, 'render_recovery_requeues_total'),
        'idle_s': (now - max(times)).total_seconds() if times else None,
        'drafts': drafts,
        'rendering': rendering,
    }


def decide_clips(project, paid_renders, snapshot, stuck_after):
    """(action, reason) for a clip-by-clip render, from its row, its reserved renders and its snapshot."""
    if project is None:
        return SKIP, "gone"
    if _is_whole_video(project) and all(paid is not CLIP_RENDER for paid, _created in paid_renders):
        # Its first render is paid by its creation; one asked for later
        # (CLIP_RENDER) is recovered like any clip-by-clip render.
        return SKIP, "whole_video"
    if project.pipeline_stage != PipelineStage.RENDER_DISPATCHED:
        return SKIP, "not_rendering"
    if not paid_renders:
        return SKIP, "reservation_settled"
    if snapshot['idle_s'] is not None and snapshot['idle_s'] < stuck_after.total_seconds():
        return SKIP, "recent"
    if not snapshot['drafts'] and not snapshot['rendering']:
        # Every paid clip is final but the fan-out stayed open (its close failed).
        return CLOSE, "fan_out_open"
    if (
        snapshot['requeues'] and snapshot['idle_s'] is not None
        and snapshot['idle_s'] < backoff_window(stuck_after, snapshot['requeues'])
    ):
        # Re-queued and never started: its last message may still start.
        return REPORT, "never_started"
    if snapshot['requeues_total'] >= RENDER_RECOVERY_MAX_REQUEUES:
        # Given up: the clips that never started fail and the reservation is
        # settled by what was delivered, never left reserved forever.
        return FAIL, "requeue_total_cap"
    if snapshot['requeues'] == 0:
        return REQUEUE, "clips_left"
    return REQUEUE, "never_started"


def _preview_clips(project_id, stuck_after):
    """The decision for a clip-by-clip render, without taking any lock or writing anything."""
    if project_lock_held(project_id):
        return SKIP, "lock_held", None
    project = VideoProject.objects.filter(pk=project_id).first()
    if project is None:
        return SKIP, "gone", None
    paid_renders = _paid_reserved(project)
    snapshot = _clip_snapshot(project, paid_renders, timezone.now())
    action, reason = decide_clips(project, paid_renders, snapshot, stuck_after)
    return action, reason, snapshot


def _requeue_clips(project_id, drafts, rendering):
    """
    Queues the clips again: DRAFT ones as any dispatch does, RENDERING ones
    with the right to take over from the owner they have now (its worker is
    presumed dead). Tries every clip; raises the first error, so the caller
    does not record a re-queue the broker refused.
    """
    failure = _queue_clip_renders(project_id, drafts)
    for clip_id, token in rendering:
        try:
            render_clip_task.apply_async(
                args=[str(clip_id)], headers={RENDER_TAKEOVER_HEADER: TakeoverRight(token).header_value()},
            )
        except Exception as exc:
            logger.error(
                "render.dispatch_failed project_id=%s clip_id=%s error=%s",
                project_id, clip_id, type(exc).__name__,
            )
            failure = failure or exc
    if failure is not None:
        raise failure


def _fail_stuck_clips(drafts, rendering):
    """
    Fails the clips the recovery gave up on: DRAFT ones with no owner, and
    RENDERING ones still under the owner the snapshot saw (dead: nothing
    started for longer than a render can live). Each write is conditional
    (``_fail_unclaimed_clip``), so a clip claimed since is left to its owner.
    Returns how many it failed.
    """
    failed = sum(_fail_unclaimed_clip(clip_id) for clip_id in drafts)
    return failed + sum(
        _fail_unclaimed_clip(clip_id, takeover=TakeoverRight(token)) for clip_id, token in rendering
    )


def _recover_clips(project_id, stuck_after):
    """Decides again and acts under the project lock and the row lock, like ``_recover``."""
    with project_lock(project_id, heartbeat=False):
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().filter(pk=project_id).first()
            if project is None:
                return SKIP, "gone", None, "skipped"
            paid_renders = _paid_reserved(project)
            now = timezone.now()
            snapshot = _clip_snapshot(project, paid_renders, now)
            action, reason = decide_clips(project, paid_renders, snapshot, stuck_after)
            if action == REQUEUE:
                metadata = dict(project.metadata or {})
                metadata['render_recovery_requeues'] = snapshot['requeues'] + 1
                metadata['render_recovery_requeues_total'] = snapshot['requeues_total'] + 1
                metadata['render_recovery_requeued_at'] = now.isoformat()
                # The error of the dispatch that could not queue the clips
                # goes with a re-queue that could.
                VideoProject.objects.filter(pk=project_id).update(
                    metadata=metadata, pipeline_error_code="", pipeline_error_at=None,
                )
                # Inside the transaction: if the broker refuses a message, the
                # record rolls back and the next run tries again. A duplicate
                # message is harmless (the clip claim is a compare-and-set).
                _requeue_clips(project_id, snapshot['drafts'], snapshot['rendering'])
            elif action == FAIL:
                _fail_stuck_clips(snapshot['drafts'], snapshot['rendering'])
                record_error(project_id, STUCK_CODE)
        if action in (CLOSE, FAIL):
            # Its own row lock, and the settle of the reservation, outside ours.
            finalize_project_render(project_id)
    if action in _ACTS:
        logger.warning(
            "render.recovery_%s project_id=%s reason=%s drafts=%d rendering=%d requeues=%d "
            "requeues_total=%d kind=clips",
            _RESULTS[action], project_id, reason, len(snapshot['drafts']), len(snapshot['rendering']),
            snapshot['requeues'], snapshot['requeues_total'],
        )
    return action, reason, snapshot, _RESULTS[action]


def _clip_candidates():
    """
    Projects at render_dispatched whose approval (with ``render_clip_ids``),
    auto-render or later render (CLIP_RENDER, apps/videos/views_clips.py)
    reservation is still RESERVED.
    """
    reserved = [
        str(pk) for pk in Transaction.objects.filter(
            status=Transaction.Status.RESERVED,
        ).values_list('pk', flat=True)
    ]
    if not reserved:
        return []
    approved = Q(
        metadata__has_key=APPROVED_RENDER.clip_ids_key,
        **{f'metadata__{APPROVED_RENDER.reservation_key}__in': reserved},
    )
    auto_rendered = Q(**{f'metadata__{AUTO_RENDER.reservation_key}__in': reserved})
    asked_later = Q(**{f'metadata__{CLIP_RENDER.reservation_key}__in': reserved})
    return list(
        VideoProject.objects.filter(pipeline_stage=PipelineStage.RENDER_DISPATCHED)
        .filter(approved | auto_rendered | asked_later)
        .order_by('created_at', 'id').values_list('pk', flat=True)
    )


def recover_stuck_renders_now(apply=False):
    """
    Finds the stuck renders (paper edits of before, and clip-by-clip renders)
    and, with ``apply``, recovers each one. One failure never stops the run.
    Returns one dict per candidate: ``project``, ``kind``, ``action``,
    ``reason``, ``result`` and, when read, ``starts``, ``requeues`` and
    ``idle_s``.
    """
    stuck_after = timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS)
    handlers = {PAPER_EDIT: (_preview, _recover), CLIPS: (_preview_clips, _recover_clips)}
    candidates = [(pk, PAPER_EDIT) for pk in _candidates()] + [(pk, CLIPS) for pk in _clip_candidates()]
    rows = []
    for project_id, kind in candidates:
        preview, recover = handlers[kind]
        snapshot = None
        try:
            action, reason, snapshot = preview(project_id, stuck_after)
            if apply and action in _ACTS:
                action, reason, snapshot, result = recover(project_id, stuck_after)
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
                "requeues_total=%d kind=%s",
                "not_started" if reason == "never_started" else "reported", project_id, reason,
                snapshot['starts'], snapshot['requeues'], snapshot['requeues_total'], kind,
            )
        row = {
            'project': project_id, 'kind': kind, 'action': action, 'reason': reason, 'result': result,
            **(snapshot or {}),
        }
        for key in ('drafts', 'rendering'):
            if key in row:
                row[key] = len(row[key])
        rows.append(row)

    totals = {name: sum(1 for row in rows if row['action'] == name) for name in _RESULTS}
    errors = sum(1 for row in rows if row['action'] == ERROR)
    logger.info(
        "render.recovery_done mode=%s candidates=%d requeue=%d charge=%d fail=%d close=%d report=%d "
        "skip=%d errors=%d",
        "apply" if apply else "dry_run", len(rows), totals[REQUEUE], totals[CHARGE],
        totals[FAIL], totals[CLOSE], totals[REPORT], totals[SKIP], errors,
    )
    return rows


@celery_app.task(name='apps.videos.tasks.recover_stuck_renders', ignore_result=True)
def recover_stuck_renders():
    """Recovers stuck renders, paper edits and clip by clip (CELERY_BEAT_SCHEDULE, every 15 min)."""
    return len(recover_stuck_renders_now(apply=True))
