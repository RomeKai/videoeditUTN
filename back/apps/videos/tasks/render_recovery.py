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

    A project that ended (completed, partial or failed: it failed while a clip
    rendered, or a settle did not run) with one of those reservations still
    RESERVED, or a whole video with its creation still RESERVED, is never
    re-queued: nothing renders it any more. Once idle, a paid clip still
    RENDERING there has a dead owner and fails (``dead_clip_after_end``, the
    project keeps its own error code), and finalize_project_render settles
    the reservation by the usual rule (``close``, ``ended_unsettled`` when no
    clip was left rendering).

Ingestions
    The same run also brings back an ingestion that makes no progress
    (``kind=ingestion``): a project uploaded or ingesting, before its review
    or render, whose creation reservation (``metadata['reservation_id']``) is
    RESERVED. Its message never ran (the broker lost it, or the create view
    could not queue it nor record that, NOT_QUEUED_UNCONFIRMED) or its worker
    died after its message was acked (the hard time limit). Nothing else would
    ever settle that reservation. Also one left ingesting at clips_selected
    with its creation charged already (its worker died before the hand-off to
    the review): nothing else would move it.

    No progress: no worker holds the project lock, and nothing happened for
    ``ingestion_stuck_after()`` (30 min by default): no delivery of the
    current chain started (``metadata['ingestion_started_at']``, written by
    every delivery that runs the ingestion, under the lock), no error was
    recorded (``pipeline_error_at``, the retry it schedules), and no re-queue
    of this routine; nor the creation or its reservation. The pipeline never
    waits that long by itself: a retry waits PIPELINE_RETRY_CAP_SECONDS at
    most, and a capacity wait re-queues every AI_CAPACITY_MAX_COUNTDOWN_SECONDS
    (each re-queue is a new start).

    What it does, deciding again under the project lock and the row lock
        fewer than INGESTION_RECOVERY_MAX_REQUEUES re-queues in a row that
        never started (a start resets them), fewer than
        INGESTION_RECOVERY_MAX_REQUEUES_TOTAL in all, and nothing that spends
        (paid stages, or the render of a whole video or an auto-render) for a
        reservation older than INGESTION_REQUEUE_MAX_AGE_SECONDS (a day)
                                                            -> re-queue
            process_initial_ingestion with a new chain, recorded in
            ``metadata['ingestion_recovery_requeues']``, ``[...]_total`` and
            ``[...]_requeued_at`` (reason ``never_started``: nothing started
            since the last hand-off; ``worker_lost``: the last thing that
            happened is a start, and its lock expired)
        otherwise, clips selected (or any clip)             -> report only: the
            work was delivered, never refunded (``clips_selected``)
        otherwise, idle less than ``ingestion_fail_after()``
        (PIPELINE_TIME_LIMIT + 15 min)                      -> report only
            (``requeue_cap``, or ``too_old``)
        otherwise                                           -> fail with
            INGESTION_STUCK and return the creation reservation, in one
            transaction under the row lock (``no_progress``, or ``too_old``:
            never re-queued, because the user paid for it more than a day ago)

    "Never started" or "its worker died" does not prove the old message is
    gone: it may be unacked on a dead host (back after the visibility
    timeout), a countdown held by a busy worker, or in a backed-up queue. So
    each re-queue starts a new chain (ingestion.INGESTION_CHAIN_KEY) and the
    old message, when it arrives, runs nothing: it never reopens for free a
    project the new copy failed and refunded, nor runs stages, plans capacity
    waits or spends retries next to it. The charge is RESERVED -> COMPLETED
    under the transaction's lock, once. The fail waits longer than any
    delivery can live and stores a chain no message has. A re-dispatch
    reserves again and runs it, with its own chain and count of re-queues.

Restores
    A render asked for after the project ended (CLIP_RENDER) gives the
    clips it never rendered back as they were once it is settled
    (``_restore_rerendered_clips``). If that restore fails after the settle
    committed (a database blip), nothing came back to it: the clip stays a
    draft with its old file. Every run retries it (``kind=restore``) for the
    projects that ended with ``metadata['clip_render_previous']``, their
    CLIP_RENDER reservation settled, and a clip to give back. The restore
    decides under the project row lock, so it never undoes a new paid render.

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
from apps.videos.services.ai.pipeline_state import PipelineStage, mark_failed, record_error
from apps.videos.services.ai.project_lock import (
    ProjectLockedError,
    project_lock,
    project_lock_held,
)
from apps.videos.tasks.ingestion import (
    APPROVED_RENDER, AUTO_RENDER, CLIP_RENDER, CLIP_RENDER_PREVIOUS_KEY, INGESTION_CHAIN_KEY,
    INGESTION_RECOVERY_REQUEUED_AT_KEY, INGESTION_RECOVERY_REQUEUES_KEY, INGESTION_RECOVERY_REQUEUES_TOTAL_KEY,
    INGESTION_STARTED_AT_KEY, INGESTION_STUCK, PaidClipRender, _is_whole_video, _paid_clip_renders, _pays_for,
    _queue_clip_renders, _restore_pending, _restore_rerendered_clips, new_ingestion_chain,
    process_initial_ingestion,
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
CLOSE, RESTORE = "close", "restore"
# What a candidate is: a paper edit of before (render_video_segments), a
# render clip by clip (an approved review or an auto-render), an ingestion
# (process_initial_ingestion) or the clips a settled render gives back.
PAPER_EDIT, CLIPS, INGESTION, RESTORES = "paper_edit", "clips", "ingestion", "restore"
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
_ACTS = (CHARGE, REQUEUE, FAIL, CLOSE, RESTORE)
_RESULTS = {
    CHARGE: "charged", REQUEUE: "requeued", FAIL: "failed", CLOSE: "closed", REPORT: "reported",
    SKIP: "skipped", RESTORE: "restored",
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


def _reservation(project, key='render_reservation_id'):
    """{'status', 'created_at'} of the project's render reservation (or the one in ``key``), or None."""
    reservation_id = (project.metadata or {}).get(key)
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

# Stages where the render ended (or the project failed while a clip rendered).
ENDED_STAGES = (PipelineStage.COMPLETED, PipelineStage.PARTIAL, PipelineStage.FAILED)
# The creation of a whole video pays its one clip (no clip ids: it pays them
# all). Only looked at once the project ended: its first render is not
# recovered at render_dispatched.
WHOLE_VIDEO_CREATION = PaidClipRender('reservation_id', 'whole_video_clip_ids', 'priced_duration_s', 'pipeline.')
# An ended project whose reservation waits for a clip left RENDERING by a dead
# owner: that clip fails and the reservation is settled.
DEAD_CLIP_AFTER_END = "dead_clip_after_end"


def _paid_reserved(project):
    """
    The clip-by-clip renders of the project (approval, auto-render, later
    render and, once a whole video ended, its creation) whose reservation is
    still RESERVED, as ``[(paid, created_at)]``.
    """
    metadata = project.metadata or {}
    reserved = []
    candidates = list(_paid_clip_renders(metadata))
    if _is_whole_video(project) and project.pipeline_stage in ENDED_STAGES and metadata.get('reservation_id'):
        candidates.append(WHOLE_VIDEO_CREATION)
    for paid in candidates:
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
    ended = project.pipeline_stage in ENDED_STAGES
    if not ended and _is_whole_video(project) and all(paid is not CLIP_RENDER for paid, _created in paid_renders):
        # Its first render is paid by its creation; one asked for later
        # (CLIP_RENDER) is recovered like any clip-by-clip render.
        return SKIP, "whole_video"
    if not ended and project.pipeline_stage != PipelineStage.RENDER_DISPATCHED:
        return SKIP, "not_rendering"
    if not paid_renders:
        return SKIP, "reservation_settled"
    if snapshot['idle_s'] is not None and snapshot['idle_s'] < stuck_after.total_seconds():
        return SKIP, "recent"
    if ended:
        # The project ended (it failed while a clip rendered, or a settle did
        # not run) with a reservation still RESERVED. Nothing renders it any
        # more: a clip still RENDERING has a dead owner by now and fails, and
        # the settle runs by the usual rule.
        if snapshot['rendering']:
            return FAIL, DEAD_CLIP_AFTER_END
        return CLOSE, "ended_unsettled"
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
            elif action == FAIL and reason == DEAD_CLIP_AFTER_END:
                # Only the dead clips: a draft does not hold the settle, and
                # the project keeps the code of the failure that ended it.
                _fail_stuck_clips([], snapshot['rendering'])
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
    Projects at render_dispatched, or ended, whose approval (with
    ``render_clip_ids``), auto-render or later render (CLIP_RENDER,
    apps/videos/views_clips.py) reservation is still RESERVED, and ended whole
    videos whose creation still is.
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
    whole_video_ended = Q(
        project_type=VideoProject.Type.SINGLE_EDIT, metadata__has_key='priced_duration_s',
        metadata__reservation_id__in=reserved, pipeline_stage__in=ENDED_STAGES,
    )
    return list(
        VideoProject.objects.filter(pipeline_stage__in=[PipelineStage.RENDER_DISPATCHED, *ENDED_STAGES])
        .filter(approved | auto_rendered | asked_later | whole_video_ended)
        .order_by('created_at', 'id').values_list('pk', flat=True)
    )


# --- Ingestions ---------------------------------------------------------------

# Where an ingestion runs: uploaded or ingesting (the user sees it processing),
# at a stage before its review or render.
INGESTING_STATUSES = (VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING)
INGESTING_STAGES = (
    PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED,
    PipelineStage.MODERATED, PipelineStage.CLIPS_SELECTED,
)
# No progress for this long, with no worker holding the lock: the ingestion is
# re-queued (see ingestion_stuck_after).
INGESTION_STUCK_AFTER_SECONDS = 30 * 60
# Re-queues in a row that never started: a start after the last one resets it.
INGESTION_RECOVERY_MAX_REQUEUES = 2
# Re-queues of one run of the ingestion in all, started or not: bounds a
# delivery that starts and dies every time (the hard time limit). A re-dispatch
# starts its own count.
INGESTION_RECOVERY_MAX_REQUEUES_TOTAL = 4
# Past PIPELINE_TIME_LIMIT (the longest a delivery can live) before a project
# whose re-queues are spent is failed and refunded.
INGESTION_FAIL_MARGIN_SECONDS = 15 * 60
# A re-queue that spends (the paid stages before clips_selected, or the render
# of a whole video or an auto-render) runs for a reservation the user made:
# only within this long of it (of the project, without one). An older one
# (projects stuck for days that the first run after a deploy finds, a long
# outage) is not re-queued: failed and refunded before clips_selected, only
# reported after. The user is never charged for work they stopped waiting for.
INGESTION_REQUEUE_MAX_AGE_SECONDS = 24 * 3600


def ingestion_stuck_after():
    """
    Idle seconds before an ingestion that no worker holds counts as stuck:
    INGESTION_STUCK_AFTER_SECONDS, and never less than twice the longest wait
    the pipeline schedules for itself (a retry, PIPELINE_RETRY_CAP_SECONDS;
    a capacity wait, AI_CAPACITY_MAX_COUNTDOWN_SECONDS plus its 20 % jitter).
    """
    from apps.videos.tasks.shared import PIPELINE_RETRY_CAP_SECONDS

    longest_wait = max(PIPELINE_RETRY_CAP_SECONDS, 1.2 * settings.AI_CAPACITY_MAX_COUNTDOWN_SECONDS)
    return max(INGESTION_STUCK_AFTER_SECONDS, 2 * longest_wait)


def ingestion_fail_after():
    """
    Idle seconds before an ingestion whose re-queues are spent is failed and
    refunded: longer than any delivery lives (PIPELINE_TIME_LIMIT, the hard
    limit), so no worker can still be running it, even one whose lock was lost.
    """
    return max(settings.PIPELINE_TIME_LIMIT + INGESTION_FAIL_MARGIN_SECONDS, ingestion_stuck_after())


def _ingestion_snapshot(project, reservation, now):
    """What the decision and the output read for an ingestion: its re-queues, idle seconds and age."""
    metadata = project.metadata or {}
    started_at = _when(metadata.get(INGESTION_STARTED_AT_KEY))
    requeued_at = _when(metadata.get(INGESTION_RECOVERY_REQUEUED_AT_KEY))
    requeues = _count(metadata, INGESTION_RECOVERY_REQUEUES_KEY)
    if requeued_at and started_at and started_at >= requeued_at:
        requeues = 0  # the last re-queue started: none in a row is pending
    # What hands the ingestion to a message: the creation (its reservation),
    # an error recorded (the retry it schedules) and a re-queue of this routine.
    handed_off = [moment for moment in (
        project.created_at, reservation['created_at'] if reservation else None, project.pipeline_error_at,
        requeued_at,
    ) if moment]
    times = handed_off + ([started_at] if started_at else [])
    paid_at = reservation['created_at'] if reservation else project.created_at
    return {
        'requeues': requeues,
        'requeues_total': _count(metadata, INGESTION_RECOVERY_REQUEUES_TOTAL_KEY),
        'idle_s': (now - max(times)).total_seconds() if times else None,
        # Paid for more than a day ago: nothing that spends is run again for it.
        'too_old': (now - paid_at).total_seconds() >= INGESTION_REQUEUE_MAX_AGE_SECONDS,
        # The last thing that happened is a start: its worker is gone (no lock).
        'started': started_at is not None and (not handed_off or started_at >= max(handed_off)),
        'clips': VideoClip.objects.filter(project_id=project.pk).exists(),
    }


def _requeue_spends(project):
    """
    A re-queue of the project would spend: the paid stages before
    clips_selected, or at clips_selected the render of a whole video or of an
    auto-render. Otherwise only the charge of the clips already selected and
    the hand-off to the review are left.
    """
    return (
        project.pipeline_stage != PipelineStage.CLIPS_SELECTED
        or _is_whole_video(project) or project.auto_render_bypass
    )


def decide_ingestion(project, reservation, snapshot):
    """(action, reason) for an ingestion, from its row, its creation reservation and its snapshot."""
    if project is None:
        return SKIP, "gone"
    if project.status not in INGESTING_STATUSES or project.pipeline_stage not in INGESTING_STAGES:
        return SKIP, "not_ingesting"
    selected = project.pipeline_stage == PipelineStage.CLIPS_SELECTED
    reserved = reservation is not None and reservation['status'] == Transaction.Status.RESERVED
    if not reserved and not selected:
        # Before its clips, an ingestion that is not paying for itself is not
        # this routine's: nothing would settle, and nothing is refunded.
        return SKIP, "no_reservation" if reservation is None else "reservation_settled"
    if snapshot['idle_s'] is not None and snapshot['idle_s'] < ingestion_stuck_after():
        return SKIP, "recent"
    requeues_left = (
        snapshot['requeues'] < INGESTION_RECOVERY_MAX_REQUEUES
        and snapshot['requeues_total'] < INGESTION_RECOVERY_MAX_REQUEUES_TOTAL
    )
    if requeues_left and not (snapshot['too_old'] and _requeue_spends(project)):
        # At clips_selected (charged already, or still reserved) the re-queued
        # delivery charges what is due once and hands it to the review.
        return REQUEUE, "worker_lost" if snapshot['started'] else "never_started"
    if selected or snapshot['clips']:
        # Its clips exist: the work was delivered, and is never refunded.
        return REPORT, "clips_selected"
    if snapshot['idle_s'] is not None and snapshot['idle_s'] < ingestion_fail_after():
        # No re-queue left (or too old for one): a delivery may still be alive
        # until this window ends.
        return REPORT, "requeue_cap" if not requeues_left else "too_old"
    return FAIL, "no_progress" if not requeues_left else "too_old"


def _preview_ingestion(project_id, _stuck_after=None):
    """The decision for an ingestion, without taking any lock or writing anything."""
    if project_lock_held(project_id):
        return SKIP, "lock_held", None
    project = VideoProject.objects.filter(pk=project_id).first()
    if project is None:
        return SKIP, "gone", None
    reservation = _reservation(project, 'reservation_id')
    snapshot = _ingestion_snapshot(project, reservation, timezone.now())
    action, reason = decide_ingestion(project, reservation, snapshot)
    return action, reason, snapshot


def _recover_ingestion(project_id, _stuck_after=None):
    """
    Decides again and acts under the project lock (no delivery can start
    meanwhile: one that arrives re-queues itself) and the row lock.
    """
    from apps.payments.services.wallet_service import PaymentError, rollback_reservation

    with project_lock(project_id, heartbeat=False):
        with transaction.atomic():
            project = VideoProject.objects.select_for_update().filter(pk=project_id).first()
            if project is None:
                return SKIP, "gone", None, "skipped"
            reservation = _reservation(project, 'reservation_id')
            now = timezone.now()
            snapshot = _ingestion_snapshot(project, reservation, now)
            action, reason = decide_ingestion(project, reservation, snapshot)
            result = _RESULTS[action]
            if action == REQUEUE:
                chain = new_ingestion_chain()
                metadata = dict(project.metadata or {})
                metadata[INGESTION_RECOVERY_REQUEUES_KEY] = snapshot['requeues'] + 1
                metadata[INGESTION_RECOVERY_REQUEUES_TOTAL_KEY] = snapshot['requeues_total'] + 1
                metadata[INGESTION_RECOVERY_REQUEUED_AT_KEY] = now.isoformat()
                # A new chain: a copy of the old one that was only delayed
                # runs nothing (ingestion.INGESTION_CHAIN_KEY).
                metadata[INGESTION_CHAIN_KEY] = chain
                VideoProject.objects.filter(pk=project_id).update(metadata=metadata)
                # Inside the transaction: if the broker refuses it, the record
                # rolls back and the next run tries again. A fresh delivery;
                # if it arrives before this lock is released, it re-queues
                # itself like any copy that finds the lock busy.
                process_initial_ingestion.apply_async(args=[str(project_id)], kwargs={'chain': chain})
            elif action == FAIL:
                try:
                    with transaction.atomic():
                        # Failed with its code and refunded, or neither: no one
                        # sees it failed and not refunded (views._fail_unqueued_project).
                        failed = mark_failed(project_id, expected_stage=project.pipeline_stage)
                        if failed:
                            record_error(project_id, INGESTION_STUCK)
                            # A chain no message has: none of the old ones runs it again.
                            VideoProject.objects.filter(pk=project_id).update(metadata={
                                **(project.metadata or {}), INGESTION_CHAIN_KEY: new_ingestion_chain(),
                            })
                            rollback_reservation(project.metadata['reservation_id'])
                except PaymentError:
                    failed = False  # rolled back with it: nothing was written
                if not failed:
                    # Settled or moved meanwhile (it cannot under these locks).
                    action, result = SKIP, "already_settled"
    if action in _ACTS:
        logger.warning(
            "pipeline.recovery_%s project_id=%s reason=%s requeues=%d requeues_total=%d",
            _RESULTS[action], project_id, reason, snapshot['requeues'] + (1 if action == REQUEUE else 0),
            snapshot['requeues_total'] + (1 if action == REQUEUE else 0),
        )
    return action, reason, snapshot, result


def _reserved_ids():
    """The ids of every RESERVED transaction, as the strings metadata holds."""
    return [
        str(pk) for pk in Transaction.objects.filter(status=Transaction.Status.RESERVED).values_list('pk', flat=True)
    ]


def _ingestion_candidates():
    """
    Projects uploaded or ingesting, before their review or render, created
    longer than ``ingestion_stuck_after()`` ago (a newer one cannot be stuck
    yet), whose creation reservation is RESERVED; and any still ingesting at
    clips_selected (its worker died after charging it: nothing would hand it
    to the review).
    """
    created_before = timezone.now() - timedelta(seconds=ingestion_stuck_after())
    selected = Q(pipeline_stage=PipelineStage.CLIPS_SELECTED, status=VideoProject.Status.INGESTING)
    reserved = _reserved_ids()
    paying = Q(metadata__reservation_id__in=reserved) if reserved else Q(pk__in=[])
    return list(
        VideoProject.objects.filter(
            status__in=INGESTING_STATUSES, pipeline_stage__in=INGESTING_STAGES, created_at__lte=created_before,
        ).filter(paying | selected).order_by('created_at', 'id').values_list('pk', flat=True)
    )


# --- Restores of a settled render asked for later ------------------------------

def _preview_restore(project_id, _stuck_after=None):
    """Whether the project has clips to give back now, read without a lock."""
    metadata = VideoProject.objects.filter(pk=project_id).values_list('metadata', flat=True).first()
    if metadata is None:
        return SKIP, "gone", None
    if not _restore_pending(metadata):
        return SKIP, "nothing_to_restore", None
    return RESTORE, "render_settled", None


def _recover_restore(project_id, _stuck_after=None):
    """_restore_rerendered_clips, which decides again under the project row lock."""
    restored = _restore_rerendered_clips(project_id)
    if restored:
        logger.warning("render.recovery_restored project_id=%s clips=%d", project_id, restored)
    return RESTORE, "render_settled", {'restored': restored}, "restored" if restored else "nothing_restored"


def _restore_candidates():
    """
    Projects that ended (completed, partial or failed) with the clips a render
    asked for later found delivered (CLIP_RENDER_PREVIOUS_KEY), that render's
    reservation settled, and a clip to give back as ``_restore_pending`` reads
    it (a draft or failed one with its old file, or a published one completed
    with a new file).
    """
    reserved = _reserved_ids()
    rows = (
        VideoProject.objects.filter(pipeline_stage__in=ENDED_STAGES, metadata__has_key=CLIP_RENDER_PREVIOUS_KEY)
        .exclude(**{f'metadata__{CLIP_RENDER_PREVIOUS_KEY}': {}})
        .exclude(**{f'metadata__{CLIP_RENDER.reservation_key}__in': reserved})
        .order_by('created_at', 'id').values_list('pk', 'metadata')
    )
    return [pk for pk, metadata in rows if _restore_pending(metadata)]


def recover_stuck_renders_now(apply=False):
    """
    Finds the stuck renders (paper edits of before, and clip-by-clip renders),
    the stuck ingestions and the restores a settled render left undone and,
    with ``apply``, recovers each one. One failure never stops the run.
    Returns one dict per candidate: ``project``, ``kind``, ``action``,
    ``reason``, ``result`` and, when read, ``starts``, ``requeues`` and
    ``idle_s``.
    """
    stuck_after = timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS)
    handlers = {
        PAPER_EDIT: (_preview, _recover), CLIPS: (_preview_clips, _recover_clips),
        INGESTION: (_preview_ingestion, _recover_ingestion), RESTORES: (_preview_restore, _recover_restore),
    }
    candidates = (
        [(pk, PAPER_EDIT) for pk in _candidates()] + [(pk, CLIPS) for pk in _clip_candidates()]
        + [(pk, INGESTION) for pk in _ingestion_candidates()] + [(pk, RESTORES) for pk in _restore_candidates()]
    )
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
                "render.recovery_error project_id=%s error=%s kind=%s", project_id, type(exc).__name__, kind,
            )
        if action == REPORT and kind == INGESTION:
            logger.warning(
                "pipeline.recovery_reported project_id=%s reason=%s requeues=%d requeues_total=%d",
                project_id, reason, snapshot['requeues'], snapshot['requeues_total'],
            )
        elif action == REPORT:
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
        "skip=%d errors=%d restore=%d",
        "apply" if apply else "dry_run", len(rows), totals[REQUEUE], totals[CHARGE],
        totals[FAIL], totals[CLOSE], totals[REPORT], totals[SKIP], errors, totals[RESTORE],
    )
    return rows


@celery_app.task(name='apps.videos.tasks.recover_stuck_renders', ignore_result=True)
def recover_stuck_renders():
    """
    Recovers stuck renders (paper edits and clip by clip) and stuck ingestions,
    and retries the restores a settled render left undone
    (CELERY_BEAT_SCHEDULE, every 15 min).
    """
    return len(recover_stuck_renders_now(apply=True))
