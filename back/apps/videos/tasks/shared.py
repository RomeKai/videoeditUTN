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
