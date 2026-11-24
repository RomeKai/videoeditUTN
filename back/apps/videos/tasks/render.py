"""
Render tasks: one clip per task, the paper-edit render and the fan-out close.
"""

import json
import os
import uuid
import logging
from celery import shared_task
from celery.exceptions import Reject
from django.conf import settings
from django.core.files import File
from apps.videos.models import AIUsageRecord, VideoProject, VideoClip
from apps.payments.models import Transaction
from moviepy import VideoFileClip, concatenate_videoclips
from apps.videos.services.selection_engine import GeminiFlashStrategy, SelectionEngine
from apps.videos.services.ai.contracts import ProviderUsage
from apps.videos.services.ai.usage_recorder import (
    record_usages,
    usages_for_failure,
    usages_for_success,
)
from apps.videos.services.ai.errors import (
    AIEmptySelectionError,
    AIError,
    NonRetryableAIError,
    is_retryable_error,
    is_task_time_limit,
)
from apps.videos.services.ai.pipeline_state import (
    STAGE_ORDER,
    TERMINAL_STAGES,
    PipelineConflictError,
    PipelineStage,
    PipelineStateError,
    StageStatus,
    advance,
    advance_to,
    infer_completed_stage,
    last_completed_stage,
    mark_failed,
    record_error,
    reopen,
    set_stage_status,
)
from apps.videos.services.segments import InvalidSegmentsError, validate_approved_segments
from apps.videos.services.ai.capacity import CapacityUnavailable
from apps.videos.services.ai.project_lock import (
    ProjectLockedError,
    ProjectLockLostError,
    project_lock,
)
import math
import time
import random
from contextlib import contextmanager
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.core.cache import cache
from apps.videos.models import VideoProject, VideoClip, ScheduledPost
from backend.celery import app as celery_app

from apps.videos.tasks.shared import (
    PIPELINE_LOCK_MAX_RETRIES,
    PIPELINE_LOCK_RETRY_SECONDS,
    _is_retryable_pipeline_error,
    _is_transient_storage_connection_error,
    _publish_requeue,
    _retry_countdown,
)


logger = logging.getLogger(__name__)

def finalize_project_render(project_id):
    """
    Closes the render fan-out once every clip reached a final state: all rendered
    -> COMPLETED, any failed -> PARTIAL (never COMPLETED with failures). Safe to
    call after every clip: counting happens under the project row lock, so only
    the call that sees the last clip finish transitions the project.

    A whole-video project (``single_edit``) is paid by its creation reservation,
    settled here from where the project ended: every call checks it, so it is
    settled even if the project left render_dispatched another way.
    """
    from apps.videos.tasks.ingestion import _settle_whole_video_reservation

    stage = _close_render_fan_out(project_id)
    _settle_whole_video_reservation(project_id)
    return stage


def _close_render_fan_out(project_id):
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        if project.pipeline_stage != PipelineStage.RENDER_DISPATCHED:
            return None

        statuses = set(
            VideoClip.objects.filter(project_id=project_id).values_list('status', flat=True)
        )
        if statuses & {VideoClip.Status.DRAFT, VideoClip.Status.RENDERING}:
            return None

        if VideoClip.Status.FAILED in statuses:
            stage, status = PipelineStage.PARTIAL, VideoProject.Status.PARTIAL
        else:
            stage, status = PipelineStage.COMPLETED, VideoProject.Status.COMPLETED

        try:
            advance(project_id, PipelineStage.RENDER_DISPATCHED, stage)
        except PipelineConflictError:
            return None
        VideoProject.objects.filter(pk=project_id).update(status=status)

    logger.info(
        "pipeline.render_finalized project_id=%s stage=%s", project_id, stage,
        extra={'project_id': str(project_id), 'stage': str(stage)},
    )
    return stage


class ClipRenderTask(celery_app.Task):
    """
    Marks the clip (and possibly the project) as failed when a render task
    fails for good. Celery calls it only after the last attempt (a retry
    calls ``on_retry``), and a clip already delivered is never marked failed.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        clip_id = args[0] if args else kwargs.get('clip_id')
        try:
            clip = VideoClip.objects.get(pk=clip_id)
            VideoClip.objects.filter(pk=clip_id).exclude(
                status__in=[VideoClip.Status.COMPLETED, VideoClip.Status.PUBLISHED],
            ).update(status=VideoClip.Status.FAILED)
            finalize_project_render(clip.project_id)
        except (DatabaseError, VideoClip.DoesNotExist) as record_exc:
            logger.error(
                "render.failure_not_recorded clip_id=%s error=%s", clip_id, type(record_exc).__name__
            )


def _claim_clip_for_render(clip_id, redelivered=False):
    """
    Compare-and-set DRAFT/FAILED -> RENDERING. Only one delivery wins; a duplicate
    finds the clip RENDERING (or COMPLETED) and must skip. A *redelivered* message
    may also take over a clip stuck in RENDERING: its worker died (acks_late) and
    nothing else would ever finish it.
    """
    claimable = [VideoClip.Status.DRAFT, VideoClip.Status.FAILED]
    if redelivered:
        claimable.append(VideoClip.Status.RENDERING)
    return VideoClip.objects.filter(pk=clip_id, status__in=claimable).update(
        status=VideoClip.Status.RENDERING
    ) == 1


def _release_clip_claim(clip_id):
    """
    RENDERING -> DRAFT, so the retry of this task can claim the clip again. Only
    the delivery holding the claim calls it: RenderEngine leaves a failed clip
    in RENDERING, so nothing else can have claimed it in between.
    """
    VideoClip.objects.filter(pk=clip_id, status=VideoClip.Status.RENDERING).update(
        status=VideoClip.Status.DRAFT
    )


# Transient errors (R2 throttling, a dropped connection, a database blip) retry
# with the pipeline's backoff; anything else fails the clip at once.
RENDER_CLIP_MAX_RETRIES = 2


def _is_retryable_render_error(exc):
    """
    The pipeline's transient errors (``_is_retryable_pipeline_error``) minus
    its generic OSError rule: MoviePy and FFmpeg report an unreadable source or
    a failed encode as a plain OSError, and rendering again cannot fix it.
    Connection and timeout errors (OSError subclasses) still retry, and so do
    botocore's transport errors with R2 (ReadTimeoutError, SSLError... also
    subclass OSError) and a throttled or failed upload (S3UploadFailedError).
    """
    if _is_transient_storage_connection_error(exc):
        return True
    if isinstance(exc, OSError) and not isinstance(exc, (ConnectionError, TimeoutError)):
        return False
    return _is_retryable_pipeline_error(exc)


# acks_late + reject_on_worker_lost: a worker killed mid-render (OOM in MoviePy)
# gets the message redelivered instead of losing the clip. Safe because the clip
# is claimed with a CAS and a COMPLETED clip is a no-op.
@celery_app.task(name='apps.videos.tasks.render_clip_task', 
    bind=True,
    base=ClipRenderTask,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=RENDER_CLIP_MAX_RETRIES,
)
def render_clip_task(self, clip_id):
    """
    Task wrapper for the RenderEngine.render_clip method.
    Renders one clip on the dedicated `render` queue. Errors propagate (the task
    is reported as FAILURE, never as a SUCCESS string); on_failure records them.
    Idempotent: re-running a rendered clip is a no-op, and duplicate deliveries
    of the same clip render only once.

    A transient error retries with backoff: the claim is handed back (DRAFT)
    first, so the retry claims the clip like any delivery and a duplicate never
    renders it twice. A DRAFT clip keeps the project open: finalize_project_render
    waits for it. Only the last attempt reaches on_failure (clip FAILED).
    """
    logger.info(f"🎬 [RENDER TASK START] Rendering Clip {clip_id}")
    from apps.videos.services.render_engine import RenderEngine
    from apps.videos.services.storage_service import CloudflareR2Manager

    claimed = False
    try:
        clip = VideoClip.objects.get(pk=clip_id)
        if clip.status == VideoClip.Status.COMPLETED and clip.s3_object_key:
            logger.info(f"⏭️ [RENDER TASK] Clip {clip_id} already rendered, skipping")
            finalize_project_render(clip.project_id)
            return f"Clip {clip_id} already rendered"

        redelivered = bool((self.request.delivery_info or {}).get('redelivered'))
        from apps.videos.tasks.ingestion import _whole_video_render_allowed

        if not _whole_video_render_allowed(clip.project_id):
            # A whole-video project that failed (and was refunded) while this
            # message waited in the queue: the video is not rendered for free.
            stale = [VideoClip.Status.DRAFT] + ([VideoClip.Status.RENDERING] if redelivered else [])
            VideoClip.objects.filter(pk=clip_id, status__in=stale).update(status=VideoClip.Status.FAILED)
            finalize_project_render(clip.project_id)
            logger.warning("render.skipped_unpaid clip_id=%s project_id=%s", clip_id, clip.project_id)
            return f"Clip {clip_id} not rendered: the project is not payable"

        if not _claim_clip_for_render(clip_id, redelivered=redelivered):
            logger.info(f"⏭️ [RENDER TASK] Clip {clip_id} is being rendered by another delivery, skipping")
            return f"Clip {clip_id} already claimed"
        claimed = True
        # Checked again after every claim, retries included: the project may
        # have failed and been refunded between the check and the claim.
        if not _whole_video_render_allowed(clip.project_id):
            VideoClip.objects.filter(pk=clip_id, status=VideoClip.Status.RENDERING).update(
                status=VideoClip.Status.FAILED,
            )
            claimed = False
            finalize_project_render(clip.project_id)
            logger.warning("render.skipped_unpaid clip_id=%s project_id=%s", clip_id, clip.project_id)
            return f"Clip {clip_id} not rendered: the project is not payable"

        previous_key = clip.s3_object_key
        RenderEngine.render_clip(clip_id)
        claimed = False  # the clip is COMPLETED: nothing left to hand back

        new_key = VideoClip.objects.values_list('s3_object_key', flat=True).get(pk=clip_id)
        if previous_key and previous_key != new_key:
            # Re-render after a failure: do not leave the old upload orphaned in R2.
            try:
                CloudflareR2Manager.delete_object(previous_key)
            except Exception as delete_exc:
                logger.error(
                    "⚠️ [RENDER TASK] Could not delete orphan R2 object for clip %s error=%s",
                    clip_id, type(delete_exc).__name__,
                )

        finalize_project_render(clip.project_id)
        return f"Clip {clip_id} rendered successfully"
    except Exception as exc:
        if not _is_retryable_render_error(exc) or self.request.retries >= self.max_retries:
            raise  # last attempt: on_failure marks the clip FAILED
        if claimed:
            try:
                _release_clip_claim(clip_id)
            except DatabaseError as release_exc:
                # The retry could not claim a clip left in RENDERING: fail it now.
                logger.error(
                    "render.release_failed clip_id=%s error=%s", clip_id, type(release_exc).__name__,
                )
                raise exc
        logger.warning(
            "render.retrying clip_id=%s attempt=%d error=%s",
            clip_id, self.request.retries + 1, type(exc).__name__,
        )
        try:
            raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))
        except Reject:
            # The retry could not be queued (broker down): Celery would drop the
            # message without on_failure, leaving the clip DRAFT and the project
            # open forever. Failing with the original error runs on_failure.
            logger.error(
                "render.retry_not_queued clip_id=%s error=%s", clip_id, type(exc).__name__,
            )
            raise exc

def _resolve_approved_segments(project):
    """
    Segments to render: the ``approved_segments`` column, falling back to
    ``metadata['approved_segments']`` for projects approved before the column
    became the single source. Missing or malformed segments are deterministic
    failures, so they raise a non-retryable error with a static message.
    """
    raw = project.approved_segments
    if not raw:
        raw = (project.metadata or {}).get('approved_segments')
    if not raw:
        raise NonRetryableAIError(
            "No segments approved for rendering.",
            provider="render", metadata={'code': 'no_approved_segments'},
        )
    try:
        return validate_approved_segments(raw)
    except InvalidSegmentsError as exc:
        raise NonRetryableAIError(
            f"Approved segments are invalid: {exc}",
            provider="render", metadata={'code': 'invalid_approved_segments'},
        ) from None


def _settle_render_reservation(project_id, charge):
    """
    Settles the tokens reserved when the paper edit was approved (``metadata
    ['render_reservation_id']``): charged when the render is delivered,
    returned when it fails for good. Idempotent and never raises: a missing or
    already settled reservation is skipped, and a payment error is only logged
    so it never masks the render's own outcome.
    """
    from django.core.exceptions import ValidationError
    from django.db import DatabaseError

    # Deferred: wallet_service imports the tasks package at load time.
    from apps.payments.services.wallet_service import (
        PaymentError, commit_reservation, rollback_reservation,
    )

    reservation_id = None
    try:
        reservation_id = (
            VideoProject.objects.filter(pk=project_id).values_list('metadata', flat=True).first() or {}
        ).get('render_reservation_id')
        if not reservation_id or not Transaction.objects.filter(
            pk=reservation_id, status=Transaction.Status.RESERVED
        ).exists():
            logger.info("render.settle_skipped project_id=%s reason=not_reserved", project_id)
            return
        if charge:
            commit_reservation(reservation_id)
            logger.info("render.charged project_id=%s", project_id)
        else:
            rollback_reservation(reservation_id)
            logger.info("render.refunded project_id=%s", project_id)
    except PaymentError as exc:
        # Lost the race to another settle (the status is re-checked under a lock).
        if reservation_id and Transaction.objects.filter(pk=reservation_id).exclude(
            status=Transaction.Status.RESERVED
        ).exists():
            logger.info("render.settle_skipped project_id=%s reason=already_settled", project_id)
        else:
            logger.error(
                "render.settle_failed project_id=%s charge=%s error=%s",
                project_id, charge, type(exc).__name__,
            )
    except (DatabaseError, ValidationError) as exc:
        logger.error(
            "render.settle_failed project_id=%s charge=%s error=%s",
            project_id, charge, type(exc).__name__,
        )


# Starts of the paper-edit render that never finished (their worker died) and
# that may still be followed by another one. A render that always kills its
# worker (an OOM on a long 4K edit) would otherwise come back forever with its
# tokens reserved: past this many, the project fails and the render
# reservation is returned. Only starts count, never a redelivery that did not
# reach the render (a broker blip, a warm shutdown, a busy lock).
RENDER_SEGMENTS_MAX_REDELIVERIES = 2
# Custom message header with the lock-busy re-queues so far. A header, not a
# kwarg: a worker running the previous code ignores unknown headers, while an
# unknown kwarg would fail the task (TypeError) during a rolling deploy.
RENDER_LOCK_RETRIES_HEADER = 'render_lock_retries'


def _render_lock_retries(request):
    """Lock-busy re-queues so far, from the message header (0 when absent)."""
    try:
        return max(0, int((request.headers or {}).get(RENDER_LOCK_RETRIES_HEADER) or 0))
    except (TypeError, ValueError):
        return 0


def _bump_unfinished_starts(project_id, delta):
    """
    Adds ``delta`` to ``metadata['render_unfinished_starts']`` under the row
    lock and returns the value it had before: +1 when a render starts, -1 when
    that start ends (delivered or with a handled error). What is left are the
    starts whose worker died. A start also records when it happened
    (``metadata['render_started_at']``): recover_stuck_renders leaves alone a
    project that started within RENDER_STUCK_AFTER_SECONDS.
    """
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        metadata = dict(project.metadata or {})
        before = int(metadata.get('render_unfinished_starts') or 0)
        metadata['render_unfinished_starts'] = max(0, before + delta)
        if delta > 0:
            metadata['render_started_at'] = timezone.now().isoformat()
        VideoProject.objects.filter(pk=project_id).update(metadata=metadata)
    return before


def _close_start(project_id, attempt):
    """This delivery's start ended (delivered or a handled error): it is not a lost worker."""
    if not attempt.get('started'):
        return
    attempt['started'] = False
    try:
        _bump_unfinished_starts(project_id, -1)
    except DatabaseError as exc:
        # Left counted as a lost start: conservative, never blocks the outcome.
        logger.error("render.start_not_closed project_id=%s error=%s", project_id, type(exc).__name__)


def _render_delivered(project_id):
    """The paper edit was delivered: COMPLETED with its export saved."""
    return VideoProject.objects.filter(
        pk=project_id, status=VideoProject.Status.COMPLETED, final_export_r2_key__gt='',
    ).exists()


def _render_reservation_open(project):
    """The render reservation is still RESERVED, or there is none to protect."""
    from django.core.exceptions import ValidationError

    reservation_id = (project.metadata or {}).get('render_reservation_id')
    if not reservation_id:
        return True
    try:
        return Transaction.objects.filter(
            pk=reservation_id, status=Transaction.Status.RESERVED,
        ).exists()
    except ValidationError:
        # Not a transaction id: no reservation of ours can be at stake.
        return True


def _fail_render(project_id, code):
    """
    Final failure of the paper edit: FAILED (never over a delivered render),
    the error code and the render reservation returned. Race-free even without
    the lock, because a delivery is only saved over a RENDERING project: once
    FAILED, no other copy can deliver it, and one that delivered first keeps
    its charge.
    """
    VideoProject.objects.filter(pk=project_id).exclude(
        status=VideoProject.Status.COMPLETED,
    ).update(status=VideoProject.Status.FAILED)
    if _render_delivered(project_id):
        return
    record_error(project_id, code)
    _settle_render_reservation(project_id, charge=False)


def _discard_upload(project_id, key):
    """Deletes an export that will not be saved, so it is not left orphaned in R2."""
    from apps.videos.services.storage_service import CloudflareR2Manager

    try:
        CloudflareR2Manager.delete_object(key)
    except Exception as exc:
        logger.warning(
            "render.orphan_not_deleted project_id=%s error=%s", project_id, type(exc).__name__,
        )


def _render_final_edit(project_id, lock, attempt):
    """
    The paper-edit render, run while holding the project lock. Raises on
    error; render_video_segments handles it under the same lock.
    ``attempt['started']`` tells the handler that this delivery counted a start.
    """
    from apps.videos.services.storage_service import CloudflareR2Manager
    from apps.videos.services.render_engine import RenderEngine
    from moviepy import VideoFileClip, concatenate_videoclips
    import tempfile

    if _render_delivered(project_id):
        # Redelivered after the export was saved: never render or charge again.
        # A worker lost between saving and charging is charged here, once.
        logger.info("render.already_delivered project_id=%s", project_id)
        _settle_render_reservation(project_id, charge=True)
        return f"Render already delivered for {project_id}"

    project = VideoProject.objects.get(pk=project_id)
    if project.status != VideoProject.Status.RENDERING:
        # A copy of a render that already failed for good: rendering it now
        # would deliver it for free.
        logger.warning("render.skipped project_id=%s reason=not_rendering", project_id)
        return f"Skipped {project_id}: not rendering"
    if not _render_reservation_open(project):
        logger.warning("render.skipped project_id=%s reason=reservation_settled", project_id)
        return f"Skipped {project_id}: reservation settled"

    lost_starts = _bump_unfinished_starts(project_id, +1)
    attempt['started'] = True
    if lost_starts:
        logger.warning(
            "render.restarted project_id=%s lost_starts=%d", project_id, lost_starts,
        )
    if lost_starts > RENDER_SEGMENTS_MAX_REDELIVERIES:
        raise NonRetryableAIError(
            "The render lost its worker too many times.",
            provider="render", metadata={'code': 'render_worker_lost'},
        )

    output_path = None
    final_s3_key = None
    persisted = 0
    try:
        approved_segments = _resolve_approved_segments(project)

        # 1. DOWNLOAD ORIGINAL FROM R2 (Temporary)
        r2_key = project.original_r2_key
        if not r2_key: raise Exception("No original_r2_key found.")

        # The source copy lives in a TemporaryDirectory so it is removed on every
        # exit path, not only on success (Bug 4).
        with tempfile.TemporaryDirectory() as work_dir:
            local_high_res_path = os.path.join(work_dir, 'source.mp4')
            CloudflareR2Manager.get_client().download_file(
                settings.CLOUDFLARE_R2_BUCKET_NAME,
                r2_key,
                local_high_res_path
            )

            # 2. NON-LINEAR EDITING (Cuts)
            with VideoFileClip(local_high_res_path) as original_clip:
                subclips = []
                for seg in approved_segments:
                    start = float(seg['start'])
                    end = float(seg['end'])
                    # MoviePy 2.0+ subclipped method
                    subclips.append(original_clip.subclipped(start, end))

                # Concatenate all approved parts
                edited_clip = concatenate_videoclips(subclips)
            
                # 3. APPLY LAYOUT & SUBTITLES (Reuse RenderEngine Logic)
                target_w, target_h = RenderEngine._get_target_resolution(
                    project.aspect_ratio, (project.metadata or {}).get('render_resolution'),
                )
                use_ft = project.use_facetracking
                gp_pos = project.gameplay_position
            
                from apps.videos.services.layouts import get_layout_strategy
                layout_strategy = get_layout_strategy(
                    project.render_layout, target_w, target_h, use_facetracking=use_ft,
                    gameplay_pos=gp_pos, camera_selection_pos=project.camera_selection_position,
                    manual_camera_coords={
                        'x': project.manual_camera_x, 'y': project.manual_camera_y,
                        'zoom': project.manual_camera_zoom,
                    },
                    editing_style=project.editing_style,
                )
                video_layout_processed = layout_strategy.apply(edited_clip)

                # Burn Subtitles if requested
                if project.add_subtitles:
                    from apps.videos.services.subtitle_engine import SubtitleEngine, StyleConfig
                    # Mapping settings
                    size_map = {"small": 0.04, "medium": 0.06, "large": 0.09}
                    pos_map = {"top": 0.20, "center": 0.50, "bottom": 0.85}
                
                    sub_config = StyleConfig(
                        font_path='Montserrat-Bold.ttf',
                        font_size_percent=size_map.get(project.subtitle_size, 0.06),
                        primary_color=project.subtitle_color,
                        y_position_percent=pos_map.get(project.subtitle_position, 0.85)
                    )
                
                    subtitler = SubtitleEngine(style_config=sub_config)
                
                    # IMPORTANT: In Paper Edit, the transcription timings must be RE-CALCULATED
                    # because the final video is shorter. For simplicity, we use the original text
                    # from the segments provided by the user.
                    final_segments = []
                    curr_t = 0.0
                    for seg in approved_segments:
                        dur = float(seg['end']) - float(seg['start'])
                        final_segments.append({"text": seg.get('text', ''), "start": curr_t, "end": curr_t + dur})
                        curr_t += dur
                
                    final_clip = subtitler.add_subtitles(video_layout_processed, final_segments)
                else:
                    final_clip = video_layout_processed

                # 4. EXPORT & S3 UPLOAD
                output_filename = f"final_{project.id}.mp4"
                output_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'final')
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, output_filename)

                final_clip.write_videofile(
                    output_path,
                    codec='libx264',
                    audio_codec='aac',
                    fps=24,
                    preset='slow', # High quality for final
                    threads=4,
                    logger=None
                )

                user_id = str(project.uploaded_by.id) if project.uploaded_by else "system"
                final_s3_key = CloudflareR2Manager.upload_video(output_path, user_id, f"{project.id}/final")

                # 5. FINAL PERSISTENCE, only while this delivery still owns the
                # project: the export and the clip row that shows it are saved
                # together, over a project that is still RENDERING with no
                # export, so a copy that lost the lock (or a project failed
                # meanwhile) never delivers twice or for free.
                if lock.lost.is_set():
                    _discard_upload(project_id, final_s3_key)
                    raise ProjectLockLostError(f"Lock for project {project_id} was lost before saving")
                with transaction.atomic():
                    persisted = VideoProject.objects.filter(
                        pk=project_id, status=VideoProject.Status.RENDERING,
                    ).filter(
                        Q(final_export_r2_key__isnull=True) | Q(final_export_r2_key=''),
                    ).update(
                        final_export_r2_key=final_s3_key, status=VideoProject.Status.COMPLETED,
                    )
                    if persisted == 1:
                        VideoClip.objects.create(
                            project=project,
                            title="VIDEO FINAL EDITADO",
                            start_time=0,
                            end_time=edited_clip.duration,
                            s3_object_key=final_s3_key,
                            status=VideoClip.Status.COMPLETED
                        )
    finally:
        # The rendered output is written under MEDIA_ROOT, outside the temporary
        # directory, so it is removed explicitly on every exit path.
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError as cleanup_exc:
                logger.warning(
                    "render.cleanup_failed project_id=%s error=%s",
                    project_id, type(cleanup_exc).__name__,
                )

    # Charged only once MoviePy and the temporary files are closed: an error
    # while closing them reaches the handler, which sees the delivery and charges.
    _close_start(project_id, attempt)
    if persisted == 1:
        logger.info(f"✅ [FINAL RENDER] Success for project {project_id}")
        _settle_render_reservation(project_id, charge=True)
        return f"Render Success: {final_s3_key}"
    _discard_upload(project_id, final_s3_key)
    if _render_delivered(project_id):
        logger.info("render.already_delivered project_id=%s", project_id)
        _settle_render_reservation(project_id, charge=True)
        return f"Render already delivered for {project_id}"
    logger.warning("render.not_persisted project_id=%s", project_id)
    return f"Render not saved for {project_id}: the project is no longer rendering"


def _handle_render_error(task, project_id, exc, attempt):
    """
    What a failed paper-edit delivery does, under the project lock when it has
    it: deliver (the export was already saved), retry, or fail for good. The
    project stays RENDERING while a retry is pending; only the final failure
    marks it FAILED and returns the reservation.
    """
    _close_start(project_id, attempt)
    if isinstance(exc, NonRetryableAIError):
        # Deterministic failure (bad or missing segments, a worker lost too
        # many times): retrying cannot fix it.
        logger.error(f"❌ [FINAL RENDER ERROR] project {project_id}: {exc.message}")
        _fail_render(project_id, exc.metadata.get('code', 'render_failed'))
        raise exc
    logger.error(
        "❌ [FINAL RENDER ERROR] project_id=%s error=%s", project_id, type(exc).__name__
    )
    if _render_delivered(project_id):
        # Failed after the export was saved (e.g. closing MoviePy): the
        # render was delivered, so it is charged and never retried.
        _settle_render_reservation(project_id, charge=True)
        return f"Render delivered for {project_id}"
    if not is_task_time_limit(exc) and task.request.retries < task.max_retries:
        try:
            # A retry is a new delivery of the render: a fresh lock budget
            # (the header is dropped) and the project stays RENDERING.
            raise task.retry(exc=exc, countdown=60, headers={})
        except Reject:
            # Not queued (broker down): the message would be dropped with the
            # project RENDERING and its tokens reserved. Fail it for good.
            logger.error(
                "render.retry_not_queued project_id=%s error=%s", project_id, type(exc).__name__,
            )
    if isinstance(exc, ProjectLockLostError):
        # The last attempt lost its lock: fail it anyway, or the project would
        # stay RENDERING with its tokens reserved. Safe even if another copy
        # owns it now: _fail_render never touches a COMPLETED project, and a
        # delivery is only saved over a RENDERING one, so that copy cannot
        # deliver after this.
        logger.warning("render.lock_lost project_id=%s", project_id)
    # Out of retries, or the task's time limit: nothing was delivered.
    _fail_render(project_id, type(exc).__name__)
    raise exc


# acks_late + reject_on_worker_lost, like the ingestion: a worker killed
# mid-render (OOM in MoviePy) gets the message back instead of leaving the
# project RENDERING with its tokens reserved. Safe on redelivery because:
# - the project lock (renewed by a heartbeat) keeps a second copy off a render
#   that is still running: a message can come back while its worker is alive
#   (it waited for its countdown or in the worker's prefetch, so the time
#   limit alone does not keep it under the broker's visibility timeout);
# - a copy only renders a project that is RENDERING with its reservation
#   still RESERVED, and only saves over a project that is still RENDERING
#   with no export; a delivered project is only settled (_render_final_edit);
# - starts that never finished are capped (RENDER_SEGMENTS_MAX_REDELIVERIES),
#   so a render that always kills its worker fails and is refunded.
# The soft time limit (RENDER_SEGMENTS_SOFT_TIME_LIMIT) is a final failure with
# refund; both limits stay below CELERY_VISIBILITY_TIMEOUT. A worker killed by
# the hard limit gets no redelivery (Celery acks it), and neither does a lost
# redelivery: recover_stuck_renders (render_recovery.py) re-queues or fails
# those projects.
@celery_app.task(name='apps.videos.tasks.render_video_segments',
    bind=True,
    max_retries=2,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=getattr(settings, 'RENDER_SEGMENTS_SOFT_TIME_LIMIT', 3 * 3600),
    time_limit=getattr(settings, 'RENDER_SEGMENTS_TIME_LIMIT', 3 * 3600 + 300),
)
def render_video_segments(self, project_id):
    """
    Task: High-Quality Non-Linear Rendering (Paper Edit Phase 2)
    1. Download Original from S3.
    2. Extract and concatenate approved segments (Float precision).
    3. Apply Layout & Subtitles.
    4. S3 Upload & Final Cleanup.

    A busy project lock re-queues the task with its own budget, carried in
    the ``render_lock_retries`` message header (RENDER_LOCK_RETRIES_HEADER).
    """
    logger.info(f"🎬 [FINAL RENDER] Starting for project {project_id}")
    lock_retries = _render_lock_retries(self.request)
    attempt = {'started': False}
    in_lock = False
    try:
        with project_lock(project_id) as lock:
            in_lock = True
            try:
                return _render_final_edit(project_id, lock, attempt)
            except Exception as exc:
                return _handle_render_error(self, project_id, exc, attempt)
    except ProjectLockedError:
        if lock_retries < PIPELINE_LOCK_MAX_RETRIES:
            logger.warning(
                "render.lock_busy_retrying project_id=%s lock_retry=%d", project_id, lock_retries + 1,
            )
            # Not published (broker down): this message goes back to the
            # broker instead of being acked away (_publish_requeue).
            _publish_requeue(
                self,
                lambda: render_video_segments.apply_async(
                    args=[str(project_id)],
                    headers={RENDER_LOCK_RETRIES_HEADER: lock_retries + 1},
                    countdown=PIPELINE_LOCK_RETRY_SECONDS,
                    retries=self.request.retries,
                ),
                "render.requeue_not_published", project_id, "lock",
            )
            return f"Lock busy, re-queued {project_id}"
        # Still held after outlasting the TTL: a live worker is rendering it.
        logger.warning("render.skipped project_id=%s reason=lock_busy", project_id)
        return f"Skipped {project_id}: lock busy"
    except Exception as exc:
        if in_lock:
            raise  # already handled under the lock (a retry, or the final error)
        # The lock could not be taken (e.g. Redis down): nothing ran yet.
        return _handle_render_error(self, project_id, exc, attempt)


# --- Clip previews (PTE-03) ---------------------------------------------------

@celery_app.task(name='apps.videos.tasks.render_clip_preview_task', ignore_result=True)
def render_clip_preview_task(preview_id):
    """
    Renders a free 360p preview of a clip (PTE-03). Never retried: a failed
    preview is stored as ``failed`` and the user can ask for a new one.
    """
    from apps.videos.services.clip_preview import render_preview

    render_preview(preview_id)


@celery_app.task(name='apps.videos.tasks.cleanup_expired_previews', ignore_result=True)
def cleanup_expired_previews():
    """Deletes expired previews and their files (CELERY_BEAT_SCHEDULE, every 15 min)."""
    from apps.videos.services.clip_preview import cleanup_expired_previews_now

    return cleanup_expired_previews_now()
