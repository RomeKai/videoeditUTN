import os
import uuid
import logging
from celery import shared_task
from django.conf import settings
from django.core.files import File

# Importamos Modelos
from apps.videos.models import VideoProject, VideoClip
from apps.payments.models import Transaction
from moviepy import VideoFileClip, concatenate_videoclips

# Importamos Servicios (SelectionEngine suele ser seguro, TranscriptionEngine lo cargaremos lazy por si acaso)
from apps.videos.services.selection_engine import SelectionEngine
from apps.videos.services.ai.errors import NonRetryableAIError, is_retryable_error
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
from apps.videos.services.ai.project_lock import (
    ProjectLockedError,
    ProjectLockLostError,
    project_lock,
)

logger = logging.getLogger(__name__)

def download_from_youtube(url, output_folder):
    import yt_dlp
    
    filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(output_folder, filename)
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.google.com/',
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    return output_path

import time
import random
from contextlib import contextmanager
from datetime import timedelta
from django.utils import timezone
from django.db import DatabaseError, transaction
from django.db.models import F
from django.core.cache import cache
from apps.videos.models import VideoProject, VideoClip, ScheduledPost

from backend.celery import app as celery_app

logger = logging.getLogger(__name__)

class ThirdPartyAPIError(Exception):
    """Exception raised when a social media API fails."""
    pass

class PostConfigurationError(Exception):
    """Raised when a post cannot be published because the workspace is misconfigured."""
    pass

@celery_app.task
def dispatch_scheduled_posts_batch():
    """
    Orchestrator: Dispatches posts scheduled for the next hour.
    Runs every hour via Celery Beat.
    """
    now = timezone.now()
    window_end = now + timedelta(hours=1)
    
    # Batch query for scheduled posts in the next hour
    scheduled_posts = ScheduledPost.objects.filter(
        status=ScheduledPost.Status.SCHEDULED,
        publish_at__range=(now, window_end)
    )
    
    count = 0
    for post in scheduled_posts:
        with transaction.atomic():
            # Lock the row for update and check status again to prevent race conditions
            post_to_queue = ScheduledPost.objects.select_for_update().get(id=post.id)
            if post_to_queue.status == ScheduledPost.Status.SCHEDULED:
                post_to_queue.status = ScheduledPost.Status.QUEUED
                post_to_queue.save()
                
                # Send to worker with specific ETA
                upload_to_social_network.apply_async(
                    args=[post_to_queue.id],
                    eta=post_to_queue.publish_at
                )
                count += 1
    
    logger.info(f"🚀 [DISPATCHER] Queued {count} posts for distribution.")
    return f"Queued {count} posts."

@celery_app.task(bind=True, max_retries=3)
def upload_to_social_network(self, post_id):
    """
    Worker: Uploads a video clip via Ayrshare API.
    Lifecycle:
    1. Lock verification (Redis).
    2. S3 Presigned URL generation (Short-lived).
    3. Ayrshare delegation with User Profile Key.
    4. Exponential Backoff on failure.
    """
    from apps.integrations.ayrshare_api import AyrshareClient, AyrshareAPIError
    from apps.videos.services.storage_service import CloudflareR2Manager

    lock_id = f"lock_post_publish_{post_id}"
    if not cache.add(lock_id, "locked", 300):
        logger.warning(f"🔒 [WORKER] Aborting post {post_id}: Already running.")
        return "Locked"

    try:
        with transaction.atomic():
            post = ScheduledPost.objects.select_for_update().get(id=post_id)
            if post.status == ScheduledPost.Status.PUBLISHED:
                return "Already Published"

            # Validation: Does the workspace have an Ayrshare Key?
            workspace = post.video_clip.project.workspace
            profile_key = workspace.ayrshare_profile_key

            if not profile_key:
                logger.error(f"❌ [WORKER] Post {post_id} FAILED: No ayrshare_profile_key for workspace {workspace.id}")
                post.status = ScheduledPost.Status.FAILED
                post.error_log = "Error: Ayrshare Profile Key not configured in workspace."
                post.save()
                # Committed on exit of the atomic block; raised after so the task is not reported as SUCCESS.
                missing_key = True
            else:
                missing_key = False
                post.status = ScheduledPost.Status.PROCESSING
                post.save()

        if missing_key:
            raise PostConfigurationError(f"No ayrshare_profile_key for workspace {workspace.id}")

        # 1. Generate S3 Presigned URL (Valid for 1 hour)
        # This URL is what Ayrshare will use to download and re-upload the video.
        s3_key = post.video_clip.s3_object_key
        if not s3_key:
            raise Exception("No s3_object_key found for the video clip.")

        media_url = CloudflareR2Manager.generate_presigned_url(s3_key, expiration_seconds=3600)
        
        # 2. Call Ayrshare API
        platform_map = {
            'TIKTOK': 'tiktok',
            'INSTAGRAM_REELS': 'instagram',
            'YOUTUBE_SHORTS': 'youtube'
        }
        target_platform = platform_map.get(post.platform, 'tiktok')
        
        # Priority: 1. AI Generated Caption, 2. Project Metadata, 3. Default
        caption = post.generated_caption or post.video_clip.project.metadata.get('caption')
        if not caption:
            caption = f"Check this out! #Viral #{target_platform}"

        response = AyrshareClient.send_post(
            profile_key=profile_key,
            s3_media_url=media_url,
            caption=caption,
            platforms=[target_platform]
        )

        # 3. Success state
        post.status = ScheduledPost.Status.PUBLISHED
        post.error_log = f"Ayrshare ID: {response.get('id')}"
        post.save()
        logger.info(f"✅ [WORKER] Post {post_id} published via Ayrshare.")
        
        return f"Published via Ayrshare: {target_platform}"

    except AyrshareAPIError as exc:
        # Resilience: Exponential Backoff for 3rd party instability
        logger.error(f"⚠️ [WORKER] Ayrshare API Error: {exc}")
        # Updated by id: `post` may not be bound if the failure happened before the fetch.
        ScheduledPost.objects.filter(id=post_id).update(
            retry_count=F('retry_count') + 1,
            error_log=f"Retry {self.request.retries + 1}: {str(exc)}",
        )

        # Backoff: 60s, 120s, 240s... (TD-04: replaces the fixed 60s countdown)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

    except PostConfigurationError:
        # Already persisted as FAILED above; deterministic, so never retried.
        raise

    except Exception as e:
        logger.error(f"❌ [WORKER] Critical failure: {e}")
        ScheduledPost.objects.filter(id=post_id).update(
            status=ScheduledPost.Status.FAILED,
            error_log=f"Critical: {str(e)}",
        )
        # Re-raised so Celery records FAILURE instead of reporting a silent SUCCESS.
        raise

    finally:
        cache.delete(lock_id)

@celery_app.task(bind=True, max_retries=3)
def process_video_seo(self, post_id):
    """
    Task: Generates viral SEO metadata for a scheduled post.
    1. Security Shield check.
    2. Extracts transcript.
    3. Calls SEOOptimizationService.
    """
    from apps.core.security import AI_Security_Shield, UnsafeContentError
    from apps.videos.services.seo_engine import SEOOptimizationService
    
    try:
        post = ScheduledPost.objects.select_related('video_clip__project').get(id=post_id)
        project = post.video_clip.project
        
        transcript = project.metadata.get('full_text', '')
        if not transcript:
            segments = project.metadata.get('transcription', [])
            transcript = " ".join([s.get('text', '') for s in segments])

        if not transcript:
            logger.warning(f"⚠️ [SEO] No transcript found for post {post_id}. Aborting.")
            return "No Transcript"

        # --- REQUERIMIENTO: SECURITY SHIELD (MODERATION) ---
        # First line of defense
        try:
            AI_Security_Shield.check_content_safety(transcript)
        except UnsafeContentError as safety_exc:
            logger.error(f"🛡️ [SEO SECURITY] Safety violation for post {post_id}: {safety_exc}")
            post.status = ScheduledPost.Status.FAILED
            post.error_log = f"Violación de seguridad: {str(safety_exc)}"
            post.save()
            return "Security Abort"

        # 1. Generate Metadata via AI
        seo_service = SEOOptimizationService()
        metadata = seo_service.generate_metadata(
            transcript_text=transcript,
            target_niche=project.metadata.get('niche', 'general')
        )

        # 2. Format Caption (Body + Hashtags)
        formatted_hashtags = " ".join([f"#{h.strip()}" for h in metadata.hashtags])
        final_caption = f"{metadata.description_body}\n\n.\n.\n{formatted_hashtags}"

        # 3. Save to ScheduledPost
        post.generated_caption = final_caption
        post.generated_hashtags = metadata.hashtags
        
        # Save additional tweaks in error_log or a dedicated field if exists
        # For now, let's keep it in the main caption
        post.save()

        logger.info(f"✅ [SEO] Metadata generated for post {post_id}")
        return f"SEO Success: {metadata.viral_title}"

    except Exception as e:
        logger.error(f"❌ [SEO] Failed for post {post_id}: {e}")
        # Retry for OpenAI timeouts or network issues
        raise self.retry(exc=e, countdown=60)

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
        except (DatabaseError, VideoClip.DoesNotExist):
            logger.exception("render.failure_not_recorded clip_id=%s", clip_id)


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
@celery_app.task(
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
        except Exception:
            logger.exception(f"⚠️ [RENDER TASK] Could not delete orphan R2 object {previous_key}")

    finalize_project_render(clip.project_id)
    return f"Clip {clip_id} rendered successfully"

@celery_app.task(bind=True, max_retries=2)
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

    try:
        project = VideoProject.objects.get(id=project_id)
        approved_segments = project.approved_segments
        
        if not approved_segments:
            raise Exception("No segments approved for rendering.")

        # 1. DOWNLOAD ORIGINAL FROM R2 (Temporary)
        r2_key = project.original_r2_key
        if not r2_key: raise Exception("No original_r2_key found.")
        
        # Use tempfile to ensure cleanup
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_high_res:
            CloudflareR2Manager.get_client().download_file(
                settings.CLOUDFLARE_R2_BUCKET_NAME, 
                r2_key, 
                tmp_high_res.name
            )
            local_high_res_path = tmp_high_res.name

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
            target_w, target_h = RenderEngine._get_target_resolution(project.aspect_ratio)
            use_ft = project.use_facetracking
            gp_pos = project.gameplay_position
            
            from apps.videos.services.layouts import get_layout_strategy
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h, use_facetracking=use_ft, gameplay_pos=gp_pos)
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

            # Clean up local output
            if os.path.exists(output_path): os.remove(output_path)
            if os.path.exists(local_high_res_path): os.remove(local_high_res_path)

            logger.info(f"✅ [FINAL RENDER] Success for project {project_id}")
            return f"Render Success: {final_s3_key}"

    except Exception as e:
        logger.error(f"❌ [FINAL RENDER ERROR] {e}", exc_info=True)
        if 'project' in locals():
            project.status = VideoProject.Status.FAILED
            project.save()
        raise self.retry(exc=e, countdown=60)

# --- INGESTION PIPELINE (AICORE-7: recoverable, idempotent) ---
#
# Stages (see services/ai/pipeline_state.py):
#   UPLOADED -> AUDIO_EXTRACTED (source in R2 + proxy + probe)
#            -> TRANSCRIBED     (transcript persisted)
#            -> CLIPS_SELECTED  (clips persisted)
#            -> RENDER_DISPATCHED (auto_render_bypass only) -> COMPLETED | PARTIAL
# Each stage persists its output BEFORE advancing, so a retry resumes at the
# failed stage and never repeats paid AI calls. No DB transaction is ever open
# while calling R2, FFmpeg, MoviePy, Groq or the LLM.

PIPELINE_MAX_RETRIES = 5
PIPELINE_RETRY_BASE_SECONDS = 30
PIPELINE_RETRY_CAP_SECONDS = 600
# A redelivered task that finds the project lock held either collided with a live
# worker or with the leftover lock of a dead one. Retrying shortly, for longer
# than the lock TTL (project_lock.DEFAULT_LOCK_TTL_SECONDS), lets the dead
# worker's lock expire instead of dropping the job.
PIPELINE_LOCK_RETRY_SECONDS = 45
PIPELINE_LOCK_MAX_RETRIES = 8

_CLIP_SUGGESTION_FIELDS = {'start', 'end', 'title', 'virality_score', 'reasoning'}


def _retry_countdown(retries):
    """Exponential backoff (30s, 60s, 120s... capped) with equal jitter to avoid retry storms."""
    ceiling = min(PIPELINE_RETRY_BASE_SECONDS * (2 ** retries), PIPELINE_RETRY_CAP_SECONDS)
    return ceiling / 2 + random.uniform(0, ceiling / 2)


# Keys that describe the source/transcript: once written they are authoritative
# and a slower or duplicate worker must not replace them.
_WRITE_ONCE_METADATA_KEYS = frozenset({'duration', 'resolution', 'full_text'})


def _merge_metadata(project_id, updates, expected_stage=None):
    """
    Merges ``updates`` into ``VideoProject.metadata`` without clobbering other keys
    (e.g. max_clips, subtitle_size). Short read-modify-write under a row lock.
    Write-once keys (duration, resolution, full_text) are kept if already present.
    With ``expected_stage`` the merge only happens while the project is still at
    that stage; otherwise ``PipelineConflictError`` (the caller lost ownership).
    """
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        if expected_stage is not None and project.pipeline_stage != expected_stage:
            raise PipelineConflictError(
                f"Project {project_id} is no longer at stage {expected_stage}"
            )
        existing = project.metadata or {}
        accepted = {
            key: value for key, value in updates.items()
            if not (key in _WRITE_ONCE_METADATA_KEYS and key in existing)
        }
        merged = {**existing, **accepted}
        VideoProject.objects.filter(pk=project_id).update(metadata=merged)


@contextmanager
def _running_stage(project_id, stage, attempt):
    # The project sits at the previous stage while ``stage`` is being produced;
    # status writes are guarded by it so a worker that lost ownership stays silent.
    owner_stage = STAGE_ORDER[STAGE_ORDER.index(stage) - 1]
    set_stage_status(project_id, stage, StageStatus.RUNNING, expected_stage=owner_stage)
    logger.info(
        "pipeline.stage_started project_id=%s stage=%s attempt=%d", project_id, stage, attempt,
        extra={'project_id': str(project_id), 'stage': str(stage), 'attempt': attempt},
    )
    try:
        yield
    except PipelineStateError:
        # Another worker owns the project now: this stage did not fail.
        raise
    except Exception:
        logger.error(
            "pipeline.stage_failed project_id=%s stage=%s attempt=%d", project_id, stage, attempt,
            extra={'project_id': str(project_id), 'stage': str(stage), 'attempt': attempt},
        )
        try:
            set_stage_status(project_id, stage, StageStatus.FAILED, expected_stage=owner_stage)
        except PipelineStateError:
            logger.warning("pipeline.status_write_skipped project_id=%s stage=%s", project_id, stage)
        except (DatabaseError, VideoProject.DoesNotExist):
            logger.exception("pipeline.status_write_failed project_id=%s stage=%s", project_id, stage)
        raise
    logger.info(
        "pipeline.stage_completed project_id=%s stage=%s attempt=%d", project_id, stage, attempt,
        extra={'project_id': str(project_id), 'stage': str(stage), 'attempt': attempt},
    )


def _probe_video(source_path):
    with VideoFileClip(source_path) as clip:
        return clip.duration, list(clip.size)


def _generate_proxy(project, source_path, user_id):
    """Web proxy for the paper-edit UI. Optional: failures never block ingestion."""
    from apps.videos.services.storage_service import CloudflareR2Manager
    from apps.videos.utils.ffmpeg_utils import FFmpegManager

    proxy_local_path = os.path.join(os.path.dirname(source_path), f"proxy_{project.id}.mp4")
    try:
        FFmpegManager.generate_web_proxy(source_path, proxy_local_path)
        proxy_r2_key = CloudflareR2Manager.upload_video(proxy_local_path, user_id, str(project.id))
        VideoProject.objects.filter(pk=project.id).update(proxy_r2_key=proxy_r2_key)
        logger.info(f"✅ Proxy uploaded to R2: {proxy_r2_key}")
    except Exception:
        logger.exception("⚠️ Proxy generation failed but continuing ingestion")
    finally:
        FFmpegManager.cleanup_local_file(proxy_local_path)


def _stage_prepare_source(project_id):
    from apps.videos.services.storage_service import CloudflareR2Manager

    project = VideoProject.objects.get(pk=project_id)

    if not project.source_file and project.video_url:
        logger.info(f"⬇️ Downloading from URL: {project.video_url}")
        download_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'raw', 'downloads')
        os.makedirs(download_dir, exist_ok=True)
        local_path = download_from_youtube(project.video_url, download_dir)
        try:
            with open(local_path, 'rb') as f:
                project.source_file.save(os.path.basename(local_path), File(f), save=False)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)
        VideoProject.objects.filter(pk=project_id).update(source_file=project.source_file.name)

    source_path = project.source_file.path
    user_id = str(project.uploaded_by.id) if project.uploaded_by else "system"

    # A retry after a later failure inside this stage must not re-upload.
    if not project.original_r2_key:
        orig_r2_key = CloudflareR2Manager.upload_video(source_path, user_id, str(project.id))
        VideoProject.objects.filter(pk=project_id).update(original_r2_key=orig_r2_key)

    if not project.proxy_r2_key:
        _generate_proxy(project, source_path, user_id)

    duration, resolution = _probe_video(source_path)
    _merge_metadata(
        project_id, {'duration': duration, 'resolution': resolution},
        expected_stage=PipelineStage.UPLOADED,
    )
    advance(project_id, PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED)


def _stage_transcribe(project_id):
    from apps.videos.services.transcription_engine import TranscriptionEngine

    project = VideoProject.objects.get(pk=project_id)
    result = TranscriptionEngine(model_size="base").transcribe_detailed(project.source_file.path)

    tx = result.data
    segments = [{'start': w.start, 'end': w.end, 'text': w.text} for w in tx.words] or [
        {'start': s.start, 'end': s.end, 'text': s.text} for s in tx.segments
    ]

    # Persist the transcript and advance together: selection can fail and retry
    # without ever paying for transcription again.
    with transaction.atomic():
        _merge_metadata(project_id, {
            'full_text': tx.full_text,
            'transcription_usage': result.usage.model_dump(),
        }, expected_stage=PipelineStage.AUDIO_EXTRACTED)
        VideoProject.objects.filter(pk=project_id).update(transcript_data=segments)
        advance(project_id, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED)


def _stage_select_clips(project_id):
    project = VideoProject.objects.get(pk=project_id)
    segments = project.transcript_data or []
    metadata = project.metadata or {}

    duration = metadata.get('duration')
    if duration is None:
        # Rows created before pipeline state may lack the probe result.
        duration, resolution = _probe_video(project.source_file.path)
        _merge_metadata(
            project_id, {'duration': duration, 'resolution': resolution},
            expected_stage=PipelineStage.TRANSCRIBED,
        )
        metadata = {**metadata, 'duration': duration, 'resolution': resolution}

    result = SelectionEngine.select_viral_clips_detailed(
        # Segments are passed separately (not stored in metadata) so the LLM sees real timestamps.
        transcription_data={
            'full_text': metadata.get('full_text') or " ".join(s['text'] for s in segments),
            'duration': duration,
            'resolution': metadata.get('resolution'),
            'segments': segments,
        },
        project_title=project.title,
        duration=duration,
        intelligence_level=project.intelligence_level,
    )
    suggestions = [clip.model_dump(include=_CLIP_SUGGESTION_FIELDS) for clip in result.data.clips]

    with transaction.atomic():
        VideoProject.objects.select_for_update().get(pk=project_id)
        # Dedupe: clips from a previous attempt are reused, never duplicated.
        if not VideoClip.objects.filter(project_id=project_id).exists():
            VideoClip.objects.bulk_create([
                VideoClip(
                    project_id=project_id,
                    title=data['title'],
                    start_time=data['start'],
                    end_time=data['end'],
                    virality_score=int(data['virality_score']),
                    ai_reasoning=data['reasoning'],
                    status=VideoClip.Status.DRAFT,
                )
                for data in suggestions
            ])
        VideoProject.objects.filter(pk=project_id).update(ai_rationale_log={
            "suggestions_count": len(suggestions),
            "raw_ai_output": suggestions,
        })
        _merge_metadata(
            project_id, {'selection_usage': result.usage.model_dump()},
            expected_stage=PipelineStage.TRANSCRIBED,
        )
        advance(project_id, PipelineStage.TRANSCRIBED, PipelineStage.CLIPS_SELECTED)


def _stage_dispatch_render(project_id):
    """
    auto_render_bypass: one render task per clip, queued only AFTER the transition
    commits. Rendering never runs inside this transaction.
    """
    with transaction.atomic():
        VideoProject.objects.select_for_update().get(pk=project_id)
        clip_ids = list(
            VideoClip.objects.filter(project_id=project_id, status=VideoClip.Status.DRAFT)
            .values_list('id', flat=True)
        )
        if not clip_ids:
            raise NonRetryableAIError("Selection produced no clips to render.", provider="pipeline")

        advance(project_id, PipelineStage.CLIPS_SELECTED, PipelineStage.RENDER_DISPATCHED)
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.RENDERING)
        for clip_id in clip_ids:
            transaction.on_commit(lambda clip_id=clip_id: render_clip_task.delay(str(clip_id)))


def _run_ingestion(project_id, attempt, progress=None, lock=None):
    """
    ``progress['stage']`` always holds the stage this worker believes the project
    is at, so a failure handler can guard its writes by ownership.
    """
    progress = progress if progress is not None else {}

    def renew_lock():
        # Between stages: extend the TTL and stop if the lock was lost meanwhile.
        if lock is not None:
            lock.renew()

    project = VideoProject.objects.get(pk=project_id)
    stage = project.pipeline_stage
    progress['stage'] = stage

    if stage == PipelineStage.FAILED:
        # Manual re-dispatch of a failed project: resume where it stopped.
        stage = last_completed_stage(project)
        reopen(project_id, stage)
        progress['stage'] = stage

    if stage in TERMINAL_STAGES or stage == PipelineStage.RENDER_DISPATCHED:
        return f"Nothing to do for {project_id}: already {stage}"

    # Rows without recorded state may already hold a transcript or clips.
    inferred = infer_completed_stage(project)
    if STAGE_ORDER.index(inferred) > STAGE_ORDER.index(stage):
        stage = advance_to(project_id, stage, inferred)
        progress['stage'] = stage

    if stage in (PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED):
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.INGESTING)

    if stage == PipelineStage.UPLOADED:
        renew_lock()
        with _running_stage(project_id, PipelineStage.AUDIO_EXTRACTED, attempt):
            _stage_prepare_source(project_id)
        stage = PipelineStage.AUDIO_EXTRACTED
        progress['stage'] = stage

    if stage == PipelineStage.AUDIO_EXTRACTED:
        renew_lock()
        with _running_stage(project_id, PipelineStage.TRANSCRIBED, attempt):
            _stage_transcribe(project_id)
        stage = PipelineStage.TRANSCRIBED
        progress['stage'] = stage

    if stage == PipelineStage.TRANSCRIBED:
        renew_lock()
        with _running_stage(project_id, PipelineStage.CLIPS_SELECTED, attempt):
            _stage_select_clips(project_id)
        stage = PipelineStage.CLIPS_SELECTED
        progress['stage'] = stage

    if stage == PipelineStage.CLIPS_SELECTED:
        renew_lock()
        if VideoProject.objects.get(pk=project_id).auto_render_bypass:
            logger.info("⏩ Auto-render bypass active. Dispatching clip renders.")
            with _running_stage(project_id, PipelineStage.RENDER_DISPATCHED, attempt):
                _stage_dispatch_render(project_id)
        else:
            logger.info("⏳ Ingestion complete. Awaiting user approval.")
            # Guarded: a redelivered task must not undo a later user action.
            VideoProject.objects.filter(
                pk=project_id,
                status__in=[VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING],
            ).update(status=VideoProject.Status.AWAITING_APPROVAL)

    return f"Ingestion Success {project_id}"


def _record_pipeline_failure(project_id, exc, attempt, final, stage=None):
    logger.error(
        "pipeline.failed project_id=%s attempt=%d final=%s error=%s",
        project_id, attempt, final, type(exc).__name__,
        extra={'project_id': str(project_id), 'attempt': attempt},
        exc_info=exc,
    )
    try:
        VideoProject.objects.filter(pk=project_id).update(
            pipeline_attempts=F('pipeline_attempts') + 1
        )
        record_error(project_id, type(exc).__name__)
        if final:
            mark_failed(project_id, expected_stage=stage)
    except (DatabaseError, VideoProject.DoesNotExist):
        logger.exception("pipeline.failure_not_recorded project_id=%s", project_id)


# acks_late + reject_on_worker_lost: if the worker dies mid-task (OOM during
# MoviePy/Whisper) the message is redelivered instead of silently lost. Running
# the task twice is safe because every stage is idempotent: transitions are
# compare-and-set, the Redis lock keeps two workers off the same project, and
# stage outputs (transcript, clips) are persisted and reused, never recomputed.
@celery_app.task(
    bind=True,
    max_retries=PIPELINE_MAX_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_initial_ingestion(self, project_id):
    """
    Multimodal Ingestion Pipeline V2 (Paper Edit V1 + Video Sync), recoverable.
    Retryable errors back off exponentially (TD-04: replaces the fixed 60s
    countdown); non-retryable errors fail the project immediately.
    """
    attempt = self.request.retries + 1
    logger.info(
        "pipeline.started project_id=%s attempt=%d", project_id, attempt,
        extra={'project_id': str(project_id), 'attempt': attempt},
    )
    progress = {}
    try:
        with project_lock(project_id) as lock:
            return _run_ingestion(project_id, attempt, progress, lock)
    except ProjectLockedError as exc:
        if self.request.retries < PIPELINE_LOCK_MAX_RETRIES:
            logger.warning(
                "pipeline.lock_busy_retrying project_id=%s attempt=%d", project_id, attempt,
                extra={'project_id': str(project_id), 'attempt': attempt},
            )
            raise self.retry(
                exc=exc,
                countdown=PIPELINE_LOCK_RETRY_SECONDS,
                max_retries=PIPELINE_LOCK_MAX_RETRIES,
            )
        # Still held after outlasting the TTL: a live worker owns the project.
        logger.warning(
            "pipeline.skipped project_id=%s attempt=%d reason=%s", project_id, attempt, exc,
            extra={'project_id': str(project_id), 'attempt': attempt},
        )
        return f"Skipped {project_id}: {exc}"
    except (ProjectLockLostError, PipelineConflictError) as exc:
        logger.warning(
            "pipeline.skipped project_id=%s attempt=%d reason=%s", project_id, attempt, exc,
            extra={'project_id': str(project_id), 'attempt': attempt},
        )
        return f"Skipped {project_id}: {exc}"
    except Exception as exc:
        will_retry = is_retryable_error(exc) and self.request.retries < self.max_retries
        _record_pipeline_failure(
            project_id, exc, attempt, final=not will_retry, stage=progress.get('stage')
        )
        if will_retry:
            raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries))
        raise

@celery_app.task(bind=True)
def process_video_pipeline(self, project_id, transaction_id=None):
    """
    Orchestrator task that links payments and ingestion.
    """
    logger.info(f"🧬 [PIPELINE] Starting for project {project_id} (Tx: {transaction_id})")
    # For now, it delegates to initial ingestion. 
    # In the future, it could handle the full lifecycle.
    return process_initial_ingestion.delay(project_id)
