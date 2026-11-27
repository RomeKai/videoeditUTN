"""
Social publishing and AI social metadata (ADR-013).
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
from django.db.models import Case, F, JSONField, Q, TextField, Value, When
from django.core.cache import caches
from apps.videos.models import VideoProject, VideoClip, ScheduledPost
from backend.celery import app as celery_app

from apps.videos.tasks.shared import (
    PostConfigurationError,
    _capacity_countdown,
    _capacity_gate,
    _is_retryable_pipeline_error,
    _llm_identity,
    _llm_usage_by_model,
    _record_call_failure,
    _retry_countdown,
    _selection_failure_usage,
    _selection_models,
)

logger = logging.getLogger(__name__)

@celery_app.task(name='apps.videos.tasks.dispatch_scheduled_posts_batch')
def dispatch_scheduled_posts_batch():
    """
    Orchestrator: dispatches the SCHEDULED posts due within the next hour, and
    those already overdue (the beat or the worker was down), which go out at once.
    Runs every 5 minutes via Celery Beat (CELERY_BEAT_SCHEDULE).
    """
    now = timezone.now()
    window_end = now + timedelta(hours=1)
    
    # Batch query: everything due by the end of the window, overdue posts included
    scheduled_posts = ScheduledPost.objects.filter(
        status=ScheduledPost.Status.SCHEDULED,
        publish_at__lte=window_end
    )
    
    count = 0
    for post in scheduled_posts:
        with transaction.atomic():
            # Lock the row for update and check status again to prevent race conditions
            post_to_queue = ScheduledPost.objects.select_for_update().filter(
                id=post.id, status=ScheduledPost.Status.SCHEDULED, publish_at__lte=window_end,
            ).first()
            if post_to_queue is None:
                continue
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

@celery_app.task(name='apps.videos.tasks.upload_to_social_network', bind=True, max_retries=3)
def upload_to_social_network(self, post_id):
    """
    Worker: publishes one post through the workspace's provider
    (apps.integrations.social, today Ayrshare).
    Lifecycle:
    1. Lock (Redis): one attempt at a time per post.
    2. Under the post's row lock: only a QUEUED or PROCESSING post goes on.
       Without the workspace's profile, or a TikTok post without its options,
       it fails here with a code, and nothing is sent.
    3. The clip's presigned URL, the text (the user's, or else the AI's) and
       the provider call, with the post's idempotency key.
    4. A retryable error backs off (60, 120, 240 s); a permanent one, or an
       unknown outcome, fails the post with a code and is never retried.
    """
    from apps.integrations import social
    from apps.integrations.social import errors as codes
    from apps.videos.services.scheduled_posts import LONGEST_RETRY_COUNTDOWN
    from apps.videos.services.seo_engine import compose_caption
    from apps.videos.services.storage_service import CloudflareR2Manager

    lock_id = f"lock_post_publish_{post_id}"
    # The cache that raises (apps/core/cache.py): with Redis down the task fails
    # instead of publishing without the lock. Outlives the slowest provider call
    # (read timeout) plus the rest of the task, so a second delivery cannot
    # start while the first one is still sending.
    locks = caches['locks']
    try:
        acquired = locks.add(lock_id, "locked", social.PUBLISH_LOCK_TTL_SECONDS)
    except Exception as exc:
        # Redis did not answer: never published without the lock. Retried with
        # backoff; out of retries the post fails, instead of staying QUEUED
        # with no task behind it (the dispatcher only picks SCHEDULED).
        attempts = self.request.retries + 1
        logger.warning(
            "publish.lock_unavailable post_id=%s attempt=%d error=%s", post_id, attempts, type(exc).__name__,
        )
        if self.request.retries >= self.max_retries:
            ScheduledPost.objects.filter(id=post_id).update(
                status=ScheduledPost.Status.FAILED,
                error_log=f"Lock unavailable after {attempts} attempts: {type(exc).__name__}",
            )
            raise
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    if not acquired:
        logger.warning(f"🔒 [WORKER] Aborting post {post_id}: Already running.")
        return "Locked"

    try:
        with transaction.atomic():
            try:
                post = ScheduledPost.objects.select_for_update().get(id=post_id)
            except ScheduledPost.DoesNotExist:
                # Deleted after it was queued (with its clip, its project or its
                # workspace): nothing to publish, and nothing to mark as failed.
                logger.warning("publish.post_deleted post_id=%s when=before_publishing", post_id)
                return "Post Deleted"
            if post.status == ScheduledPost.Status.PUBLISHED:
                return "Already Published"
            if post.status not in (ScheduledPost.Status.QUEUED, ScheduledPost.Status.PROCESSING):
                # It moved on after this message was queued (the user retried it and the beat
                # queues it again, its AI text failed): a late copy never publishes it.
                logger.warning("publish.skipped post_id=%s status=%s", post_id, post.status)
                return "Skipped"

            clip = post.video_clip
            project = clip.project
            profile = social.ready_profile(project.workspace_id)
            failure, tiktok = None, None
            if profile is None:
                failure = social.PermanentError(codes.ACCOUNT_NOT_CONNECTED, 'no profile')
            elif post.platform == ScheduledPost.Platform.TIKTOK:
                try:
                    tiktok = social.TikTokOptions.from_post(post.tiktok)
                except social.PermanentError as exc:
                    failure = exc
            if failure is not None:
                post.status = ScheduledPost.Status.FAILED
                post.error_log = str(failure)
                # updated_at too: auto_now only reaches the database when it is listed,
                # and the project's activity dates the failure with it.
                post.save(update_fields=['status', 'error_log', 'updated_at'])
                # Committed on exit of the atomic block; raised after so the task is not reported as SUCCESS.
            else:
                post.status = ScheduledPost.Status.PROCESSING
                post.save(update_fields=['status', 'updated_at'])
            key = social.idempotency_key(post.pk, post.publish_key_version)

        if failure is not None:
            logger.error("publish.not_sent post_id=%s code=%s", post_id, failure.code)
            raise PostConfigurationError(failure.code)

        # 1. Presigned URL (valid for 1 hour): the provider downloads the video from it.
        s3_key = clip.s3_object_key
        if not s3_key:
            raise Exception("No s3_object_key found for the video clip.")
        media_url = CloudflareR2Manager.generate_presigned_url(s3_key, expiration_seconds=3600)

        # 2. What goes out: the API's caption and hashtags (the user's, or else the AI's, each on
        # its own: ScheduledPost.caption_to_publish), composed once here. Empty if there is none.
        provider = social.provider_for(profile)
        request = social.PublishRequest(
            platform=post.platform,
            media_url=media_url,
            text=compose_caption(post.caption_to_publish(), post.hashtags_to_publish()),
            # YouTube needs a title: the clip's.
            title=clip.title or project.title or '',
            idempotency_key=key,
            tiktok=tiktok,
        )
        result = provider.publish(profile, request)

        # 3. Out. A TikTok video the network still processes has no link yet (`post_url` null).
        post.status = ScheduledPost.Status.PUBLISHED
        post.post_url = result.post_url
        post.external_id = result.external_id
        post.error_log = None
        try:
            # Only these columns, and never an INSERT: a full save() of a row deleted
            # while the provider answered tries to insert it again and breaks on its clip.
            # In a savepoint: the error of a row that is gone must not break an outer transaction.
            with transaction.atomic():
                post.save(update_fields=['status', 'post_url', 'external_id', 'error_log', 'updated_at'])
        except DatabaseError:
            if ScheduledPost.objects.filter(id=post_id).exists():
                raise
            # Published, and its row went meanwhile: there is nowhere to record it.
            logger.warning("publish.post_deleted post_id=%s when=after_publishing", post_id)
            return f"Published via {provider.display_name}: {post.platform} (post deleted)"
        logger.info("publish.published post_id=%s provider=%s pending=%s", post_id, provider.name, result.pending)
        return f"Published via {provider.display_name}: {post.platform}"

    except social.RetryableError as exc:
        # Nothing went out (or the provider refuses this key if it did): backoff, the same key,
        # unless the provider answered for certain that nothing went out (`key_spent`).
        attempts = self.request.retries + 1
        logger.warning(
            "publish.retryable post_id=%s attempt=%d code=%s detail=%s", post_id, attempts, exc.code, exc.detail,
        )
        # Updated by id: `post` may not be bound if the failure happened before the fetch.
        changes = {'retry_count': F('retry_count') + 1}
        if exc.key_spent:
            changes['publish_key_version'] = F('publish_key_version') + 1
        if self.request.retries >= self.max_retries:
            # Out of retries: FAILED, never left in PROCESSING with no task behind it.
            ScheduledPost.objects.filter(id=post_id).update(
                status=ScheduledPost.Status.FAILED,
                error_log=f"{codes.PROVIDER_UNAVAILABLE} (after {attempts} attempts; {exc.detail})",
                **changes,
            )
            raise
        # "Retry n: ..." tells the API that a retry is on its way (services/scheduled_posts.stuck_after).
        ScheduledPost.objects.filter(id=post_id).update(error_log=f"Retry {attempts}: {exc}", **changes)
        # Backoff: 60s, 120s, 240s (TD-04), or longer if the provider asked, up to the longest one.
        countdown = max(
            60 * (2 ** self.request.retries),
            min(exc.retry_after or 0, int(LONGEST_RETRY_COUNTDOWN.total_seconds())),
        )
        raise self.retry(exc=exc, countdown=countdown)

    except social.OutcomeUnknownError as exc:
        # Never retried: the post may already be public. A person checks and retries.
        logger.error("publish.outcome_unknown post_id=%s detail=%s", post_id, exc.detail)
        ScheduledPost.objects.filter(id=post_id).update(
            status=ScheduledPost.Status.FAILED,
            error_log=str(exc),
        )
        raise

    except social.PermanentError as exc:
        code = exc.code
        if code == codes.PROVIDER_REJECTED and self.request.retries > 0:
            # An earlier attempt with this same key may have reached the provider, and a
            # repeated key is refused with a code Ayrshare does not document: it may be out.
            code = codes.OUTCOME_UNKNOWN
        logger.error("publish.rejected post_id=%s code=%s detail=%s", post_id, code, exc.detail)
        ScheduledPost.objects.filter(id=post_id).update(
            status=ScheduledPost.Status.FAILED,
            retry_count=F('retry_count') + 1,
            error_log=f"{code} ({exc.detail})" if exc.detail else code,
        )
        raise

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
        try:
            locks.delete(lock_id)
        except Exception as exc:
            # Its TTL frees it; the error must not hide the task's own outcome.
            logger.warning("publish.lock_release_failed post_id=%s error=%s", post_id, type(exc).__name__)

# Social metadata (AICORE-17, ADR-013). One generation at a time per post, and
# the last generation kept until its output moderation passes, so a moderation
# retry never pays for a second generation.
SEO_LOCK_TTL_SECONDS = 600
SEO_PENDING_TTL_SECONDS = 3600


@celery_app.task(name='apps.videos.tasks.process_video_seo', bind=True, max_retries=3)
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

    except ScheduledPost.DoesNotExist:
        # Deleted before or while it was generated (a discarded draft, a cancelled
        # post): nothing to write, and not a failure of the task.
        logger.info("seo.skipped post_id=%s reason=deleted", post_id)
        return "Post Deleted"

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
    its status: it can still be published. A DRAFT stays a DRAFT with the code
    (the API shows `seo_status: "failed"`): it was not going out, and the user
    writes the text and sends it. A post with the user's own caption keeps its
    status too: it does not need the AI's. Either way the API request is over
    (`seo_requested_at`).
    """
    posts = ScheduledPost.objects.filter(id=post_id).exclude(status__in=_SEO_FROZEN_STATUSES)
    if posts.filter(generated_caption__gt='', generated_hashtags__isnull=False).exists():
        logger.warning("seo.regenerate_failed post_id=%s code=%s", post_id, code)
        posts.update(seo_requested_at=None)
        return
    keeps = Q(status=ScheduledPost.Status.DRAFT) | Q(caption__gt='')
    posts.update(
        status=Case(When(keeps, then=F('status')), default=Value(ScheduledPost.Status.FAILED)),
        error_log=code,
        seo_requested_at=None,
    )


def _recover_seo_failed_post(post_id):
    """
    A post an earlier SEO failure left FAILED is recoverable (ADR-013): back to
    DRAFT, so the user reviews the caption and schedules it again. It is never
    re-scheduled on its own. A DRAFT that kept the failure loses it. Publishing
    failures are not touched.
    """
    seo_codes = (
        Q(error_log__in=_SEO_FAILURE_CODES)
        | Q(error_log__startswith='seo_')
        | Q(error_log__startswith='moderation_')
    )
    ScheduledPost.objects.filter(
        seo_codes, id=post_id, status__in=(ScheduledPost.Status.FAILED, ScheduledPost.Status.DRAFT),
    ).update(status=ScheduledPost.Status.DRAFT, error_log=None)


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

    fields = {'generated_caption': metadata.caption, 'generated_hashtags': metadata.hashtags, 'seo_requested_at': None}
    if regenerate:
        # "Regenerar" replaces the text the user wrote too (empty: the API shows the AI's), unless the
        # user saved that text after asking (`text_edited_at`, services/scheduled_posts.update_post):
        # then it stays. The Case reads the row before this update, so it sees the request this same
        # statement clears.
        asked = Q(text_edited_at__isnull=True) | Q(text_edited_at__lt=F('seo_requested_at'))
        fields.update(
            caption=Case(When(asked, then=Value('')), default=F('caption'), output_field=TextField()),
            hashtags=Case(When(asked, then=Value([], output_field=JSONField())), default=F('hashtags'), output_field=JSONField()),
        )
    stored = ScheduledPost.objects.filter(id=post.id).exclude(
        status__in=_SEO_FROZEN_STATUSES,
    ).update(**fields)
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
