"""
TranscriptionEngine — public facade for speech-to-text (AICORE-5).

Backends:
- ``groq``  (AI Core V2, default): remote Whisper Large V3 Turbo via Groq.
- ``local`` (legacy): openai-whisper on the worker CPU/GPU.

Routing rules (see ADR-008):
- ``AI_CORE_V2_ENABLED=False``  -> legacy local Whisper (pilot kill switch).
- ``AI_CORE_V2_ENABLED=True``   -> ``TRANSCRIPTION_BACKEND`` (``groq`` by default).

There is deliberately NO silent fallback from Groq to local Whisper: loading a
1-3 GB model inside a worker that is already rendering is the fastest way to an
OOM kill. Provider failures propagate as typed ``AIError`` subclasses so the
Celery task decides whether to retry (``is_retryable_error``) or fail fast.

Importing this module must stay cheap: no ``whisper``, ``torch`` or network
clients are imported at module load time.
"""

import logging
import os
import threading
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed at runtime
    from apps.videos.services.ai.audio_preprocessor import AudioPreprocessor
    from apps.videos.services.ai.contracts import AIExecutionResult, TranscriptionResult
    from apps.videos.services.ai.groq_transcription import GroqTranscriptionProvider

logger = logging.getLogger(__name__)


class TranscriptionBackend(str, Enum):
    GROQ = "groq"
    LOCAL = "local"


# ----------------------------------------------------------------------
# Local Whisper model cache (CORE-04)
# ----------------------------------------------------------------------
# One model per (size, device) per worker process. Loading takes seconds to
# minutes and up to ~3 GB, so it must not happen once per task. Worker
# recycling (CELERY_WORKER_MAX_TASKS_PER_CHILD) bounds how long a process keeps it.
_LOCAL_MODELS: Dict[Tuple[str, str], Any] = {}
_LOCAL_MODELS_LOCK = threading.Lock()


def resolve_local_device() -> str:
    """
    Device for local Whisper from ``TRANSCRIPTION_DEVICE``: 'auto' (default) picks
    CUDA when torch sees a GPU, else CPU; 'cpu' / 'cuda' force it. A forced
    'cuda' without a GPU falls back to CPU with a warning instead of crashing.
    Apple MPS is not used: openai-whisper is unreliable on it.
    """
    requested = str(getattr(settings, "TRANSCRIPTION_DEVICE", "auto") or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"
    if requested not in ("auto", "cuda"):
        raise ImproperlyConfigured(
            f"TRANSCRIPTION_DEVICE must be 'auto', 'cpu' or 'cuda', got {requested!r}"
        )

    import torch

    if torch.cuda.is_available():
        return "cuda"
    if requested == "cuda":
        logger.warning("transcription.cuda_unavailable falling back to cpu")
    return "cpu"


def load_local_model(model_size: str) -> Tuple[Any, str]:
    """Returns ``(model, device)``, loading the model only once per process."""
    device = resolve_local_device()
    key = (model_size, device)
    model = _LOCAL_MODELS.get(key)
    if model is not None:
        return model, device
    with _LOCAL_MODELS_LOCK:
        model = _LOCAL_MODELS.get(key)
        if model is None:
            import whisper

            started = time.monotonic()
            model = whisper.load_model(model_size, device=device)
            _LOCAL_MODELS[key] = model
            logger.info(
                "transcription.local_model_loaded size=%s device=%s duration_s=%.3f",
                model_size,
                device,
                time.monotonic() - started,
                extra={"model_size": model_size, "device": device},
            )
    return model, device


def preload_local_model() -> bool:
    """
    Loads the local Whisper model at worker process start (``worker_process_init``)
    when ``WHISPER_PRELOAD`` is on and the local backend is the active one.
    Never raises: a failed preload only means the first task loads the model.
    Returns True when a model was loaded or was already cached.
    """
    if not getattr(settings, "WHISPER_PRELOAD", True):
        return False
    try:
        if TranscriptionEngine.resolve_backend() is not TranscriptionBackend.LOCAL:
            return False
        size = getattr(settings, "WHISPER_LOCAL_MODEL_SIZE", "base")
        load_local_model(size)
        return True
    except Exception as exc:  # pragma: no cover - logged, never fatal for the worker
        logger.warning("transcription.preload_failed error=%s", type(exc).__name__)
        return False


class TranscriptionEngine:
    """
    Service for transcribing audio/video files.

    Public API (stable, consumed by tasks, RenderEngine and subtitles):
    - ``transcribe(audio_path, word_timestamps=True) -> List[{start, end, text}]``
    - ``transcribe_detailed(audio_path) -> AIExecutionResult[TranscriptionResult]``
    - ``group_words(...)`` (static)

    Collaborators are injectable so tests never touch the network or load models.
    """

    def __init__(
        self,
        model_size: str = "tiny",
        groq_provider_factory: Optional[Callable[[], "GroqTranscriptionProvider"]] = None,
        preprocessor_factory: Optional[Callable[[], "AudioPreprocessor"]] = None,
        v2_enabled: Optional[bool] = None,
    ):
        """
        Args:
            model_size: local Whisper size ('tiny', 'base', 'small', ...). Ignored by Groq.
            v2_enabled: V2 for this project (rollout.v2_enabled_for); None reads
                the master switch AI_CORE_V2_ENABLED.
            groq_provider_factory: builds the Groq provider (tests inject a fake).
            preprocessor_factory: builds the audio preprocessor (tests inject a fake).
        """
        self.model_size = model_size
        self._model = None
        self._device: Optional[str] = None
        self._groq_provider_factory = groq_provider_factory
        self._preprocessor_factory = preprocessor_factory
        self._v2_enabled = v2_enabled
        self.last_full_text: Optional[str] = None

    # ------------------------------------------------------------------
    # Backend resolution
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_backend(v2_enabled: Optional[bool] = None) -> TranscriptionBackend:
        """
        Resolves the active backend from settings on every call (not cached),
        so flipping the flag takes effect without restarting the worker.
        ``v2_enabled`` is the per-project rollout decision; None reads the
        master switch.
        """
        if v2_enabled is None:
            v2_enabled = getattr(settings, "AI_CORE_V2_ENABLED", False)
        if not v2_enabled:
            return TranscriptionBackend.LOCAL

        raw = str(getattr(settings, "TRANSCRIPTION_BACKEND", TranscriptionBackend.GROQ.value))
        try:
            return TranscriptionBackend(raw.strip().lower())
        except ValueError as exc:
            valid = ", ".join(b.value for b in TranscriptionBackend)
            raise ImproperlyConfigured(
                f"Invalid TRANSCRIPTION_BACKEND={raw!r}. Expected one of: {valid}."
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe(self, audio_path: str, word_timestamps: bool = True) -> List[Dict[str, Any]]:
        """
        Transcribes an audio or video file.

        Returns word dicts ``{start, end, text}`` when ``word_timestamps`` is True,
        otherwise segment dicts with the same keys. Signature and output format
        are unchanged from the legacy engine.
        """
        self._ensure_exists(audio_path)
        backend = self.resolve_backend(self._v2_enabled)

        if backend is TranscriptionBackend.LOCAL:
            raw = self._transcribe_local_raw(audio_path, word_timestamps=word_timestamps)
            self.last_full_text = str(raw.get("text", "")).strip() or None
            if word_timestamps:
                return self._legacy_words(raw)
            return self._legacy_segments(raw)

        from apps.videos.services.ai.errors import AINoSpeechDetectedError

        try:
            exec_result = self._transcribe_groq(audio_path)
        except AINoSpeechDetectedError:
            # This list API (subtitles for a rendered clip) answers "no words"
            # for silent audio, as the local backend always did; only the
            # ingestion path (transcribe_detailed) treats it as a failure.
            self.last_full_text = None
            return []
        tx = exec_result.data
        self.last_full_text = tx.full_text

        if word_timestamps:
            return [{"start": w.start, "end": w.end, "text": w.text} for w in tx.words]
        return [{"start": s.start, "end": s.end, "text": s.text} for s in tx.segments]

    def transcribe_detailed(self, audio_path: str) -> "AIExecutionResult[TranscriptionResult]":
        """
        Transcribes and returns the full contract plus provider usage metrics
        (provider, model, latency, estimated cost). Always word-level.
        """
        self._ensure_exists(audio_path)
        backend = self.resolve_backend(self._v2_enabled)

        if backend is TranscriptionBackend.LOCAL:
            started = time.monotonic()
            raw = self._transcribe_local_raw(audio_path, word_timestamps=True)
            result = self._local_to_execution_result(raw, latency=time.monotonic() - started)
        else:
            result = self._transcribe_groq(audio_path)

        self.last_full_text = result.data.full_text
        return result

    # ------------------------------------------------------------------
    # Groq backend (AI Core V2)
    # ------------------------------------------------------------------

    def _transcribe_groq(self, audio_path: str) -> "AIExecutionResult[TranscriptionResult]":
        preprocessor = self._build_preprocessor()
        provider = self._build_groq_provider()

        logger.info(
            "transcription.started backend=groq",
            extra={"backend": TranscriptionBackend.GROQ.value, "provider": "groq"},
        )
        # The preprocessor context guarantees FLAC chunk cleanup even when the
        # provider raises, so a failed attempt never leaks temp audio to disk.
        with preprocessor.process(audio_path) as chunks:
            result = provider.transcribe(chunks)

        logger.info(
            "transcription.completed backend=groq model=%s latency_s=%.3f words=%d",
            result.usage.model,
            result.usage.duration_seconds,
            len(result.data.words),
            extra={
                "backend": TranscriptionBackend.GROQ.value,
                "provider": result.usage.provider,
                "model": result.usage.model,
                "latency_s": result.usage.duration_seconds,
                "words": len(result.data.words),
                "cost_usd": result.usage.estimated_cost_usd,
            },
        )
        return result

    def _build_groq_provider(self) -> "GroqTranscriptionProvider":
        if self._groq_provider_factory is not None:
            return self._groq_provider_factory()
        from apps.videos.services.ai.groq_transcription import GroqTranscriptionProvider

        # Raises AIAuthenticationError (non-retryable) when GROQ_API_KEY is missing:
        # a misconfigured worker must fail fast instead of degrading to local Whisper.
        return GroqTranscriptionProvider()

    def _build_preprocessor(self) -> "AudioPreprocessor":
        if self._preprocessor_factory is not None:
            return self._preprocessor_factory()
        from apps.videos.services.ai.audio_preprocessor import AudioPreprocessor

        return AudioPreprocessor()

    # ------------------------------------------------------------------
    # Local backend (legacy openai-whisper)
    # ------------------------------------------------------------------

    @property
    def model(self):
        """
        Local Whisper model (~75 MB to ~3 GB), shared by every engine of the
        worker process (``load_local_model``). whisper/torch are imported there,
        never at module import, so the Groq path and the web container do not
        pay their memory cost.
        """
        if self._model is None:
            self._model, self._device = load_local_model(self.model_size)
        return self._model

    def _transcribe_local_raw(self, audio_path: str, word_timestamps: bool) -> Dict[str, Any]:
        logger.info(
            "transcription.started backend=local size=%s",
            self.model_size,
            extra={"backend": TranscriptionBackend.LOCAL.value, "model_size": self.model_size},
        )
        model = self.model
        # fp16 halves GPU inference time; on CPU it is unsupported (Whisper would
        # warn and fall back to fp32), so it is only requested on CUDA.
        fp16 = self._device == "cuda"
        return model.transcribe(audio_path, fp16=fp16, word_timestamps=word_timestamps)

    def _local_to_execution_result(
        self, raw: Dict[str, Any], latency: float
    ) -> "AIExecutionResult[TranscriptionResult]":
        from apps.videos.services.ai.contracts import (
            AIExecutionResult,
            ProviderUsage,
            TranscriptionResult,
            TranscriptionSegment,
            WordTimestamp,
        )
        from apps.videos.services.ai.errors import AINoSpeechDetectedError
        from apps.videos.services.ai.pricing import PRICING_VERSION

        full_text = str(raw.get("text", "")).strip()
        if not full_text:
            # The contract requires non-empty text; silent audio is a deterministic
            # outcome, so it is reported as non-retryable instead of looping retries.
            # Same error as the Groq backend so both report one static code.
            raise AINoSpeechDetectedError(
                message="Local Whisper returned an empty transcription (no speech detected).",
                provider="local",
                model=f"whisper-{self.model_size}",
            )

        segments: List[TranscriptionSegment] = []
        words: List[WordTimestamp] = []
        for seg in raw.get("segments", []):
            if not isinstance(seg, dict):
                continue
            seg_words = [
                WordTimestamp(
                    text=str(w.get("word", "")).strip(),
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    confidence=w.get("probability"),
                )
                for w in seg.get("words", [])
                if isinstance(w, dict) and str(w.get("word", "")).strip()
            ]
            words.extend(seg_words)
            seg_text = str(seg.get("text", "")).strip()
            if seg_text:
                segments.append(
                    TranscriptionSegment(
                        start=float(seg.get("start", 0.0)),
                        end=float(seg.get("end", 0.0)),
                        text=seg_text,
                        words=seg_words,
                    )
                )

        data = TranscriptionResult(
            full_text=full_text,
            words=words,
            segments=segments,
            language=raw.get("language"),
            duration=segments[-1].end if segments else None,
        )
        usage = ProviderUsage(
            provider="local",
            model=f"whisper-{self.model_size}",
            duration_seconds=round(latency, 3),
            # Local inference has no API charge: a real zero (unlike a missing price).
            estimated_cost_usd=0.0,
            audio_seconds=data.duration,
            pricing_version=PRICING_VERSION,
        )
        return AIExecutionResult[TranscriptionResult](data=data, usage=usage, success=True)

    @staticmethod
    def _legacy_words(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Legacy word extraction, kept byte-compatible with the pre-AICORE engine."""
        all_words: List[Dict[str, Any]] = []
        for segment in raw.get("segments", []):
            if isinstance(segment, dict):
                for word in segment.get("words", []):
                    if isinstance(word, dict):
                        all_words.append({
                            "start": word.get("start"),
                            "end": word.get("end"),
                            "text": str(word.get("word", "")).strip(),
                        })
        return all_words

    @staticmethod
    def _legacy_segments(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        segments: List[Dict[str, Any]] = []
        for seg in raw.get("segments", []):
            if isinstance(seg, dict):
                segments.append({
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "text": str(seg.get("text", "")).strip(),
                })
        return segments

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_exists(audio_path: str) -> None:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

    @staticmethod
    def group_words(words: List[Dict[str, Any]], max_words: int = 3, emoji_map: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """
        Groups words into manageable segments. 
        If a word matches the emoji_map, it creates a standalone emoji segment.
        """
        if not words:
            return []
            
        grouped = []
        buffer = []
        
        for w in words:
            # Normalize the word to look it up in the emoji map
            clean_word = "".join(e for e in w["text"].lower() if e.isalnum())
            
            if emoji_map and clean_word in emoji_map:
                # 1. Close and save existing buffer
                if buffer:
                    grouped.append({
                        "start": buffer[0]["start"],
                        "end": buffer[-1]["end"],
                        "text": " ".join([x["text"] for x in buffer]),
                        "is_emoji": False
                    })
                    buffer = []
                
                # 2. Create high-impact emoji segment
                grouped.append({
                    "start": w["start"],
                    "end": w["end"],
                    "text": emoji_map[clean_word],
                    "is_emoji": True
                })
            else:
                # Standard word, add to buffer
                buffer.append(w)
                # Close segment if word limit reached (e.g., 3 words)
                if len(buffer) >= max_words:
                    grouped.append({
                        "start": buffer[0]["start"],
                        "end": buffer[-1]["end"],
                        "text": " ".join([x["text"] for x in buffer]),
                        "is_emoji": False
                    })
                    buffer = []
        
        # Save remaining words in the buffer
        if buffer:
            grouped.append({
                "start": buffer[0]["start"],
                "end": buffer[-1]["end"],
                "text": " ".join([x["text"] for x in buffer]),
                "is_emoji": False
            })
            
        return grouped
