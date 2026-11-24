import json
import os
import uuid
import logging
from celery import shared_task
from django.conf import settings
from django.core.files import File

# Importamos Modelos
from apps.videos.models import AIUsageRecord, VideoProject, VideoClip
from apps.payments.models import Transaction
from moviepy import VideoFileClip, concatenate_videoclips

# Importamos Servicios (SelectionEngine suele ser seguro, TranscriptionEngine lo cargaremos lazy por si acaso)
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
        if post.generated_caption:
            # Stored apart (ADR-013): the hashtags get their '#' only here.
            from apps.videos.services.seo_engine import compose_caption

            caption = compose_caption(post.generated_caption, post.generated_hashtags)
        else:
            caption = post.video_clip.project.metadata.get('caption')
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
        logger.error("⚠️ [WORKER] Ayrshare API Error: %s", type(exc).__name__)
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
        logger.error("❌ [WORKER] Critical failure: %s", type(e).__name__)
        ScheduledPost.objects.filter(id=post_id).update(
            status=ScheduledPost.Status.FAILED,
            error_log=f"Critical: {str(e)}",
        )
        # Re-raised so Celery records FAILURE instead of reporting a silent SUCCESS.
        raise

    finally:
        cache.delete(lock_id)

# Social metadata (AICORE-17, ADR-013). One generation at a time per post, and
# the last generation kept until its output moderation passes, so a moderation
# retry never pays for a second generation.
SEO_LOCK_TTL_SECONDS = 600
SEO_PENDING_TTL_SECONDS = 3600


@celery_app.task(bind=True, max_retries=3)
def process_video_seo(self, post_id, capacity_waited_s=0.0, regenerate=False):
    """
    Caption and hashtags for one ScheduledPost from the clip's own transcript,
    for the post's platform (AICORE-17, ADR-013).

    - Idempotent: a post that already has them is not sent to the LLM again,
      unless ``regenerate`` (an explicit user request).
    - One generation at a time per post (Redis lock).
    - The clip transcript is moderated before the call; the generated text
      before it is stored.
    - Every attempt writes an AIUsageRecord with stage ``seo``.
    - A final failure leaves the post FAILED with a static code in
      ``error_log``; non-retryable errors are not retried.
    """
    from apps.core.security import ModerationError

    try:
        post = ScheduledPost.objects.select_related('video_clip__project').get(id=post_id)
        if not regenerate and post.generated_caption and post.generated_hashtags:
            return "SEO Already Generated"
        with _seo_post_lock(post_id) as acquired:
            if not acquired:
                # Another worker is generating this post right now.
                logger.info("seo.skipped post_id=%s reason=locked", post_id)
                return "SEO Busy"
            post.refresh_from_db()
            if post.status in _SEO_FROZEN_STATUSES:
                # Publishing started or finished: its caption is final.
                logger.info("seo.skipped post_id=%s reason=%s", post_id, post.status)
                return "SEO Skipped"
            if not regenerate and post.generated_caption and post.generated_hashtags:
                return "SEO Already Generated"  # finished by another run before the lock
            if regenerate and self.request.retries == 0 and not capacity_waited_s:
                _seo_pending_clear(post_id)  # a fresh request, not a retry of this one
            return _generate_post_metadata(self, post, capacity_waited_s, regenerate)

    except ModerationError as mod_exc:
        # Fail closed: no moderation verdict => content does not pass. Must stay above
        # the generic handler, which would retry blindly with a fixed countdown.
        logger.error(
            "🛡️ [SEO SECURITY] Moderation failed for post %s: code=%s retryable=%s retries=%s",
            post_id, mod_exc.code, mod_exc.retryable, self.request.retries,
        )
        if mod_exc.retryable and self.request.retries < self.max_retries:
            raise self.retry(countdown=_retry_countdown(self.request.retries))
        # Static code only: never the provider message or any user text.
        _fail_seo_post(post_id, mod_exc.code)
        return "Moderation Failed"

    except AIError as exc:
        # Classified provider errors: auth, configuration and 4xx stop here.
        will_retry = exc.is_retryable and self.request.retries < self.max_retries
        logger.error(
            "seo.failed post_id=%s error=%s retry=%s", post_id, type(exc).__name__, will_retry
        )
        if will_retry:
            raise self.retry(countdown=_retry_countdown(self.request.retries))
        _fail_seo_post(post_id, f"seo_generation_failed:{type(exc).__name__}")
        return "SEO Failed"

    except Exception as exc:
        will_retry = (
            _is_retryable_pipeline_error(exc) and self.request.retries < self.max_retries
        )
        logger.error(
            "seo.failed post_id=%s error=%s retry=%s", post_id, type(exc).__name__, will_retry
        )
        if will_retry:
            raise self.retry(countdown=_retry_countdown(self.request.retries))
        _fail_seo_post(post_id, f"seo_failed:{type(exc).__name__}")
        raise


# Codes this task writes to error_log; a later successful generation clears them.
_SEO_FAILURE_CODES = (
    'seo_no_clip_transcript', 'content_blocked', 'generated_content_blocked', 'capacity_timeout',
)


# Publishing started or finished: SEO never changes these posts.
_SEO_FROZEN_STATUSES = (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED)


def _fail_seo_post(post_id, code):
    """
    FAILED with a static code, unless publishing already started or finished:
    a post published with another caption must never turn FAILED because of SEO.
    A failed regeneration keeps the caption it was replacing, and the post keeps
    its status: it can still be published.
    """
    posts = ScheduledPost.objects.filter(id=post_id).exclude(status__in=_SEO_FROZEN_STATUSES)
    if posts.filter(generated_caption__gt='', generated_hashtags__isnull=False).exists():
        logger.warning("seo.regenerate_failed post_id=%s code=%s", post_id, code)
        return
    posts.update(status=ScheduledPost.Status.FAILED, error_log=code)


def _recover_seo_failed_post(post_id):
    """
    A post an earlier SEO failure left FAILED is recoverable (ADR-013): back to
    DRAFT, so the user reviews the caption and schedules it again. It is never
    re-scheduled on its own. Publishing failures are not touched.
    """
    seo_codes = (
        Q(error_log__in=_SEO_FAILURE_CODES)
        | Q(error_log__startswith='seo_')
        | Q(error_log__startswith='moderation_')
    )
    ScheduledPost.objects.filter(seo_codes, id=post_id, status=ScheduledPost.Status.FAILED).update(
        status=ScheduledPost.Status.DRAFT, error_log=None,
    )


@contextmanager
def _seo_post_lock(post_id):
    """Yields whether this worker holds the post; Redis down is a retryable error."""
    import redis

    from apps.videos.services.ai.errors import RetryableAIError
    from apps.videos.services.ai.project_lock import get_redis_client

    lock = get_redis_client().lock(f"lock:seo:post:{post_id}", timeout=SEO_LOCK_TTL_SECONDS)
    try:
        acquired = lock.acquire(blocking=False)
    except redis.exceptions.RedisError as exc:
        raise RetryableAIError(
            f"Redis unavailable, cannot lock post {post_id}", provider="redis"
        ) from exc
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except redis.exceptions.LockError:
                logger.warning("seo.lock_not_owned post_id=%s", post_id)


def _seo_pending_key(post_id):
    return f"seo:pending:{post_id}"


def _seo_pending_get(post_id):
    from apps.videos.services.ai.project_lock import get_redis_client

    raw = get_redis_client().get(_seo_pending_key(post_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _seo_pending_set(post_id, data):
    from apps.videos.services.ai.project_lock import get_redis_client

    try:
        get_redis_client().set(
            _seo_pending_key(post_id), json.dumps(data), ex=SEO_PENDING_TTL_SECONDS
        )
    except Exception as exc:  # the result is still used right away
        logger.warning("seo.pending_not_saved post_id=%s error=%s", post_id, type(exc).__name__)


def _seo_pending_clear(post_id):
    from apps.videos.services.ai.project_lock import get_redis_client

    try:
        get_redis_client().delete(_seo_pending_key(post_id))
    except Exception as exc:
        logger.warning("seo.pending_not_cleared post_id=%s error=%s", post_id, type(exc).__name__)


def _generate_post_metadata(task, post, capacity_waited_s, regenerate=False):
    """Generates (or reuses the pending generation), moderates the output and stores it."""
    from apps.core.security import AI_Security_Shield, UnsafeContentError
    from apps.videos.services.seo_engine import SocialMetadata

    pending = _seo_pending_get(post.id)
    if pending is None:
        outcome = _generate_social_metadata(task, post, capacity_waited_s, regenerate)
        if isinstance(outcome, str):  # waiting for capacity, or the post already failed
            return outcome
        pending = outcome
        _seo_pending_set(post.id, pending)

    metadata = SocialMetadata.model_validate(pending)
    try:
        AI_Security_Shield.check_content_safety(metadata.composed_caption())
    except UnsafeContentError:
        _seo_pending_clear(post.id)
        logger.error("seo.output_blocked post_id=%s", post.id)
        _fail_seo_post(post.id, "generated_content_blocked")
        return "Security Abort"

    stored = ScheduledPost.objects.filter(id=post.id).exclude(
        status__in=_SEO_FROZEN_STATUSES,
    ).update(generated_caption=metadata.caption, generated_hashtags=metadata.hashtags)
    if not stored:
        # Publishing started while this run generated: its caption is final.
        _seo_pending_clear(post.id)
        logger.info("seo.skipped post_id=%s reason=publishing", post.id)
        return "SEO Skipped"
    _recover_seo_failed_post(post.id)
    _seo_pending_clear(post.id)
    logger.info("seo.persisted post_id=%s hashtags=%d", post.id, len(metadata.hashtags))
    return "SEO Success"


def _generate_social_metadata(task, post, capacity_waited_s, regenerate=False):
    """
    One paid generation: clip transcript, capacity, input moderation, LLM call
    and usage rows. Returns the metadata as a dict, or a status string when the
    post waits for capacity or already failed.
    """
    from apps.core.security import AI_Security_Shield, ModerationError, UnsafeContentError
    from apps.videos.services.seo_engine import (
        SEOOptimizationService,
        SocialMetadataRequest,
        clip_transcript,
    )

    clip = post.video_clip
    project = clip.project
    text = clip_transcript(project.transcript_data, clip.start_time, clip.end_time)
    if not text:
        # Never the whole project transcript as a substitute (ADR-013).
        logger.warning("seo.no_clip_transcript post_id=%s", post.id)
        _fail_seo_post(post.id, "seo_no_clip_transcript")
        return "No Transcript"
    request = SocialMetadataRequest(
        clip_transcript=text,
        clip_title=clip.title or "",
        platform=post.platform,
        target_niche=(project.metadata or {}).get('niche') or 'general',
        language=getattr(settings, 'AI_SEO_LANGUAGE', 'es'),
    )

    # Capacity first (AICORE-11), so waiting for it never repeats the moderation call.
    gate = _capacity_gate()
    reservation, models = None, []
    if gate is not None:
        models = _selection_models(True)
        try:
            reservation = gate.reserve(_seo_demand(models, request), stage='seo')
        except CapacityUnavailable as capacity_exc:
            return _seo_wait_for_capacity(
                str(post.id), capacity_exc, capacity_waited_s, task.request.retries, regenerate
            )

    try:
        AI_Security_Shield.check_content_safety(_seo_moderation_payload(request))
    except UnsafeContentError:
        _release_seo_capacity(gate, reservation)
        logger.error("seo.input_blocked post_id=%s", post.id)
        _fail_seo_post(post.id, "content_blocked")
        return "Security Abort"
    except ModerationError:
        _release_seo_capacity(gate, reservation)  # the LLM was never called
        raise

    started = time.monotonic()
    try:
        result = SEOOptimizationService().generate_metadata(request)
    except Exception as exc:
        if gate is not None:
            gate.settle(reservation, _selection_failure_usage(models, exc))
        _record_call_failure(
            project.id, AIUsageRecord.Stage.SEO, AIUsageRecord.PipelineVersion.V2, exc,
            _llm_identity(getattr(settings, 'AI_DEFAULT_LLM_MODEL', 'unknown')), started,
        )
        raise
    if gate is not None:
        gate.settle(reservation, _llm_usage_by_model(models, result.attempts or [result.usage]))
    record_usages(
        project.id, AIUsageRecord.Stage.SEO, AIUsageRecord.PipelineVersion.V2,
        usages_for_success(result),
    )
    return result.data.model_dump()


def _seo_moderation_payload(request):
    return "\n".join(part for part in (request.clip_title, request.clip_transcript) if part)

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
                return f"Render Success: {final_s3_key}"

    except NonRetryableAIError as e:
        # Deterministic failure (bad or missing segments): retrying cannot fix it.
        logger.error(f"❌ [FINAL RENDER ERROR] project {project_id}: {e.message}")
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.FAILED)
        record_error(project_id, e.metadata.get('code', 'render_failed'))
        raise
    except Exception as e:
        logger.error(
            "❌ [FINAL RENDER ERROR] project_id=%s error=%s", project_id, type(e).__name__
        )
        if 'project' in locals():
            project.status = VideoProject.Status.FAILED
            project.save()
        raise self.retry(exc=e, countdown=60)
    finally:
        # The rendered output is written under MEDIA_ROOT, outside the temporary
        # directory, so it is removed explicitly on every exit path.
        if output_path and os.path.exists(output_path):
            os.remove(output_path)

# --- INGESTION PIPELINE (AICORE-7: recoverable, idempotent) ---
#
# Stages (see services/ai/pipeline_state.py):
#   UPLOADED -> AUDIO_EXTRACTED (source in R2 + proxy + probe)
#            -> TRANSCRIBED     (transcript persisted)
#            -> MODERATED       (transcript + user text passed moderation, ADR-011)
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
# Prompt-to-Edit parser (PTE-01b): transient errors re-queue the ingestion with
# their own counter, so they never spend the retries the stages need. After
# these, the project goes on with the default settings.
PTE_PARSE_MAX_RETRIES = 2
PTE_PARSE_RETRY_SECONDS = (30, 90)

_CLIP_SUGGESTION_FIELDS = {'start', 'end', 'title', 'virality_score', 'reasoning'}


# --- Transient infrastructure errors -----------------------------------------
# errors.is_retryable_error only knows AI/provider failures. The source-preparation
# and persistence stages also fail transiently on R2, yt-dlp, the filesystem and
# the database; those must back off and retry instead of failing the project for
# good. Deterministic errors (403, missing file, private video) stay non-retryable.

_TRANSIENT_S3_ERROR_CODES = frozenset({
    'SlowDown', 'RequestTimeout', 'ServiceUnavailable', 'InternalError',
    'Throttling', 'ThrottlingException', 'RequestLimitExceeded', 'TooManyRequests',
})
_PERMANENT_DOWNLOAD_MARKERS = (
    'video unavailable', 'private video', 'sign in', 'unsupported url', 'copyright',
    'not available', 'does not exist', 'has been removed', 'members-only', 'age-restricted',
)
_DETERMINISTIC_OS_ERRORS = (
    FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError, FileExistsError,
)


def _is_retryable_pipeline_error(exc):
    """AI errors keep their own flag; known transient infrastructure errors retry."""
    if isinstance(exc, AIError):
        return is_retryable_error(exc)
    if is_retryable_error(exc):
        return True

    from apps.core.security import ModerationError
    if isinstance(exc, ModerationError):
        # ADR-011 D-M4: no verdict yet (timeout, 5xx, 429) retries with backoff;
        # a misconfigured or exhausted account does not. Blocked content
        # (UnsafeContentError) never retries.
        return exc.retryable

    from django.db import InterfaceError, OperationalError
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True

    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:  # pragma: no cover - boto3 is a hard dependency
        BotoCoreError = ClientError = ()
    if ClientError and isinstance(exc, ClientError):
        error = exc.response.get('Error', {})
        http_status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0) or 0
        return (
            http_status >= 500
            or http_status in (408, 429)
            or error.get('Code') in _TRANSIENT_S3_ERROR_CODES
        )
    if BotoCoreError and isinstance(exc, BotoCoreError):
        # Endpoint/connection/read-timeout failures; config errors are rare and bounded by max_retries.
        return True

    try:
        from yt_dlp.utils import DownloadError
    except ImportError:  # pragma: no cover
        DownloadError = ()
    if DownloadError and isinstance(exc, DownloadError):
        message = str(exc).lower()
        return not any(marker in message for marker in _PERMANENT_DOWNLOAD_MARKERS)

    try:
        from kombu.exceptions import OperationalError as BrokerError
        import redis
        broker_errors = (BrokerError, redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)
    except ImportError:  # pragma: no cover
        broker_errors = ()
    if broker_errors and isinstance(exc, broker_errors):
        return True

    if isinstance(exc, OSError):
        return not isinstance(exc, _DETERMINISTIC_OS_ERRORS)

    return False


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
    except CapacityUnavailable:
        # Waiting for provider capacity (AICORE-11) is not a failure.
        try:
            set_stage_status(
                project_id, stage, StageStatus.PENDING_CAPACITY, expected_stage=owner_stage
            )
        except PipelineStateError:
            logger.warning("pipeline.status_write_skipped project_id=%s stage=%s", project_id, stage)
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
        except (DatabaseError, VideoProject.DoesNotExist) as status_exc:
            logger.error(
                "pipeline.status_write_failed project_id=%s stage=%s error=%s",
                project_id, stage, type(status_exc).__name__,
            )
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
    except Exception as proxy_exc:
        logger.error(
            "⚠️ Proxy generation failed but continuing ingestion project_id=%s error=%s",
            project.id, type(proxy_exc).__name__,
        )
    finally:
        FFmpegManager.cleanup_local_file(proxy_local_path)


def _stage_prepare_source(project_id):
    from apps.videos.services.storage_service import CloudflareR2Manager

    project = VideoProject.objects.get(pk=project_id)

    if not project.source_file and project.video_url:
        logger.info("⬇️ Downloading source from URL for project %s", project_id)
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


def _llm_identity(model):
    """(provider, model) of a LiteLLM model string; a bare name defaults to openai."""
    from apps.videos.services.ai.llm_credentials import provider_for_model

    return provider_for_model(model), model


def _record_call_failure(project_id, stage, pipeline_version, exc, identity, started):
    """
    One usage row per attempt of a failed provider call: the attempts the error
    carries (billed ones included) or a zero-token row with the measured latency.
    Never raises; the caller re-raises the original exception unchanged.
    """
    provider, model = identity
    record_usages(
        project_id, stage, pipeline_version,
        usages_for_failure(exc, provider, model, time.monotonic() - started),
    )


def _persist_then_record_usage(project_id, stage, pipeline_version, usages, persist):
    """
    Runs ``persist`` (the stage artifact + transition) and writes the usage rows
    AFTER it, outside its transaction. If persisting fails the call was billed
    anyway, so the rows are still written before the error propagates.
    """
    try:
        persist()
    except Exception:
        record_usages(project_id, stage, pipeline_version, usages)
        raise
    record_usages(project_id, stage, pipeline_version, usages)


# --- AI capacity (AICORE-11) -------------------------------------------------
# Every Groq/LLM call of the pipeline reserves capacity first. No room means the
# stage waits (pending_capacity) and the task is re-queued; it is not a failure.

def _capacity_gate():
    from apps.videos.services.ai.capacity import CapacityGate, capacity_enabled

    return CapacityGate() if capacity_enabled() else None


def _transcription_demand(model_key, duration):
    from apps.videos.services.ai.audio_preprocessor import AudioPreprocessor

    seconds = max(1.0, float(duration or 0))
    # Upper bound: the preprocessor steps by window - overlap (a small file is one request).
    step = AudioPreprocessor.WINDOW_DURATION_SECONDS - AudioPreprocessor.OVERLAP_SECONDS
    chunks = max(1, math.ceil(seconds / step))
    return {model_key: {'audio_seconds': seconds, 'requests': chunks}}


def _llm_demand(models, duration):
    minutes = max(1.0, float(duration or 0) / 60.0)
    tokens = (
        math.ceil(minutes * settings.AI_CAPACITY_LLM_TOKENS_PER_AUDIO_MINUTE)
        + settings.AI_CAPACITY_LLM_PROMPT_TOKENS
        + settings.AI_CAPACITY_LLM_OUTPUT_TOKENS
    )
    return {model: {'tokens': tokens, 'requests': 1} for model in models}


def _selection_models(v2_enabled):
    """Models the selection stage may call: the fallback is reserved too, then refunded if unused."""
    from apps.videos.services.ai.llm_credentials import resolve_api_key

    if not v2_enabled:
        return [GeminiFlashStrategy().get_model_name()]
    primary = settings.AI_DEFAULT_LLM_MODEL
    fallback = getattr(settings, 'AI_FALLBACK_LLM_MODEL', None)
    models = [primary]
    if fallback and fallback != primary and resolve_api_key(fallback):
        models.append(fallback)
    return models


def _reserve_selection(project_id, v2_enabled, duration):
    gate = _capacity_gate()
    if gate is None:
        return None, None, []
    models = _selection_models(v2_enabled)
    reservation = gate.reserve(_llm_demand(models, duration), stage=PipelineStage.CLIPS_SELECTED)
    _capacity_granted(project_id, PipelineStage.CLIPS_SELECTED)
    return gate, reservation, models


def _selection_failure_usage(models, exc):
    """
    Usage of a failed selection: the attempts the error carries. Without them,
    a timeout or dropped connection may still have been billed, so the
    reservation is kept; any other failure never reached the model (refund).
    """
    from apps.videos.services.ai.errors import is_billing_uncertain

    attempts = getattr(exc, 'usage_attempts', None)
    if not attempts and is_billing_uncertain(exc):
        return {}
    return _llm_usage_by_model(models, attempts)


def _llm_usage_by_model(models, usages):
    """
    Real usage per model from the attempts made. A model never called gets 0
    (its reservation is refunded); a call whose billing is unknown keeps the
    reserved tokens (None) rather than guessing.
    """
    actual = {model: {'tokens': 0, 'requests': 0} for model in models}
    for usage in usages or []:
        entry = actual.setdefault(usage.model, {'tokens': 0, 'requests': 0})
        entry['requests'] += 1
        if entry['tokens'] is None:
            continue
        if usage.error_code and usage.estimated_cost_usd is None and not usage.total_tokens:
            entry['tokens'] = None  # failed with unknown billing: keep the reservation
        else:
            entry['tokens'] += usage.total_tokens
    return actual


def _audio_usage(model_key, usage_or_exc):
    """Billed audio seconds, or None when unknown (the reservation is kept)."""
    from apps.videos.services.ai.errors import is_billing_uncertain

    if isinstance(usage_or_exc, BaseException):
        if is_billing_uncertain(usage_or_exc):
            return {}  # the chunk in flight may have been billed
        carried = getattr(usage_or_exc, 'usage_attempts', None) or []
        if carried and carried[0].audio_seconds is not None:
            if carried[0].estimated_cost_usd is None:
                return {}  # the provider could not price it: unknown
            seconds = carried[0].audio_seconds
        else:
            seconds = 0.0  # rejected before any chunk was billed
    else:
        seconds = usage_or_exc.audio_seconds
        if seconds is None:
            return {}
    return {model_key: {'audio_seconds': seconds}}


def _is_current_wait(wait, project, stage):
    """
    A wait belongs to this run only if it is for the same stage and no failed
    attempt was recorded since it started; otherwise (e.g. a manual re-dispatch
    after a failure) it is stale and a new wait starts.
    """
    return (
        bool(wait) and wait.get('stage') == str(stage)
        and wait.get('attempts') == project.pipeline_attempts
    )


def _capacity_granted(project_id, stage):
    """Closes a pending_capacity wait: logs and stores how long it took (no text)."""
    project = VideoProject.objects.get(pk=project_id)
    metadata = project.metadata or {}
    wait = metadata.get('capacity_wait')
    if not wait:
        return
    if not _is_current_wait(wait, project, stage):
        _merge_metadata(project_id, {'capacity_wait': None})  # stale: nothing to record
        return
    try:
        waited = (timezone.now() - datetime.fromisoformat(wait['since'])).total_seconds()
    except (KeyError, TypeError, ValueError):
        waited = None
    logger.info(
        "pipeline.capacity_granted project_id=%s stage=%s waited_s=%s",
        project_id, stage, "?" if waited is None else f"{waited:.1f}",
        extra={'project_id': str(project_id), 'stage': str(stage)},
    )
    history = dict(metadata.get('capacity_waited_s') or {})
    if waited is not None:
        history[str(stage)] = round(history.get(str(stage), 0) + waited, 1)
    _merge_metadata(project_id, {'capacity_wait': None, 'capacity_waited_s': history})


def _capacity_countdown(wait_seconds):
    """Re-queue delay: the bucket deficit, capped, plus up to 20 % jitter so waiting tasks spread out."""
    countdown = min(max(wait_seconds, 1.0), float(settings.AI_CAPACITY_MAX_COUNTDOWN_SECONDS))
    return countdown * (1 + random.uniform(0, 0.2))


def _seo_demand(models, request):
    """
    SEO prompt ~ the clip title and transcript (~4 characters per token) plus the
    fixed prompt and output, on the primary and the fallback (refunded if unused).
    """
    tokens = (
        math.ceil((len(request.clip_transcript) + len(request.clip_title)) / 4)
        + settings.AI_CAPACITY_LLM_PROMPT_TOKENS
        + settings.AI_CAPACITY_LLM_OUTPUT_TOKENS
    )
    return {model: {'tokens': tokens, 'requests': 1} for model in models}


def _prompt_capacity_exhausted(project_id, exc):
    """True when waiting more for the parser's capacity would pass the maximum wait."""
    project = VideoProject.objects.get(pk=project_id)
    wait = (project.metadata or {}).get('capacity_wait') or {}
    planned = wait.get('planned_s', 0.0) if _is_current_wait(wait, project, exc.stage) else 0.0
    if planned + exc.wait_seconds <= settings.AI_CAPACITY_MAX_WAIT_SECONDS:
        return False
    logger.warning(
        "prompt_parse.capacity_timeout project_id=%s planned_s=%.1f", project_id, planned,
    )
    _merge_metadata(project_id, {'capacity_wait': None})
    return True


class _PromptParseCapacity:
    """
    Capacity for the Prompt-to-Edit parser call (AICORE-11, PTE-01b): the
    prompt and the parser's fixed prompt and output, on the primary and the
    fallback with a key, settled with the real usage like the selection.
    """

    def __init__(self, project_id, stage):
        self.project_id = project_id
        self.stage = stage
        self.gate = _capacity_gate()
        self.models = []

    def reserve(self, prompt):
        if self.gate is None:
            return None
        self.models = _selection_models(True)
        tokens = (
            math.ceil(len(prompt) / 4)
            + settings.AI_CAPACITY_LLM_PROMPT_TOKENS
            + settings.AI_CAPACITY_LLM_OUTPUT_TOKENS
        )
        reservation = self.gate.reserve(
            {model: {'tokens': tokens, 'requests': 1} for model in self.models}, stage=self.stage,
        )
        _capacity_granted(self.project_id, self.stage)
        return reservation

    def settle(self, reservation, result=None, exc=None):
        if self.gate is None or reservation is None:
            return
        if exc is not None:
            self.gate.settle(reservation, _selection_failure_usage(self.models, exc))
        else:
            self.gate.settle(reservation, _llm_usage_by_model(self.models, result.attempts or [result.usage]))


def _release_seo_capacity(gate, reservation):
    """Moderation stopped the post before the LLM call: the reservation goes back."""
    if gate is not None and reservation is not None:
        gate.settle(reservation, {
            bucket.model: {'tokens': 0, 'requests': 0} for bucket, _ in reservation.drawn
        })


def _seo_wait_for_capacity(post_id, exc, waited_s, retries, regenerate=False):
    """
    Re-queues SEO generation after the capacity wait; fails the post past the max
    wait. The Celery retry count travels with the message: a call that keeps
    failing still runs out of retries even if capacity waits sit in between.
    """
    if waited_s + exc.wait_seconds > settings.AI_CAPACITY_MAX_WAIT_SECONDS:
        logger.error("seo.capacity_timeout post_id=%s waited_s=%.1f", post_id, waited_s)
        _fail_seo_post(post_id, 'capacity_timeout')
        return "Capacity Timeout"
    countdown = _capacity_countdown(exc.wait_seconds)
    logger.info("seo.pending_capacity post_id=%s countdown_s=%.1f", post_id, countdown)
    kwargs = {'capacity_waited_s': round(waited_s + countdown, 1)}
    if regenerate:
        kwargs['regenerate'] = True  # or the re-queued run would see the old caption and stop
    process_video_seo.apply_async(
        args=[post_id], kwargs=kwargs, countdown=countdown, retries=retries,
    )
    return "Waiting for capacity"


def _wait_for_capacity(project_id, exc, attempt, owner_stage, retries):
    """
    Re-queues the ingestion after the capacity wait instead of blocking the
    worker. Not a failed attempt: pipeline_attempts and the usage rows are not
    touched. Fails with CapacityTimeoutError once the planned waiting for the
    stage would exceed AI_CAPACITY_MAX_WAIT_SECONDS.

    The Celery retry count travels with the re-queued message, so a provider
    call that keeps failing still runs out of retries (PIPELINE_MAX_RETRIES)
    even when capacity waits sit between its attempts.
    """
    from apps.videos.services.ai.capacity import CapacityTimeoutError

    project = VideoProject.objects.get(pk=project_id)
    wait = (project.metadata or {}).get('capacity_wait') or {}
    if not _is_current_wait(wait, project, exc.stage):
        wait = {
            'stage': str(exc.stage), 'since': timezone.now().isoformat(), 'planned_s': 0.0,
            'attempts': project.pipeline_attempts,
        }

    if wait['planned_s'] + exc.wait_seconds > settings.AI_CAPACITY_MAX_WAIT_SECONDS:
        logger.error(
            "pipeline.capacity_timeout project_id=%s stage=%s planned_s=%.1f wait_s=%.1f",
            project_id, exc.stage, wait['planned_s'], exc.wait_seconds,
            extra={'project_id': str(project_id), 'stage': str(exc.stage)},
        )
        try:
            set_stage_status(project_id, exc.stage, StageStatus.FAILED, expected_stage=owner_stage)
        except PipelineStateError:
            pass
        _merge_metadata(project_id, {'capacity_wait': None})
        _record_pipeline_failure(
            project_id, CapacityTimeoutError("capacity wait exceeded"), attempt,
            final=True, stage=owner_stage,
        )
        return f"Capacity timeout for {project_id}"

    countdown = _capacity_countdown(exc.wait_seconds)
    wait['planned_s'] = round(wait['planned_s'] + countdown, 1)
    _merge_metadata(project_id, {'capacity_wait': wait})
    logger.info(
        "pipeline.pending_capacity project_id=%s stage=%s countdown_s=%.1f planned_s=%.1f",
        project_id, exc.stage, countdown, wait['planned_s'],
        extra={'project_id': str(project_id), 'stage': str(exc.stage)},
    )
    process_initial_ingestion.apply_async(
        args=[str(project_id)], countdown=countdown, retries=retries
    )
    return f"Waiting for capacity: {project_id}"


def _stage_transcribe(project_id):
    from apps.videos.services.transcription_engine import TranscriptionBackend, TranscriptionEngine

    from apps.videos.services.ai.rollout import v2_enabled_for

    project = VideoProject.objects.get(pk=project_id)
    model_size = "base"
    # Per-project rollout (AICORE-9): decided once here and handed to the engine,
    # so the backend below is the one that runs.
    v2_enabled = v2_enabled_for(project)
    engine = TranscriptionEngine(model_size=model_size, v2_enabled=v2_enabled)

    # Fixed once from the backend the engine resolves (groq -> v2, local Whisper
    # -> legacy) and never re-read: the usage rows describe what actually ran.
    backend = engine.resolve_backend(v2_enabled)
    pipeline_version = (
        AIUsageRecord.PipelineVersion.V2
        if backend is TranscriptionBackend.GROQ
        else AIUsageRecord.PipelineVersion.LEGACY
    )
    if backend is TranscriptionBackend.GROQ:
        identity = ('groq', getattr(settings, 'AI_DEFAULT_TRANSCRIPTION_MODEL', 'whisper-large-v3-turbo'))
    else:
        identity = ('local', f'whisper-{model_size}')

    # Remote transcription reserves Groq capacity first (local Whisper calls no provider).
    gate = _capacity_gate() if backend is TranscriptionBackend.GROQ else None
    reservation = None
    capacity_model = f"groq/{identity[1]}"
    if gate is not None:
        reservation = gate.reserve(
            _transcription_demand(capacity_model, (project.metadata or {}).get('duration')),
            stage=PipelineStage.TRANSCRIBED,
        )
        _capacity_granted(project_id, PipelineStage.TRANSCRIBED)

    started = time.monotonic()
    try:
        result = engine.transcribe_detailed(project.source_file.path)
    except Exception as exc:
        if gate is not None:
            gate.settle(reservation, _audio_usage(capacity_model, exc))
        _record_call_failure(
            project_id, AIUsageRecord.Stage.TRANSCRIPTION, pipeline_version, exc, identity, started
        )
        raise
    if gate is not None:
        gate.settle(reservation, _audio_usage(capacity_model, result.usage))

    tx = result.data
    segments = [{'start': w.start, 'end': w.end, 'text': w.text} for w in tx.words] or [
        {'start': s.start, 'end': s.end, 'text': s.text} for s in tx.segments
    ]

    def persist():
        # Persist the transcript and advance together: selection can fail and retry
        # without ever paying for transcription again.
        with transaction.atomic():
            _merge_metadata(project_id, {
                'full_text': tx.full_text,
                'transcription_usage': result.usage.model_dump(),
            }, expected_stage=PipelineStage.AUDIO_EXTRACTED)
            VideoProject.objects.filter(pk=project_id).update(transcript_data=segments)
            advance(project_id, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED)

    _persist_then_record_usage(
        project_id, AIUsageRecord.Stage.TRANSCRIPTION, pipeline_version,
        usages_for_success(result), persist,
    )


def _moderation_payload(project):
    """
    Everything that reaches the selection LLM and is not ours: the user's own
    text (title, and the selection criteria once PTE-02 exists) and the transcript.
    """
    metadata = project.metadata or {}
    transcript = metadata.get('full_text') or " ".join(
        str(item.get('text', '')) for item in (project.transcript_data or [])
    )
    parts = [project.title or "", str(metadata.get('selection_criteria') or ""), transcript]
    return "\n".join(part for part in parts if part and part.strip())


def _stage_moderate(project_id):
    """
    ADR-011 D-M2: moderate before paying for the selection LLM. Blocked content
    raises UnsafeContentError and a missing verdict raises a ModerationError
    (retryable or not); both reach the pipeline handler, which fails the project
    and refunds it once no retry is left. A ModerationOverride lets it through.
    Only the outcome and latency are stored, never text.
    """
    from apps.core.security import AI_Security_Shield
    from apps.videos.models import ModerationOverride

    project = VideoProject.objects.get(pk=project_id)
    started = time.monotonic()

    if ModerationOverride.objects.filter(project_id=project_id).exists():
        outcome = 'overridden'
        logger.warning(
            "pipeline.moderation_overridden project_id=%s", project_id,
            extra={'project_id': str(project_id)},
        )
    else:
        try:
            AI_Security_Shield.check_content_safety(_moderation_payload(project))
        except Exception as exc:
            try:
                _merge_metadata(
                    project_id, _moderation_outcome(type(exc).__name__, started),
                    expected_stage=PipelineStage.TRANSCRIBED,
                )
            except (DatabaseError, PipelineStateError) as record_exc:
                # Recording the outcome must never mask the moderation failure.
                logger.error(
                    "pipeline.moderation_outcome_not_recorded project_id=%s error=%s",
                    project_id, type(record_exc).__name__,
                )
            raise
        outcome = 'passed'

    with transaction.atomic():
        _merge_metadata(
            project_id, _moderation_outcome(outcome, started),
            expected_stage=PipelineStage.TRANSCRIBED,
        )
        advance(project_id, PipelineStage.TRANSCRIBED, PipelineStage.MODERATED)


def _moderation_outcome(outcome, started):
    """D-M7 metrics stored on the project: outcome (or error class) and latency, no text."""
    return {'moderation': {
        'outcome': outcome,
        'latency_s': round(time.monotonic() - started, 3),
        'checked_at': timezone.now().isoformat(),
    }}


def _is_moderation_failure(exc):
    from apps.core.security import ModerationError, UnsafeContentError

    return isinstance(exc, (ModerationError, UnsafeContentError))


def _refund_ingestion_reservation(project_id):
    """
    Returns the tokens reserved when the project was created (``metadata
    ['reservation_id']``, set by the create view) if the project ended FAILED
    without delivering anything: a project that already has clips (suggested,
    rendered or both) keeps its reservation. Idempotent and never raises: a
    missing or already settled reservation is only logged, and payment errors
    never mask the original failure.
    """
    from django.core.exceptions import ValidationError

    # Deferred: wallet_service imports this module at load time.
    from apps.payments.services.wallet_service import PaymentError, rollback_reservation

    try:
        project = VideoProject.objects.get(pk=project_id)
        if project.pipeline_stage != PipelineStage.FAILED:
            return
        if VideoClip.objects.filter(project_id=project_id).exists():
            logger.info("pipeline.refund_skipped project_id=%s reason=has_clips", project_id)
            return
        reservation_id = (project.metadata or {}).get('reservation_id')
        if not reservation_id:
            logger.warning("pipeline.refund_skipped project_id=%s reason=no_reservation", project_id)
            return
        if not Transaction.objects.filter(
            pk=reservation_id, status=Transaction.Status.RESERVED
        ).exists():
            logger.info("pipeline.refund_skipped project_id=%s reason=not_reserved", project_id)
            return
        rollback_reservation(reservation_id)
        logger.info(
            "pipeline.refunded project_id=%s", project_id, extra={'project_id': str(project_id)}
        )
    except (PaymentError, DatabaseError, VideoProject.DoesNotExist, ValidationError) as exc:
        logger.error(
            "pipeline.refund_failed project_id=%s error=%s", project_id, type(exc).__name__
        )


def _stage_select_clips(project_id):
    from apps.videos.services.ai.llm_options import reasoning_effort_setting, reasoning_kwargs
    from apps.videos.services.ai.rollout import v2_enabled_for

    project = VideoProject.objects.get(pk=project_id)
    segments = project.transcript_data or []
    metadata = project.metadata or {}

    duration = metadata.get('duration')
    if duration is None:
        # Rows created before pipeline state may lack the probe result.
        duration, resolution = _probe_video(project.source_file.path)
        _merge_metadata(
            project_id, {'duration': duration, 'resolution': resolution},
            expected_stage=PipelineStage.MODERATED,
        )
        metadata = {**metadata, 'duration': duration, 'resolution': resolution}

    selection_kwargs = dict(
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
        # User preferences were stored but never reached the selector.
        editing_style=project.editing_style,
        max_clips=metadata.get('max_clips'),
        selection_criteria=metadata.get('selection_criteria'),
    )
    started = time.monotonic()
    v2_enabled = v2_enabled_for(project)
    # The level actually sent to the primary model ('default' = none sent: no
    # setting, or a model that cannot take it), for the AICORE-14 comparison.
    selection_effort = reasoning_kwargs(
        _selection_models(v2_enabled)[0], reasoning_effort_setting(),
    ).get('reasoning_effort') or 'default'
    if v2_enabled:
        # Fixed by the branch that actually executes; never re-read afterwards.
        pipeline_version = AIUsageRecord.PipelineVersion.V2
        identity = _llm_identity(getattr(settings, 'AI_DEFAULT_LLM_MODEL', 'unknown'))
        gate, reservation, capacity_models = _reserve_selection(project_id, True, duration)
        try:
            result = SelectionEngine.select_viral_clips_detailed(**selection_kwargs)
        except Exception as exc:
            if gate is not None:
                gate.settle(reservation, _selection_failure_usage(capacity_models, exc))
            _record_call_failure(
                project_id, AIUsageRecord.Stage.SELECTION, pipeline_version, exc, identity, started
            )
            raise
        if gate is not None:
            gate.settle(
                reservation, _llm_usage_by_model(capacity_models, result.attempts or [result.usage])
            )
        suggestions = [clip.model_dump(include=_CLIP_SUGGESTION_FIELDS) for clip in result.data.clips]
        usage = result.usage
        usage_rows = usages_for_success(result)
    else:
        pipeline_version = AIUsageRecord.PipelineVersion.LEGACY
        identity = _llm_identity(GeminiFlashStrategy().get_model_name())
        # The legacy call reports no tokens: the reservation stands as the estimate.
        _reserve_selection(project_id, False, duration)
        # Kill switch off: the legacy selector stays in charge (the V2 path always
        # goes through LiteLLM). Usage is synthetic so persistence is unchanged.
        try:
            # The rollout already chose legacy for this project: never re-read the master switch.
            legacy_clips = SelectionEngine.select_viral_clips(**selection_kwargs, v2_enabled=False)
        except Exception as exc:
            _record_call_failure(
                project_id, AIUsageRecord.Stage.SELECTION, pipeline_version, exc, identity, started
            )
            raise
        suggestions = [
            {
                'start': clip['start'],
                'end': clip['end'],
                'title': clip['title'],
                'virality_score': clip.get('virality_score', 0),
                'reasoning': clip.get('reasoning', ''),
            }
            for clip in legacy_clips
        ]
        usage = ProviderUsage(provider='legacy', model='legacy', estimated_cost_usd=0.0)
        # The legacy strategy is a real paid LLM call whose tokens are not captured,
        # so its cost is UNKNOWN (None), never the false zero above (which only
        # feeds the metadata UI cache).
        usage_rows = [ProviderUsage(
            provider=identity[0], model=identity[1],
            duration_seconds=round(time.monotonic() - started, 3),
            estimated_cost_usd=None,
        )]

    def persist():
        with transaction.atomic():
            VideoProject.objects.select_for_update().get(pk=project_id)
            # Dedupe: clips from a previous attempt are reused, never duplicated.
            if not VideoClip.objects.filter(project_id=project_id).exists():
                if not suggestions:
                    # Never advance to CLIPS_SELECTED without clips: the project
                    # would wait for an approval of nothing, or fail at dispatch
                    # and resume past selection forever. The usage rows are still
                    # written by _persist_then_record_usage (the call was billed).
                    raise AIEmptySelectionError(
                        "Selection produced no clips.", provider="pipeline"
                    )
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
            _merge_metadata(project_id, {
                'selection_usage': usage.model_dump(),
                # The reasoning level this selection ran with, for the AICORE-14
                # comparison in the pilot export ('default' = provider default).
                'selection_reasoning_effort': selection_effort,
            }, expected_stage=PipelineStage.MODERATED)
            advance(project_id, PipelineStage.MODERATED, PipelineStage.CLIPS_SELECTED)

    _persist_then_record_usage(
        project_id, AIUsageRecord.Stage.SELECTION, pipeline_version, usage_rows, persist
    )


def _stage_dispatch_render(project_id, progress=None):
    """
    auto_render_bypass: one render task per clip, queued only AFTER the transition
    commits. Rendering never runs inside this transaction. If the worker dies or
    ``.delay()`` fails after the commit, the project is left at RENDER_DISPATCHED
    with its clips still DRAFT and ``_resume_render_dispatch`` queues them again.
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
        if progress is not None:
            # The stage is ours now: a failure past this point must be recorded against it.
            progress['stage'] = PipelineStage.RENDER_DISPATCHED
        VideoProject.objects.filter(pk=project_id).update(status=VideoProject.Status.RENDERING)
        for clip_id in clip_ids:
            transaction.on_commit(lambda clip_id=clip_id: render_clip_task.delay(str(clip_id)))


def _resume_render_dispatch(project_id):
    """
    Project is at RENDER_DISPATCHED: (re)queue every clip still DRAFT. Duplicates
    are harmless: render_clip_task claims the clip with a CAS and skips COMPLETED
    ones. With nothing left to queue, closes the fan-out if every clip finished.
    """
    clip_ids = list(
        VideoClip.objects.filter(project_id=project_id, status=VideoClip.Status.DRAFT)
        .values_list('id', flat=True)
    )
    if not clip_ids:
        finalize_project_render(project_id)
        return f"Render already dispatched for {project_id}"

    failure = None
    for clip_id in clip_ids:
        try:
            render_clip_task.delay(str(clip_id))
        except Exception as exc:
            logger.error(
                "render.dispatch_failed project_id=%s clip_id=%s error=%s",
                project_id, clip_id, type(exc).__name__,
            )
            failure = failure or exc
    if failure is not None:
        raise failure

    logger.info(
        "pipeline.render_redispatched project_id=%s clips=%d", project_id, len(clip_ids),
        extra={'project_id': str(project_id)},
    )
    return f"Render re-dispatched for {project_id}: {len(clip_ids)} clips"


def _run_ingestion(project_id, attempt, progress=None, lock=None, parse_retries=0):
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

    if stage in TERMINAL_STAGES:
        return f"Nothing to do for {project_id}: already {stage}"

    if stage == PipelineStage.RENDER_DISPATCHED:
        renew_lock()
        return _resume_render_dispatch(project_id)

    # Prompt-to-Edit (PTE-01b): parse and apply once, before any stage reads the
    # project settings (idempotent; a project without a prompt is untouched).
    if stage != PipelineStage.CLIPS_SELECTED:
        from apps.videos.services.ai.prompt_validator import apply_prompt_to_project

        # The parser's LLM call waits for capacity under the stage about to run.
        upcoming = STAGE_ORDER[min(STAGE_ORDER.index(stage) + 1, len(STAGE_ORDER) - 1)] \
            if stage in STAGE_ORDER else stage
        try:
            apply_prompt_to_project(
                project_id,
                # Transient parser errors retry on their own budget (PromptParseRetry).
                can_retry=parse_retries < PTE_PARSE_MAX_RETRIES,
                capacity=_PromptParseCapacity(project_id, upcoming),
            )
        except CapacityUnavailable as exc:
            if _prompt_capacity_exhausted(project_id, exc):
                # The prompt is optional: past the maximum wait the project goes
                # on with its settings instead of failing.
                from apps.videos.services.ai.prompt_validator import give_up_prompt

                give_up_prompt(project_id, 'CapacityTimeoutError')
            else:
                try:
                    set_stage_status(project_id, upcoming, StageStatus.PENDING_CAPACITY, expected_stage=stage)
                except PipelineStateError:
                    logger.warning("pipeline.status_write_skipped project_id=%s stage=%s", project_id, upcoming)
                raise

    # Rows without recorded state may already hold a transcript or clips.
    inferred = infer_completed_stage(project)
    if STAGE_ORDER.index(inferred) > STAGE_ORDER.index(stage):
        stage = advance_to(project_id, stage, inferred)
        progress['stage'] = stage

    if stage in (
        PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED,
        PipelineStage.TRANSCRIBED, PipelineStage.MODERATED,
    ):
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
        with _running_stage(project_id, PipelineStage.MODERATED, attempt):
            _stage_moderate(project_id)
        stage = PipelineStage.MODERATED
        progress['stage'] = stage

    if stage == PipelineStage.MODERATED:
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
                _stage_dispatch_render(project_id, progress)
        else:
            logger.info("⏳ Ingestion complete. Awaiting user approval.")
            # Guarded: a redelivered task must not undo a later user action.
            VideoProject.objects.filter(
                pk=project_id,
                status__in=[VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING],
            ).update(status=VideoProject.Status.AWAITING_APPROVAL)

    return f"Ingestion Success {project_id}"


def _work_already_done(project_id):
    """The project reached clips_selected (or later): its main work is delivered."""
    current = VideoProject.objects.filter(pk=project_id).values_list('pipeline_stage', flat=True).first()
    if current in TERMINAL_STAGES:
        return True
    return current in STAGE_ORDER and STAGE_ORDER.index(current) >= STAGE_ORDER.index(PipelineStage.CLIPS_SELECTED)


def _record_pipeline_failure(project_id, exc, attempt, final, stage=None):
    logger.error(
        "pipeline.failed project_id=%s attempt=%d final=%s error=%s",
        project_id, attempt, final, type(exc).__name__,
        extra={'project_id': str(project_id), 'attempt': attempt},
    )
    failed_now = False
    try:
        VideoProject.objects.filter(pk=project_id).update(
            pipeline_attempts=F('pipeline_attempts') + 1
        )
        record_error(project_id, type(exc).__name__)
        if final and stage is None and _work_already_done(project_id):
            # Failed before this run read the project (e.g. taking the lock of a
            # redelivered message): a project that already has its clips is not
            # failed because of it.
            logger.warning("pipeline.fail_skipped project_id=%s reason=work_done", project_id)
        elif final:
            failed_now = mark_failed(project_id, expected_stage=stage)
    except (DatabaseError, VideoProject.DoesNotExist) as record_exc:
        logger.error(
            "pipeline.failure_not_recorded project_id=%s error=%s",
            project_id, type(record_exc).__name__,
        )
        return
    # A project that ends FAILED without clips gave the user nothing, whatever
    # the reason (moderation, ADR-011 D-M5; capacity timeout, ADR-012 D-C4; a
    # provider or pipeline error): the reservation goes back. Only the call that
    # failed the project refunds it, so a project another worker moved on keeps it.
    if failed_now:
        _refund_ingestion_reservation(project_id)


# acks_late + reject_on_worker_lost: if the worker dies mid-task (OOM during
# MoviePy/Whisper) the message is redelivered instead of silently lost. Running
# the task twice is safe because every stage is idempotent: transitions are
# compare-and-set, the Redis lock keeps two workers off the same project, and
# stage outputs (transcript, clips) are persisted and reused, never recomputed.
# The soft time limit (settings.PIPELINE_SOFT_TIME_LIMIT) raises
# SoftTimeLimitExceeded, which is non-retryable: the project fails with that
# code and the lock is released instead of being renewed by a hung worker.
@celery_app.task(
    bind=True,
    max_retries=PIPELINE_MAX_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=getattr(settings, 'PIPELINE_SOFT_TIME_LIMIT', 2 * 3600),
    time_limit=getattr(settings, 'PIPELINE_TIME_LIMIT', 2 * 3600 + 300),
)
def process_initial_ingestion(self, project_id, lock_retries=0, parse_retries=0):
    """
    Multimodal Ingestion Pipeline V2 (Paper Edit V1 + Video Sync), recoverable.
    Retryable errors back off exponentially (TD-04: replaces the fixed 60s
    countdown); non-retryable errors fail the project immediately.

    Waiting for a busy project lock has its own counter (``lock_retries``,
    AICORE-18): lock contention never spends the retries meant for errors.
    """
    from apps.videos.services.ai.prompt_validator import PromptParseRetry

    attempt = self.request.retries + 1
    logger.info(
        "pipeline.started project_id=%s attempt=%d", project_id, attempt,
        extra={'project_id': str(project_id), 'attempt': attempt},
    )
    progress = {}
    try:
        with project_lock(project_id) as lock:
            return _run_ingestion(project_id, attempt, progress, lock, parse_retries)
    except PromptParseRetry:
        # Re-queued, not self.retry(): the stages keep their whole retry budget.
        countdown = PTE_PARSE_RETRY_SECONDS[min(parse_retries, len(PTE_PARSE_RETRY_SECONDS) - 1)]
        logger.warning(
            "prompt_parse.retrying project_id=%s parse_retry=%d countdown_s=%d",
            project_id, parse_retries + 1, countdown,
        )
        process_initial_ingestion.apply_async(
            args=[str(project_id)],
            kwargs={'lock_retries': lock_retries, 'parse_retries': parse_retries + 1},
            countdown=countdown,
            retries=self.request.retries,
        )
        return f"Prompt parse retry queued for {project_id}"
    except ProjectLockedError as exc:
        if lock_retries < PIPELINE_LOCK_MAX_RETRIES:
            logger.warning(
                "pipeline.lock_busy_retrying project_id=%s attempt=%d lock_retry=%d",
                project_id, attempt, lock_retries + 1,
                extra={'project_id': str(project_id), 'attempt': attempt},
            )
            # Re-queued, not self.retry(): the error retry count travels unchanged.
            process_initial_ingestion.apply_async(
                args=[str(project_id)],
                kwargs={'lock_retries': lock_retries + 1},
                countdown=PIPELINE_LOCK_RETRY_SECONDS,
                retries=self.request.retries,
            )
            return f"Lock busy, re-queued {project_id}"
        # Still held after outlasting the TTL: a live worker owns the project.
        logger.warning(
            "pipeline.skipped project_id=%s attempt=%d reason=%s",
            project_id, attempt, type(exc).__name__,
            extra={'project_id': str(project_id), 'attempt': attempt},
        )
        return f"Skipped {project_id}: {exc}"
    except (ProjectLockLostError, PipelineConflictError) as exc:
        logger.warning(
            "pipeline.skipped project_id=%s attempt=%d reason=%s",
            project_id, attempt, type(exc).__name__,
            extra={'project_id': str(project_id), 'attempt': attempt},
        )
        return f"Skipped {project_id}: {exc}"
    except CapacityUnavailable as exc:
        # The lock is already released here: the re-queued task can take it.
        try:
            return _wait_for_capacity(
                project_id, exc, attempt, progress.get('stage'), self.request.retries
            )
        except Exception as wait_exc:
            # Parking failed (e.g. the database): handled like any stage error,
            # never left in pending_capacity with no message in the queue.
            will_retry = (
                _is_retryable_pipeline_error(wait_exc) and self.request.retries < self.max_retries
            )
            _record_pipeline_failure(
                project_id, wait_exc, attempt, final=not will_retry, stage=progress.get('stage')
            )
            if will_retry:
                raise self.retry(exc=wait_exc, countdown=_retry_countdown(self.request.retries))
            raise
    except Exception as exc:
        will_retry = _is_retryable_pipeline_error(exc) and self.request.retries < self.max_retries
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
