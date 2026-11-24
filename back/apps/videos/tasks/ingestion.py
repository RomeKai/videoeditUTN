"""
Ingestion pipeline (AICORE-7): recoverable, idempotent stages from upload to
clips selected, plus Prompt-to-Edit parsing (PTE-01b).
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
    AINoSpeechDetectedError,
    NonRetryableAIError,
    WholeVideoNotPaidError,
    WholeVideoSourceChangedError,
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
from decimal import ROUND_HALF_UP, Decimal
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
    PIPELINE_MAX_RETRIES,
    _audio_usage,
    _capacity_countdown,
    _capacity_gate,
    _capacity_granted,
    _is_current_wait,
    _is_retryable_pipeline_error,
    _llm_demand,
    _llm_identity,
    _llm_usage_by_model,
    _merge_metadata,
    _persist_then_record_usage,
    _record_call_failure,
    _retry_countdown,
    _selection_failure_usage,
    _selection_models,
    _transcription_demand,
    download_from_youtube,
)
from apps.videos.tasks.render import (
    finalize_project_render,
    render_clip_task,
)

logger = logging.getLogger(__name__)
# Prompt-to-Edit parser (PTE-01b): transient errors re-queue the ingestion with
# their own counter, so they never spend the retries the stages need. After
# these, the project goes on with the default settings.
PTE_PARSE_MAX_RETRIES = 2
PTE_PARSE_RETRY_SECONDS = (30, 90)

_CLIP_SUGGESTION_FIELDS = {'start', 'end', 'title', 'virality_score', 'reasoning'}


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
    # CORE-04: wall-clock time of each stage, logged on completion and failure
    # so a benchmark (or a dashboard, INFRA-02) can be built from the logs alone.
    started = time.monotonic()
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
        duration_s = round(time.monotonic() - started, 3)
        logger.error(
            "pipeline.stage_failed project_id=%s stage=%s attempt=%d duration_s=%.3f",
            project_id, stage, attempt, duration_s,
            extra={
                'project_id': str(project_id), 'stage': str(stage), 'attempt': attempt,
                'duration_s': duration_s,
            },
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
    duration_s = round(time.monotonic() - started, 3)
    logger.info(
        "pipeline.stage_completed project_id=%s stage=%s attempt=%d duration_s=%.3f",
        project_id, stage, attempt, duration_s,
        extra={
            'project_id': str(project_id), 'stage': str(stage), 'attempt': attempt,
            'duration_s': duration_s,
        },
    )


def _probe_video(source_path):
    with VideoFileClip(source_path) as clip:
        return clip.duration, list(clip.size)


def _generate_proxy(project, source_path, user_id):
    """Web proxy for the paper-edit UI. Optional: failures never block ingestion."""
    from apps.videos.services.storage_service import CloudflareR2Manager
    from apps.videos.utils.ffmpeg_utils import FFmpegManager

    # Unique per run: a redelivered proxy task may overlap a slow one, and two
    # FFmpeg processes must never write (or clean up) the same file.
    proxy_local_path = os.path.join(
        os.path.dirname(source_path), f"proxy_{project.id}_{uuid.uuid4().hex[:8]}.mp4"
    )
    started = time.monotonic()
    try:
        FFmpegManager.generate_web_proxy(source_path, proxy_local_path)
        proxy_r2_key = CloudflareR2Manager.upload_video(proxy_local_path, user_id, str(project.id))
        VideoProject.objects.filter(pk=project.id).update(proxy_r2_key=proxy_r2_key)
        duration_s = round(time.monotonic() - started, 3)
        logger.info(
            "pipeline.proxy_completed project_id=%s duration_s=%.3f", project.id, duration_s,
            extra={'project_id': str(project.id), 'duration_s': duration_s},
        )
    except Exception as proxy_exc:
        logger.error(
            "⚠️ Proxy generation failed but continuing ingestion project_id=%s error=%s",
            project.id, type(proxy_exc).__name__,
        )
    finally:
        FFmpegManager.cleanup_local_file(proxy_local_path)


@celery_app.task(name='apps.videos.tasks.generate_proxy_task', ignore_result=True)
def generate_proxy_task(project_id):
    """
    Builds the paper-edit web proxy outside the ingestion run (CORE-04), so the
    FFmpeg re-encode overlaps transcription instead of preceding it. Optional
    and idempotent: a project that already has a proxy, or no source, is skipped,
    and a failure only means the paper edit opens without a preview.
    """
    project = VideoProject.objects.select_related('uploaded_by').filter(pk=project_id).first()
    if project is None or project.proxy_r2_key or not project.source_file:
        return
    user_id = str(project.uploaded_by.id) if project.uploaded_by else "system"
    _generate_proxy(project, project.source_file.path, user_id)


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
        # CORE-04: the proxy (a full FFmpeg re-encode) runs in its own task, in
        # parallel with transcription, instead of delaying every later stage.
        try:
            generate_proxy_task.delay(str(project_id))
        except Exception as dispatch_exc:
            # The proxy is optional and must never fail the stage: if the task
            # cannot be queued (broker hiccup), build it inline as before.
            logger.warning(
                "pipeline.proxy_dispatch_failed project_id=%s error=%s",
                project_id, type(dispatch_exc).__name__,
            )
            _generate_proxy(project, source_path, user_id)

    duration, resolution = _probe_video(source_path)
    if _is_whole_video(project):
        # Before transcribing it: a file that is not what was priced costs nothing more.
        _check_whole_video_source(project, duration)
    _merge_metadata(
        project_id, {'duration': duration, 'resolution': resolution},
        expected_stage=PipelineStage.UPLOADED,
    )
    advance(project_id, PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED)


def _reserve_selection(project_id, v2_enabled, duration):
    gate = _capacity_gate()
    if gate is None:
        return None, None, []
    models = _selection_models(v2_enabled)
    reservation = gate.reserve(_llm_demand(models, duration), stage=PipelineStage.CLIPS_SELECTED)
    _capacity_granted(project_id, PipelineStage.CLIPS_SELECTED)
    return gate, reservation, models


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


def _wait_for_capacity(project_id, exc, attempt, owner_stage, retries, parse_retries=0):
    """
    Re-queues the ingestion after the capacity wait instead of blocking the
    worker. Not a failed attempt: pipeline_attempts and the usage rows are not
    touched. Fails with CapacityTimeoutError once the planned waiting for the
    stage would exceed AI_CAPACITY_MAX_WAIT_SECONDS.

    The Celery retry count travels with the re-queued message, so a provider
    call that keeps failing still runs out of retries (PIPELINE_MAX_RETRIES)
    even when capacity waits sit between its attempts. So does the prompt
    parser's own count (``parse_retries``): a capacity wait never refills it.
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
        args=[str(project_id)], kwargs={'parse_retries': parse_retries},
        countdown=countdown, retries=retries,
    )
    return f"Waiting for capacity: {project_id}"


def _stage_transcribe(project_id):
    from apps.videos.services.transcription_engine import TranscriptionBackend, TranscriptionEngine

    from apps.videos.services.ai.rollout import v2_enabled_for

    project = VideoProject.objects.get(pk=project_id)
    # Same size the worker preloads (WHISPER_PRELOAD), so the cached model is reused.
    model_size = getattr(settings, 'WHISPER_LOCAL_MODEL_SIZE', 'base')
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
        if isinstance(exc, AINoSpeechDetectedError) and _is_whole_video(project):
            # Music, gameplay, b-roll: a whole-video edit only needs the
            # transcript for its subtitles, so it renders without them.
            _persist_silent_transcript(project_id)
            return
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


def _persist_silent_transcript(project_id):
    with transaction.atomic():
        _merge_metadata(
            project_id, {'full_text': '', 'no_speech': True},
            expected_stage=PipelineStage.AUDIO_EXTRACTED,
        )
        VideoProject.objects.filter(pk=project_id).update(transcript_data=[])
        advance(project_id, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED)
    logger.info("pipeline.no_speech project_id=%s", project_id)


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
        from apps.videos.services.ai.usage_recorder import record_moderation_usage

        try:
            AI_Security_Shield.check_content_safety(_moderation_payload(project))
        except Exception as exc:
            record_moderation_usage(project, time.monotonic() - started, exc)
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
        latency = time.monotonic() - started

    with transaction.atomic():
        _merge_metadata(
            project_id, _moderation_outcome(outcome, started),
            expected_stage=PipelineStage.TRANSCRIBED,
        )
        advance(project_id, PipelineStage.TRANSCRIBED, PipelineStage.MODERATED)
    if outcome == 'passed':
        # After the stage is persisted: a metric never blocks the pipeline.
        record_moderation_usage(project, latency)


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
        clips = VideoClip.objects.filter(project_id=project_id)
        if _is_whole_video(project):
            # What it pays for is the render: refunded unless it was delivered
            # or is still being rendered.
            clips = clips.filter(status__in=[VideoClip.Status.COMPLETED, VideoClip.Status.RENDERING])
        if clips.exists():
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


def _commit_ingestion_reservation(project_id):
    """
    Charges the tokens reserved when the project was created (``metadata
    ['reservation_id']``) once its AI work is delivered: the clips are
    selected. Until then they stay reserved, so a failure can still refund
    them (``_refund_ingestion_reservation``). Idempotent and never raises: an
    already settled reservation is skipped, and a payment error is logged
    without stopping the pipeline (the reservation stays for a manual review).
    """
    from django.core.exceptions import ValidationError

    # Deferred: wallet_service imports this module at load time.
    from apps.payments.services.wallet_service import PaymentError, commit_reservation

    reservation_id = None
    try:
        reservation_id = (
            VideoProject.objects.filter(pk=project_id).values_list('metadata', flat=True).first() or {}
        ).get('reservation_id')
        if not reservation_id:
            logger.warning("pipeline.charge_skipped project_id=%s reason=no_reservation", project_id)
            return
        if not Transaction.objects.filter(
            pk=reservation_id, status=Transaction.Status.RESERVED
        ).exists():
            # Already charged, or refunded earlier (a re-dispatched project runs free).
            logger.info("pipeline.charge_skipped project_id=%s reason=not_reserved", project_id)
            return
        commit_reservation(reservation_id)
        logger.info(
            "pipeline.charged project_id=%s", project_id, extra={'project_id': str(project_id)}
        )
    except PaymentError as exc:
        # commit_reservation re-checks the status under a row lock: losing that
        # race to another settle is not a failure.
        if Transaction.objects.filter(pk=reservation_id).exclude(status=Transaction.Status.RESERVED).exists():
            logger.info("pipeline.charge_skipped project_id=%s reason=already_settled", project_id)
        else:
            logger.error(
                "pipeline.charge_failed project_id=%s error=%s", project_id, type(exc).__name__
            )
    except (DatabaseError, ValidationError) as exc:
        logger.error(
            "pipeline.charge_failed project_id=%s error=%s", project_id, type(exc).__name__
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


def _is_whole_video(project):
    """
    "Editar el video completo" (``project_type=single_edit``, screen D5): an
    uploaded file priced at creation for its whole duration (``metadata
    ['priced_duration_s']``, written by the create view). A single_edit row
    without it (a link, a project created before this flow, an admin edit) was
    never priced for a whole render, so it keeps the clips flow with review.
    """
    return (
        project.project_type == VideoProject.Type.SINGLE_EDIT
        and (project.metadata or {}).get('priced_duration_s') is not None
    )


def _whole_video_is_paid(project_id):
    """The creation reservation that pays a whole-video render is still RESERVED."""
    reservation_id = (
        VideoProject.objects.filter(pk=project_id).values_list('metadata', flat=True).first() or {}
    ).get('reservation_id')
    return bool(reservation_id) and Transaction.objects.filter(
        pk=reservation_id, status=Transaction.Status.RESERVED,
    ).exists()


def _ensure_whole_video_paid(project_id):
    """
    A whole-video render is paid by the creation reservation, charged only when
    the render is delivered (``_settle_whole_video_reservation``). If it was
    refunded (the project failed before), a re-dispatch must not render for free.
    """
    if not _whole_video_is_paid(project_id):
        raise WholeVideoNotPaidError()


def _whole_video_render_allowed(project_id):
    """
    ``render_clip_task`` asks before claiming the clip (and again after): a
    paid render runs only while the project is at render_dispatched with the
    reservation that pays it still reserved. That is the creation reservation
    of a whole-video edit, or the auto-render reservation of the selected clips
    ("Renderizar sin revisar"). A render message left in the queue after the
    project failed (and was refunded) must not deliver for free.
    """
    project = VideoProject.objects.filter(pk=project_id).first()
    if project is None:
        return True
    if _is_whole_video(project):
        return project.pipeline_stage == PipelineStage.RENDER_DISPATCHED and _whole_video_is_paid(project_id)
    auto_render_id = (project.metadata or {}).get(AUTO_RENDER_RESERVATION_KEY)
    if auto_render_id:
        return project.pipeline_stage == PipelineStage.RENDER_DISPATCHED and Transaction.objects.filter(
            pk=auto_render_id, status=Transaction.Status.RESERVED,
        ).exists()
    return True


# "Renderizar sin revisar" (auto_render_bypass): the render of the selected
# clips, reserved before they are queued and settled by what was delivered.
AUTO_RENDER_RESERVATION_KEY = 'auto_render_reservation_id'


def _reserve_auto_render(project_id, resuming=False):
    """
    The selected clips are rendered without the review, so their render is
    reserved here, priced like an approval (``render_cost`` of their seconds),
    before any render is queued. A redelivery reuses a reservation that is
    still reserved; a settled one (the project failed and is re-dispatched)
    is replaced by a new one for the clips still to render. Returns False when
    it cannot be paid (not enough tokens, plan limit): the project then waits
    for the review, where the user approves (and pays) what they want.

    Only while ingesting (``resuming``: a re-dispatch at render_dispatched,
    already rendering): a redelivered ingestion message that finds the user
    already approved a paper edit, or anything else past the review, does
    nothing. The clips it pays for are recorded (``auto_render_clip_ids``):
    only they are settled.
    """
    from apps.payments.models import Wallet
    from apps.payments.services import wallet_service
    from apps.payments.services.pricing_engine import FeatureNotAllowedError, PlanLimitExceededError
    from apps.videos.services.render_pricing import render_cost

    allowed = [VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING]
    if resuming:
        allowed.append(VideoProject.Status.RENDERING)
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        if project.status not in allowed:
            logger.info("pipeline.auto_render_not_ingesting project_id=%s", project_id)
            return False
        metadata = project.metadata or {}
        linked = metadata.get(AUTO_RENDER_RESERVATION_KEY)
        if linked and Transaction.objects.filter(pk=linked, status=Transaction.Status.RESERVED).exists():
            return True
        clips = list(
            VideoClip.objects.filter(project_id=project_id, status=VideoClip.Status.DRAFT)
            .values_list('id', 'start_time', 'end_time')
        )
        if not clips:
            return True  # nothing left to render, nothing to pay
        seconds = sum(max(0.0, float(end) - float(start)) for _id, start, end in clips)
        try:
            cost = render_cost(project, seconds)
            wallet, _ = Wallet.objects.get_or_create(workspace=project.workspace)
            txn = wallet_service.reserve_funds(
                wallet_id=wallet.id, amount=cost, user=project.uploaded_by,
                description=f"Render automático: {project.title}"[:255],
            )
        except (wallet_service.InsufficientFundsError, PlanLimitExceededError, FeatureNotAllowedError) as exc:
            reason = 'insufficient_funds' if isinstance(exc, wallet_service.InsufficientFundsError) else 'plan_limit'
            if not resuming:
                # Read by the review screen: the project waits for the user.
                project.metadata = {**metadata, 'auto_render_skipped': reason}
                project.save(update_fields=['metadata'])
            logger.info("pipeline.auto_render_skipped project_id=%s reason=%s", project_id, reason)
            return False
        project.metadata = {
            **metadata,
            AUTO_RENDER_RESERVATION_KEY: str(txn.id),
            'auto_render_seconds': round(seconds, 3),
            'auto_render_clip_ids': [str(clip_id) for clip_id, _start, _end in clips],
        }
        project.metadata.pop('auto_render_skipped', None)
        project.save(update_fields=['metadata'])
    logger.info("pipeline.auto_render_reserved project_id=%s", project_id)
    return True


def _auto_render_settlement(project):
    """
    What the auto-render reservation gets from where the project ended, as
    ``(outcome, delivered_seconds)``. The one rule for the pipeline and for
    settle_stale_reservations:

    - Not final yet (any stage but completed, partial or failed): ``('wait', 0)``.
    - A clip still rendering: ``('wait', 0)``.
    - No clip delivered (completed or published): ``('rollback', 0)``.
    - Otherwise ``('charge', seconds delivered)``: only what was delivered is paid.

    Only the clips the reservation paid for count (``auto_render_clip_ids``).
    """
    if project.pipeline_stage not in (*TERMINAL_STAGES, PipelineStage.FAILED):
        return 'wait', 0.0
    clips = VideoClip.objects.filter(project_id=project.pk)
    paid_ids = (project.metadata or {}).get('auto_render_clip_ids')
    if paid_ids is not None:
        # Only the clips this reservation paid for: a paper-edit clip, or clips
        # rendered before (a re-dispatch), are not part of it.
        clips = clips.filter(pk__in=paid_ids)
    clips = list(clips.values_list('status', 'start_time', 'end_time'))
    if any(status == VideoClip.Status.RENDERING for status, _start, _end in clips):
        return 'wait', 0.0
    delivered = sum(
        max(0.0, float(end) - float(start))
        for status, start, end in clips
        if status in (VideoClip.Status.COMPLETED, VideoClip.Status.PUBLISHED)
    )
    if delivered <= 0:
        return 'rollback', 0.0
    return 'charge', delivered


def _auto_render_charge(project, txn_amount, delivered_seconds):
    """The share of the reservation for the seconds delivered (cents, at least 0.01)."""
    reserved_seconds = float((project.metadata or {}).get('auto_render_seconds') or 0)
    if reserved_seconds <= 0 or delivered_seconds >= reserved_seconds:
        return Decimal(txn_amount)
    share = Decimal(str(delivered_seconds / reserved_seconds))
    charge = (Decimal(txn_amount) * share).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return max(charge, Decimal('0.01'))


def _settle_auto_render_reservation(project_id):
    """
    Settles the auto-render reservation from where the project ended (every
    ``finalize_project_render`` and the failure handler run it): the clips
    delivered are charged in proportion to their seconds, the rest is returned;
    nothing delivered is refunded whole; still rendering or not final waits.
    Idempotent (only a RESERVED transaction is touched) and never raises.
    """
    from django.core.exceptions import ValidationError

    # Deferred: wallet_service imports this module at load time.
    from apps.payments.services.wallet_service import (
        PaymentError, commit_partial_reservation, rollback_reservation,
    )

    try:
        with transaction.atomic():
            # Locked: a re-dispatch (reopen) must not slip between reading the
            # final state and settling from it.
            project = VideoProject.objects.select_for_update().filter(pk=project_id).first()
            reservation_id = ((project.metadata or {}) if project else {}).get(AUTO_RENDER_RESERVATION_KEY)
            if not reservation_id:
                return
            txn = Transaction.objects.filter(pk=reservation_id, status=Transaction.Status.RESERVED).first()
            if txn is None:
                return  # already settled
            outcome, delivered = _auto_render_settlement(project)
            if outcome == 'wait':
                return
            if outcome == 'rollback':
                rollback_reservation(reservation_id)
                logger.info("pipeline.auto_render_refunded project_id=%s", project_id)
            else:
                charge = _auto_render_charge(project, txn.amount, delivered)
                commit_partial_reservation(reservation_id, charge)
                logger.info(
                    "pipeline.auto_render_charged project_id=%s whole=%s",
                    project_id, charge >= txn.amount,
                )
    except PaymentError:
        # Another finalize, the failure handler or the sweeper settled it first.
        logger.info("pipeline.auto_render_settle_skipped project_id=%s reason=already_settled", project_id)
    except (DatabaseError, ValidationError) as exc:
        logger.error(
            "pipeline.auto_render_settle_failed project_id=%s error=%s", project_id, type(exc).__name__,
        )


def _whole_video_settlement(project):
    """
    What a whole-video project's creation reservation gets from where the
    project ended, as ``(outcome, reason)``. The one rule for the pipeline
    (``_settle_whole_video_reservation``) and for settle_stale_reservations:

    - Not final yet (any stage but completed, partial or failed, including
      clips_selected and render_dispatched): ``('wait', 'not_final')``.
    - A clip completed or published (the video was delivered):
      ``('commit', 'video_delivered')``.
    - A clip still rendering: ``('wait', 'clip_rendering')``.
    - Otherwise (the render failed, or the project failed before it):
      ``('rollback', 'video_not_delivered')``.
    """
    if project.pipeline_stage not in (*TERMINAL_STAGES, PipelineStage.FAILED):
        return 'wait', 'not_final'
    statuses = set(VideoClip.objects.filter(project_id=project.pk).values_list('status', flat=True))
    if statuses & {VideoClip.Status.COMPLETED, VideoClip.Status.PUBLISHED}:
        return 'commit', 'video_delivered'
    if VideoClip.Status.RENDERING in statuses:
        return 'wait', 'clip_rendering'
    return 'rollback', 'video_not_delivered'


def _settle_whole_video_reservation(project_id):
    """
    Settles a whole-video project's creation reservation from where the
    project ended, not from the transition that got it there. Every
    ``finalize_project_render`` call runs it, so it also settles a project that
    left render_dispatched another way (marked failed while its clip rendered)
    or a crash between the transition and the charge.

    - The video was delivered (a clip completed or published): charged.
    - Still rendering, or the project is not final yet: nothing for now.
    - Otherwise (the render failed, or the project failed before it): refunded.

    Idempotent (only a RESERVED transaction is touched) and never raises.
    """
    from django.core.exceptions import ValidationError

    # Deferred: wallet_service imports this module at load time.
    from apps.payments.services.wallet_service import PaymentError, rollback_reservation

    project = VideoProject.objects.filter(pk=project_id).first()
    if project is None or not _is_whole_video(project):
        return
    if project.pipeline_stage not in (*TERMINAL_STAGES, PipelineStage.FAILED):
        return
    if not _whole_video_is_paid(project_id):
        return  # already settled
    outcome, _reason = _whole_video_settlement(project)
    if outcome == 'commit':
        _commit_ingestion_reservation(project_id)
        return
    if outcome == 'wait':
        return

    try:
        reservation_id = (project.metadata or {}).get('reservation_id')
        if not reservation_id or not Transaction.objects.filter(
            pk=reservation_id, status=Transaction.Status.RESERVED,
        ).exists():
            logger.info("pipeline.refund_skipped project_id=%s reason=not_reserved", project_id)
            return
        rollback_reservation(reservation_id)
        logger.info(
            "pipeline.refunded project_id=%s reason=render_failed", project_id,
            extra={'project_id': str(project_id)},
        )
    except (PaymentError, DatabaseError, ValidationError) as exc:
        logger.error(
            "pipeline.refund_failed project_id=%s error=%s", project_id, type(exc).__name__
        )


def _check_whole_video_source(project, duration):
    """
    The whole video is rendered, so it must be the one priced at creation and
    within the whole-video limit (an admin can turn a long clips project into
    a single_edit). The price used whole seconds (int): one second of margin
    for that and for rounding between ffprobe and MoviePy.
    """
    if float(duration) > float(project.metadata['priced_duration_s']) + 1:
        raise WholeVideoSourceChangedError()
    limit = getattr(settings, 'AI_SINGLE_EDIT_MAX_DURATION_SECONDS', 600)
    if float(duration) > limit + 1:
        raise NonRetryableAIError(
            "The video is longer than a whole-video edit allows.",
            provider="pipeline", metadata={'code': 'too_long'},
        )


def _stage_whole_video(project_id):
    """
    Whole-video edit (``_is_whole_video``): the whole video is the one clip.
    No selection LLM and no review: the clip is rendered as soon as it exists
    (``_run_ingestion``). Never longer than what was priced at creation.
    """
    project = VideoProject.objects.get(pk=project_id)
    metadata = project.metadata or {}
    # MoviePy's own duration (written by _stage_prepare_source): the render cuts
    # with MoviePy, which fails on an end past the duration it measures.
    duration = metadata.get('duration')
    if not duration:
        duration, resolution = _probe_video(project.source_file.path)
        _merge_metadata(
            project_id, {'duration': duration, 'resolution': resolution},
            expected_stage=PipelineStage.MODERATED,
        )
    duration = float(duration or 0)
    if duration <= 0:
        raise NonRetryableAIError("The source has no duration to render.", provider="pipeline")
    _check_whole_video_source(project, duration)
    with transaction.atomic():
        VideoProject.objects.select_for_update().get(pk=project_id)
        # Dedupe: a redelivered task reuses the clip of an earlier attempt.
        if not VideoClip.objects.filter(project_id=project_id).exists():
            VideoClip.objects.create(
                project_id=project_id,
                title=project.title or "Video completo",
                start_time=0.0,
                end_time=float(duration),
                virality_score=0,
                ai_reasoning="",
                status=VideoClip.Status.DRAFT,
            )
        advance(project_id, PipelineStage.MODERATED, PipelineStage.CLIPS_SELECTED)
    logger.info("pipeline.whole_video project_id=%s", project_id)


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
        if _is_whole_video(project) and not _whole_video_is_paid(project_id):
            # Refunded when it failed: nothing is run again (not even a clip
            # created), and the reason it failed stays as it was.
            logger.warning("pipeline.redispatch_skipped project_id=%s reason=refunded", project_id)
            return f"Not resumed {project_id}: its whole-video payment was refunded"
        # Manual re-dispatch of a failed project: resume where it stopped.
        stage = last_completed_stage(project)
        reopen(project_id, stage)
        progress['stage'] = stage

    if stage in TERMINAL_STAGES:
        return f"Nothing to do for {project_id}: already {stage}"

    if stage == PipelineStage.RENDER_DISPATCHED:
        renew_lock()
        if _is_whole_video(project):
            # Charged by finalize_project_render once the video is delivered.
            _ensure_whole_video_paid(project_id)
        else:
            # A crash between the dispatch and the charge must not leave it reserved.
            _commit_ingestion_reservation(project_id)
            if (project.metadata or {}).get(AUTO_RENDER_RESERVATION_KEY) and not _reserve_auto_render(
                project_id, resuming=True,
            ):
                # Re-dispatched after its auto-render reservation was settled,
                # and the clips still to render cannot be paid: the project goes
                # back to failed (with its clips untouched), so it can be
                # re-dispatched once the workspace has tokens. Projects
                # dispatched before auto-render was charged have no key and
                # resume as they started.
                mark_failed(project_id, expected_stage=PipelineStage.RENDER_DISPATCHED)
                record_error(project_id, 'AutoRenderNotPaid')
                logger.warning("pipeline.redispatch_skipped project_id=%s reason=auto_render_unpaid", project_id)
                return f"Not resumed {project_id}: its auto-render cannot be paid"
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
            if _is_whole_video(VideoProject.objects.get(pk=project_id)):
                _stage_whole_video(project_id)
            else:
                _stage_select_clips(project_id)
        stage = PipelineStage.CLIPS_SELECTED
        progress['stage'] = stage

    if stage == PipelineStage.CLIPS_SELECTED:
        renew_lock()
        current = VideoProject.objects.get(pk=project_id)
        if _is_whole_video(current):
            # Nothing to review: it renders right away. What it pays for is the
            # render, so it is charged when finalize_project_render delivers it.
            _ensure_whole_video_paid(project_id)
            logger.info("pipeline.whole_video_render project_id=%s", project_id)
            with _running_stage(project_id, PipelineStage.RENDER_DISPATCHED, attempt):
                _stage_dispatch_render(project_id, progress)
        elif current.auto_render_bypass and _reserve_auto_render_after_charge(project_id):
            logger.info("⏩ Auto-render bypass active. Dispatching clip renders.")
            with _running_stage(project_id, PipelineStage.RENDER_DISPATCHED, attempt):
                _stage_dispatch_render(project_id, progress)
        else:
            # The clips exist: the ingestion work is delivered and its tokens are
            # charged (already, if an auto-render could not be paid).
            _commit_ingestion_reservation(project_id)
            logger.info("⏳ Ingestion complete. Awaiting user approval.")
            # Guarded: a redelivered task must not undo a later user action.
            VideoProject.objects.filter(
                pk=project_id,
                status__in=[VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING],
            ).update(status=VideoProject.Status.AWAITING_APPROVAL)

    return f"Ingestion Success {project_id}"


def _reserve_auto_render_after_charge(project_id):
    """
    Auto-render: the clips exist, so the ingestion work is delivered and its
    tokens are charged; then the render of the clips is reserved. False when
    it cannot be paid: the project waits for the review instead.
    """
    _commit_ingestion_reservation(project_id)
    return _reserve_auto_render(project_id)


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
        _settle_auto_render_reservation(project_id)


# acks_late + reject_on_worker_lost: if the worker dies mid-task (OOM during
# MoviePy/Whisper) the message is redelivered instead of silently lost. Running
# the task twice is safe because every stage is idempotent: transitions are
# compare-and-set, the Redis lock keeps two workers off the same project, and
# stage outputs (transcript, clips) are persisted and reused, never recomputed.
# The soft time limit (settings.PIPELINE_SOFT_TIME_LIMIT) raises
# SoftTimeLimitExceeded, which is non-retryable: the project fails with that
# code and the lock is released instead of being renewed by a hung worker.
@celery_app.task(name='apps.videos.tasks.process_initial_ingestion', 
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
                kwargs={'lock_retries': lock_retries + 1, 'parse_retries': parse_retries},
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
                project_id, exc, attempt, progress.get('stage'), self.request.retries,
                parse_retries,
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

@celery_app.task(name='apps.videos.tasks.process_video_pipeline', bind=True)
def process_video_pipeline(self, project_id, transaction_id=None):
    """
    Orchestrator task that links payments and ingestion.
    """
    logger.info(f"🧬 [PIPELINE] Starting for project {project_id} (Tx: {transaction_id})")
    # For now, it delegates to initial ingestion. 
    # In the future, it could handle the full lifecycle.
    return process_initial_ingestion.delay(project_id)
