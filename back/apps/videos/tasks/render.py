"""
Render tasks: one clip per task, the paper-edit render and the fan-out close.
"""

import json
import os
import uuid
import logging
from celery import shared_task
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
    """Marks the clip (and possibly the project) as failed when a render task fails for good."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        clip_id = args[0] if args else kwargs.get('clip_id')
        try:
            clip = VideoClip.objects.get(pk=clip_id)
            VideoClip.objects.filter(pk=clip_id).update(status=VideoClip.Status.FAILED)
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


# acks_late + reject_on_worker_lost: a worker killed mid-render (OOM in MoviePy)
# gets the message redelivered instead of losing the clip. Safe because the clip
# is claimed with a CAS and a COMPLETED clip is a no-op.
@celery_app.task(name='apps.videos.tasks.render_clip_task', 
    bind=True,
    base=ClipRenderTask,
    acks_late=True,
    reject_on_worker_lost=True,
)
def render_clip_task(self, clip_id):
    """
    Task wrapper for the RenderEngine.render_clip method.
    Renders one clip on the dedicated `render` queue. Errors propagate (the task
    is reported as FAILURE, never as a SUCCESS string); on_failure records them.
    Idempotent: re-running a rendered clip is a no-op, and duplicate deliveries
    of the same clip render only once.
    """
    logger.info(f"🎬 [RENDER TASK START] Rendering Clip {clip_id}")
    from apps.videos.services.render_engine import RenderEngine
    from apps.videos.services.storage_service import CloudflareR2Manager

    clip = VideoClip.objects.get(pk=clip_id)
    if clip.status == VideoClip.Status.COMPLETED and clip.s3_object_key:
        logger.info(f"⏭️ [RENDER TASK] Clip {clip_id} already rendered, skipping")
        finalize_project_render(clip.project_id)
        return f"Clip {clip_id} already rendered"

    redelivered = bool((self.request.delivery_info or {}).get('redelivered'))
    if not _claim_clip_for_render(clip_id, redelivered=redelivered):
        logger.info(f"⏭️ [RENDER TASK] Clip {clip_id} is being rendered by another delivery, skipping")
        return f"Clip {clip_id} already claimed"

    previous_key = clip.s3_object_key
    RenderEngine.render_clip(clip_id)

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


@celery_app.task(name='apps.videos.tasks.render_video_segments', bind=True, max_retries=2)
def render_video_segments(self, project_id):
    """
    Task: High-Quality Non-Linear Rendering (Paper Edit Phase 2)
    1. Download Original from S3.
    2. Extract and concatenate approved segments (Float precision).
    3. Apply Layout & Subtitles.
    4. S3 Upload & Final Cleanup.
    """
    logger.info(f"🎬 [FINAL RENDER] Starting for project {project_id}")
    from apps.videos.services.storage_service import CloudflareR2Manager
    from apps.videos.services.render_engine import RenderEngine
    from moviepy import VideoFileClip, concatenate_videoclips
    import tempfile

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

                # 5. FINAL PERSISTENCE
                project.final_export_r2_key = final_s3_key
                project.status = VideoProject.Status.COMPLETED
                project.save()

                # We create a final VideoClip record to represent the full edited video
                final_clip_obj = VideoClip.objects.create(
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

    except NonRetryableAIError as e:
        # Deterministic failure (bad or missing segments): retrying cannot fix it.
        logger.error(f"❌ [FINAL RENDER ERROR] project {project_id}: {e.message}")
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.FAILED)
        record_error(project_id, e.metadata.get('code', 'render_failed'))
        _settle_render_reservation(project_id, charge=False)
        raise
    except Exception as e:
        logger.error(
            "❌ [FINAL RENDER ERROR] project_id=%s error=%s", project_id, type(e).__name__
        )
        if 'project' in locals():
            project.status = VideoProject.Status.FAILED
            project.save()
        if self.request.retries >= self.max_retries:
            # Out of retries: nothing was delivered, so the render is not charged.
            _settle_render_reservation(project_id, charge=False)
            raise
        raise self.retry(exc=e, countdown=60)
    finally:
        # The rendered output is written under MEDIA_ROOT, outside the temporary
        # directory, so it is removed explicitly on every exit path.
        if output_path and os.path.exists(output_path):
            os.remove(output_path)


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
