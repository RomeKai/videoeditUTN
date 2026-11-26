"""
Helpers shared by the task modules: download, retry policy, metadata writes,
usage rows and AI capacity (AICORE-11).
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

class UnsupportedLinkError(ValueError):
    """
    The link is not one video of the sites the app downloads from
    (services/link_validation.py, services/ytdlp_safe.py): another site, an
    internal address, a list, a channel or a live stream. Not retried.
    """


class LinkTooLongError(ValueError):
    """
    The linked video is longer than the plan allows (its
    ``max_video_duration_seconds``, below the app's limit): a plan with
    longer videos takes it. Not retried.
    """


class LinkTooLongForAppError(ValueError):
    """
    The linked video is longer than the app processes at all
    (AI_UPLOAD_MAX_DURATION_SECONDS, 3 h by default), whatever the plan: no
    plan takes it. Not retried.
    """


class LinkTooLargeError(ValueError):
    """The linked video's file is larger than LINK_DOWNLOAD_MAX_BYTES. Not retried."""


def _link_duration_limits(project):
    """``(app, plan)``: the app's AI_UPLOAD_MAX_DURATION_SECONDS and the plan's limit (None without one)."""
    app_limit = getattr(settings, 'AI_UPLOAD_MAX_DURATION_SECONDS', 10800)
    plan = getattr(project.workspace, 'subscription_plan', None)
    plan_limit = plan.max_video_duration_seconds if plan is not None and plan.max_video_duration_seconds else None
    return app_limit, plan_limit


def link_duration_limit(project):
    """
    Longest video a link project may download: the plan's
    ``max_video_duration_seconds`` and the app's AI_UPLOAD_MAX_DURATION_SECONDS,
    the lower of the two. The probe tells the user before creating; this makes
    it binding when the video is fetched.
    """
    app_limit, plan_limit = _link_duration_limits(project)
    return min(app_limit, plan_limit) if plan_limit else app_limit


def link_too_long_error(project):
    """
    The error of a link project's video longer than ``link_duration_limit``:
    LinkTooLongError when the plan's limit is the lower one (a plan with
    longer videos takes it), LinkTooLongForAppError when the app's is, or
    both are the same (no plan takes it).
    """
    app_limit, plan_limit = _link_duration_limits(project)
    return LinkTooLongError if plan_limit and plan_limit < app_limit else LinkTooLongForAppError


def download_from_youtube(url, output_folder, max_duration_seconds=None, too_long_error=LinkTooLongError):
    """
    Downloads the one video of ``url`` through ytdlp_safe: only the allowed
    sites' extractors, every hop checked again, one video that is not live,
    no request or redirect to a non-public address, at most
    LINK_DOWNLOAD_MAX_BYTES and, with ``max_duration_seconds``, nothing
    longer (refused before downloading with ``too_long_error``: the one of
    ``link_too_long_error``). Raises UnsupportedLinkError, LinkTooLongError
    or LinkTooLongForAppError, LinkTooLargeError or yt-dlp's DownloadError.
    """
    from apps.videos.services import ytdlp_safe
    from apps.videos.services.link_validation import LinkRejected, validate_video_link

    # Also for rows saved before the creation checked the link. A host that
    # does not resolve raises its OSError (retried); a refused one does not retry.
    try:
        url = validate_video_link(url, resolve=True).url
    except LinkRejected:
        raise UnsupportedLinkError("The link is not on an allowed site.") from None

    output_path = os.path.join(output_folder, f"{uuid.uuid4()}.mp4")

    def check_duration(info):
        duration = info.get('duration')
        if max_duration_seconds and isinstance(duration, (int, float)) and duration > max_duration_seconds:
            raise too_long_error("The linked video is longer than allowed.")

    try:
        return ytdlp_safe.download_video(
            url, output_path,
            max_filesize=getattr(settings, 'LINK_DOWNLOAD_MAX_BYTES', 4 * 1024 ** 3),
            check_info=check_duration,
        )
    except ytdlp_safe.LinkRefused:
        raise UnsupportedLinkError("The link is not one video of an allowed site.") from None
    except ytdlp_safe.LinkTooLarge:
        raise LinkTooLargeError("The linked video is larger than allowed.") from None
    finally:
        if not os.path.exists(output_path):
            # A refused or failed download leaves no parts behind.
            prefix = os.path.splitext(os.path.basename(output_path))[0]
            for name in os.listdir(output_folder):
                if name.startswith(prefix):
                    try:
                        os.remove(os.path.join(output_folder, name))
                    except OSError:
                        pass

class ThirdPartyAPIError(Exception):
    """Exception raised when a social media API fails."""
    pass

class PostConfigurationError(Exception):
    """Raised when a post cannot be published because the workspace is misconfigured."""
    pass

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
# worker's lock expire instead of dropping the job. Used by the ingestion and
# by the paper-edit render.
PIPELINE_LOCK_RETRY_SECONDS = 45
PIPELINE_LOCK_MAX_RETRIES = 8
# Pause before a delivery whose re-queue could not be published goes back to
# the broker (_publish_requeue).
REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS = 5


def _publish_requeue(task, publish, event, project_id, kind, undo=None):
    """
    Publishes a re-queue of the running delivery (``publish``, a callable):
    the lock was busy (``kind='lock'``), the prompt parser asked for another
    try (``'prompt_parse'``) or capacity is short (``'capacity'``). If the
    broker does not take it, this delivery must not end: the task acks late,
    and Celery acks a task that raised too (task_acks_on_failure_or_timeout),
    so the only message the project has would be lost. The options:

    - re-raise the error: acked as a failure, the message is lost;
    - ``self.retry``: publishes a new message as well, so it fails the same
      way, and Celery then raises ``Reject(requeue=False)``: dropped;
    - ``Reject(requeue=True)``: the broker gets this same message back (a
      Redis restore, at the end of the queue). If Redis cannot take even
      that, the message stays unacked and returns after the visibility
      timeout. Nothing is lost either way.

    So it pauses REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS, so that a broker that
    keeps failing gets one try every few seconds instead of a hot loop, and
    rejects with requeue. The message comes back unchanged, with the counters
    it carries (lock, parser and error retries): the re-queue that was not
    published spent none of them. ``undo`` reverts what the caller wrote for
    that re-queue (the capacity wait it planned), so the redelivery does not
    count it twice; a failing undo is only logged. In eager mode there is no
    broker, so an error here is the inline task's own and is raised as is.
    """
    from celery.exceptions import Reject

    try:
        publish()
    except Exception as exc:
        if getattr(task.request, 'is_eager', False):
            raise
        logger.error(
            "%s project_id=%s requeue=%s error=%s", event, project_id, kind, type(exc).__name__,
            extra={'project_id': str(project_id)},
        )
        if undo is not None:
            try:
                undo()
            except Exception as undo_exc:
                logger.error(
                    "%s project_id=%s requeue=%s undo_failed=%s",
                    event, project_id, kind, type(undo_exc).__name__,
                    extra={'project_id': str(project_id)},
                )
        time.sleep(REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS)
        # The class name only: the broker's message stays out of the task logs.
        raise Reject(type(exc).__name__, requeue=True) from None


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
    # ytdlp_safe refused a request or a redirect to a non-public address.
    'blocked non-public address',
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
        return _is_transient_client_error(exc)
    if _is_upload_failed_error(exc):
        return _is_transient_upload_failure(exc)
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


def _is_transient_client_error(exc):
    """An R2/S3 ClientError worth retrying: 5xx, 408, 429 or a throttling code."""
    error = exc.response.get('Error', {})
    http_status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0) or 0
    return (
        http_status >= 500
        or http_status in (408, 429)
        or error.get('Code') in _TRANSIENT_S3_ERROR_CODES
    )


def _is_upload_failed_error(exc):
    try:
        from boto3.exceptions import S3UploadFailedError
    except ImportError:  # pragma: no cover - boto3 is a hard dependency
        return False
    return isinstance(exc, S3UploadFailedError)


def _is_transient_upload_failure(exc):
    """
    boto3's ``upload_file`` turns every ClientError (throttling, 5xx) into an
    S3UploadFailedError raised inside its ``except``, so the original error is
    its ``__context__``: a deterministic 4xx (AccessDenied, NoSuchBucket) is
    not retried. Without that detail it retries, bounded by max_retries.
    """
    from botocore.exceptions import ClientError

    inner = exc.__cause__ or exc.__context__
    if isinstance(inner, ClientError):
        return _is_transient_client_error(inner)
    return True


def _is_transient_storage_connection_error(exc):
    """
    botocore's connection and transport failures talking to R2: endpoint
    unreachable, connection closed, read or connect timeout, TLS or proxy
    errors. Several of them also subclass OSError (through requests), so a
    caller that treats a plain OSError as deterministic must check this first.
    """
    try:
        from botocore.exceptions import ConnectionError as BotoConnectionError, HTTPClientError
    except ImportError:  # pragma: no cover - boto3 is a hard dependency
        return False
    return isinstance(exc, (BotoConnectionError, HTTPClientError))


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
