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
    _retry_countdown,
)


logger = logging.getLogger(__name__)

def finalize_project_render(project_id):
    """
    Closes the render fan-out once every clip reached a final state: all rendered
    -> COMPLETED, any failed -> PARTIAL (never COMPLETED with failures). Safe to
    call after every clip: counting happens under the project row lock, so only
    the call that sees the last clip finish transitions the project.
    """
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
        if not _claim_clip_for_render(clip_id, redelivered=redelivered):
            logger.info(f"⏭️ [RENDER TASK] Clip {clip_id} is being rendered by another delivery, skipping")
            return f"Clip {clip_id} already claimed"
        claimed = True

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


# Redeliveries after a lost worker that may start the paper-edit render again.
# A render that always kills its worker (an OOM on a long 4K edit) would
# otherwise come back forever with its tokens reserved: past this many, the
# project fails and the render reservation is returned.
RENDER_SEGMENTS_MAX_REDELIVERIES = 2


def _count_render_redelivery(project_id):
    """Adds one to ``metadata['render_redeliveries']`` under the row lock and returns it."""
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        metadata = dict(project.metadata or {})
        count = int(metadata.get('render_redeliveries') or 0) + 1
        metadata['render_redeliveries'] = count
        VideoProject.objects.filter(pk=project_id).update(metadata=metadata)
    return count


def _render_delivered(project_id):
    """The paper edit was delivered: COMPLETED with its export saved."""
    return VideoProject.objects.filter(
        pk=project_id, status=VideoProject.Status.COMPLETED, final_export_r2_key__gt='',
    ).exists()


def _render_final_edit(project_id, redelivered):
    """
    The paper-edit render, run while holding the project lock. Raises on
    error; render_video_segments decides between retrying and failing.
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

    if redelivered:
        redeliveries = _count_render_redelivery(project_id)
        logger.warning(
            "render.redelivered project_id=%s redeliveries=%d", project_id, redeliveries,
        )
        if redeliveries > RENDER_SEGMENTS_MAX_REDELIVERIES:
            raise NonRetryableAIError(
                "The render lost its worker too many times.",
                provider="render", metadata={'code': 'render_worker_lost'},
            )

    output_path = None
    try:
        project = VideoProject.objects.get(id=project_id)
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
                    gameplay_pos=gp_pos, editing_style=project.editing_style,
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

                # 5. FINAL PERSISTENCE: the export and the clip row that shows it
                # are saved together, so a redelivery that finds the project
                # delivered (and skips it) leaves nothing half done.
                with transaction.atomic():
                    VideoProject.objects.filter(pk=project_id).update(
                        final_export_r2_key=final_s3_key, status=VideoProject.Status.COMPLETED,
                    )
                    VideoClip.objects.create(
                        project=project,
                        title="VIDEO FINAL EDITADO",
                        start_time=0,
                        end_time=edited_clip.duration,
                        s3_object_key=final_s3_key,
                        status=VideoClip.Status.COMPLETED
                    )

                logger.info(f"✅ [FINAL RENDER] Success for project {project_id}")
                _settle_render_reservation(project_id, charge=True)
                return f"Render Success: {final_s3_key}"
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


# acks_late + reject_on_worker_lost, like the ingestion: a worker killed
# mid-render (OOM in MoviePy) gets the message back instead of leaving the
# project RENDERING with its tokens reserved. Safe on redelivery because:
# - the project lock (renewed by a heartbeat) keeps a second delivery off a
#   render that is still running. The render has no time limit, so it can
#   outlast the broker's visibility timeout and be redelivered while alive;
# - a project already COMPLETED with final_export_r2_key is not rendered or
#   charged again (_render_final_edit);
# - redeliveries are capped (RENDER_SEGMENTS_MAX_REDELIVERIES), so a render
#   that always kills its worker fails and is refunded.
@celery_app.task(name='apps.videos.tasks.render_video_segments',
    bind=True,
    max_retries=2,
    acks_late=True,
    reject_on_worker_lost=True,
)
def render_video_segments(self, project_id, lock_retries=0, redelivered=False):
    """
    Task: High-Quality Non-Linear Rendering (Paper Edit Phase 2)
    1. Download Original from S3.
    2. Extract and concatenate approved segments (Float precision).
    3. Apply Layout & Subtitles.
    4. S3 Upload & Final Cleanup.

    ``lock_retries`` counts the re-queues while the project lock is busy (its
    own budget, like the ingestion's). ``redelivered`` keeps the broker's flag
    across those re-queues: a dead worker's lock lasts until its TTL, so the
    redelivery that found it must still count as one.
    """
    logger.info(f"🎬 [FINAL RENDER] Starting for project {project_id}")
    redelivered = redelivered or bool((self.request.delivery_info or {}).get('redelivered'))
    try:
        with project_lock(project_id):
            return _render_final_edit(project_id, redelivered)
    except ProjectLockedError:
        if lock_retries < PIPELINE_LOCK_MAX_RETRIES:
            logger.warning(
                "render.lock_busy_retrying project_id=%s lock_retry=%d", project_id, lock_retries + 1,
            )
            render_video_segments.apply_async(
                args=[str(project_id)],
                kwargs={'lock_retries': lock_retries + 1, 'redelivered': redelivered},
                countdown=PIPELINE_LOCK_RETRY_SECONDS,
                retries=self.request.retries,
            )
            return f"Lock busy, re-queued {project_id}"
        # Still held after outlasting the TTL: a live worker is rendering it.
        logger.warning("render.skipped project_id=%s reason=lock_busy", project_id)
        return f"Skipped {project_id}: lock busy"
    except NonRetryableAIError as e:
        # Deterministic failure (bad or missing segments, a worker lost too
        # many times): retrying cannot fix it.
        logger.error(f"❌ [FINAL RENDER ERROR] project {project_id}: {e.message}")
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.FAILED)
        record_error(project_id, e.metadata.get('code', 'render_failed'))
        _settle_render_reservation(project_id, charge=False)
        raise
    except Exception as e:
        logger.error(
            "❌ [FINAL RENDER ERROR] project_id=%s error=%s", project_id, type(e).__name__
        )
        if _render_delivered(project_id):
            # Failed after the export was saved (e.g. closing MoviePy): the
            # render was delivered, so it is charged and never retried.
            _settle_render_reservation(project_id, charge=True)
            return f"Render delivered for {project_id}"
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.FAILED)
        if self.request.retries >= self.max_retries:
            # Out of retries: nothing was delivered, so the render is not charged.
            _settle_render_reservation(project_id, charge=False)
            raise
        # A retry is a new delivery of the render, not a redelivery after a
        # lost worker: it starts with a fresh lock budget and no redelivery flag.
        raise self.retry(exc=e, countdown=60, kwargs={})


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
