"""
Groq Whisper Transcription Provider (AI Core V2).

Remote ASR provider using the official Groq SDK to transcribe audio
via the whisper-large-v3-turbo model. Supports multi-chunk transcription
with offset correction and overlap deduplication.

Design constraints:
- Never logs raw audio bytes or full transcript text.
- Error classification follows the AICORE-1 hierarchy strictly.
- Cost estimation comes from the versioned table in ``pricing.py`` (per-model rate).
"""

import logging
import time
from decimal import Decimal
from typing import List, Optional, Tuple

from django.conf import settings

from apps.videos.services.ai.audio_preprocessor import AudioChunk
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ProviderUsage,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from apps.videos.services.ai.pricing import PRICING_VERSION, estimate_asr_cost
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
)

logger = logging.getLogger(__name__)

# Provider identifier used in ProviderUsage
_PROVIDER_NAME = "groq"


class GroqTranscriptionProvider:
    """
    Transcribes audio chunks using Groq's hosted Whisper API.

    Responsibilities:
    1. Validate API key availability at construction time.
    2. Send each AudioChunk to the Groq transcriptions endpoint.
    3. Apply temporal offsets for multi-chunk pipelines.
    4. Deduplicate words that fall inside overlap zones.
    5. Classify SDK exceptions into the AICORE-1 error hierarchy.
    6. Calculate latency, duration, and estimated cost metrics.
    """

    def __init__(self, api_key: Optional[str] = None):
        resolved_key = api_key or getattr(settings, "GROQ_API_KEY", None)

        if not resolved_key:
            raise AIAuthenticationError(
                message="GROQ_API_KEY is not configured. Set it in your environment or Django settings.",
                provider=_PROVIDER_NAME,
                model=self._get_model(),
            )

        # Lazy import to avoid hard dependency at module load time
        try:
            from groq import Groq
        except ImportError as exc:
            raise AIAuthenticationError(
                message="The 'groq' package is not installed. Run: pip install groq",
                provider=_PROVIDER_NAME,
                raw_error=exc,
            )

        self._client = Groq(api_key=resolved_key)
        self._model = self._get_model()

    @staticmethod
    def _get_model() -> str:
        return getattr(settings, "AI_DEFAULT_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe(self, chunks: List[AudioChunk]) -> AIExecutionResult[TranscriptionResult]:
        """
        Transcribes one or more audio chunks and merges the results.

        For single-chunk audio, no offset or dedup is needed.
        For multi-chunk audio, each chunk's timestamps are shifted by
        its start_time and overlap-zone words are deduplicated.
        """
        if not chunks:
            raise ValueError("No audio chunks provided for transcription.")

        all_words: List[WordTimestamp] = []
        all_segments: List[TranscriptionSegment] = []
        all_text_parts: List[str] = []
        total_latency = 0.0
        total_audio_duration = 0.0
        # Groq bills each request separately (with a minimum length), so the
        # cost is the sum over chunks; None as soon as one chunk is unpriced.
        total_cost: Optional[Decimal] = Decimal(0)

        prev_chunk_end: Optional[float] = None

        for chunk in chunks:
            logger.info(
                "🎙️ [GroqTranscription] Processing chunk %d "
                "(offset=%.3fs, duration=%.3fs)",
                chunk.chunk_index,
                chunk.start_time,
                chunk.duration,
            )

            raw_response, latency = self._call_groq_api(chunk.file_path)
            total_latency += latency

            result = self._parse_response(raw_response)

            # Apply temporal offset for this chunk
            if chunk.start_time > 0:
                result = self._apply_offset(result, chunk.start_time)

            # Deduplicate overlap-zone words
            if prev_chunk_end is not None and result.words:
                result_words = self._deduplicate_overlap(
                    words=result.words,
                    prev_end=prev_chunk_end,
                    overlap=2.0,
                )
            else:
                result_words = list(result.words)

            all_words.extend(result_words)
            all_segments.extend(result.segments)
            all_text_parts.append(result.full_text)
            total_audio_duration += chunk.duration
            chunk_cost = estimate_asr_cost(self._model, chunk.duration)
            total_cost = None if (total_cost is None or chunk_cost is None) else total_cost + chunk_cost

            prev_chunk_end = chunk.end_time

            logger.info(
                "✅ [GroqTranscription] Chunk %d done: %d words, latency=%.3fs",
                chunk.chunk_index,
                len(result_words),
                latency,
            )

        merged = TranscriptionResult(
            full_text=" ".join(all_text_parts),
            words=all_words,
            segments=all_segments,
            language=None,
            duration=total_audio_duration,
        )

        usage = ProviderUsage(
            provider=_PROVIDER_NAME,
            model=self._model,
            duration_seconds=total_latency,
            # Decimal -> float only at the contract boundary; None = unknown price.
            estimated_cost_usd=None if total_cost is None else float(round(total_cost, 6)),
            audio_seconds=total_audio_duration,
            pricing_version=PRICING_VERSION,
        )

        return AIExecutionResult[TranscriptionResult](
            data=merged,
            usage=usage,
            success=True,
        )

    def transcribe_chunk(self, chunk: AudioChunk) -> AIExecutionResult[TranscriptionResult]:
        """Convenience method to transcribe a single chunk."""
        return self.transcribe([chunk])

    # ------------------------------------------------------------------
    # Groq SDK interaction
    # ------------------------------------------------------------------

    def _call_groq_api(self, file_path: str) -> Tuple[dict, float]:
        """
        Sends a single file to the Groq transcriptions endpoint.

        Returns:
            Tuple of (raw_response_dict, latency_seconds).

        Raises:
            AIError subclass based on the failure type.
        """
        try:
            start = time.monotonic()
            with open(file_path, "rb") as audio_file:
                response = self._client.audio.transcriptions.create(
                    file=("audio.flac", audio_file),
                    model=self._model,
                    response_format="verbose_json",
                    timestamp_granularities=["word", "segment"],
                    language="es",
                )
            latency = time.monotonic() - start

            # The SDK returns a Pydantic model; convert to dict for parsing
            if hasattr(response, "model_dump"):
                return response.model_dump(), latency
            elif hasattr(response, "to_dict"):
                return response.to_dict(), latency
            else:
                return dict(response), latency

        except Exception as exc:
            raise self._classify_error(exc) from exc

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: dict) -> TranscriptionResult:
        """
        Maps the Groq verbose_json response to TranscriptionResult contracts.
        """
        full_text = raw.get("text", "").strip()

        # Parse word-level timestamps
        words: List[WordTimestamp] = []
        for w in raw.get("words", []):
            words.append(
                WordTimestamp(
                    text=w.get("word", "").strip(),
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                )
            )

        # Parse segment-level timestamps
        segments: List[TranscriptionSegment] = []
        for seg in raw.get("segments", []):
            seg_words: List[WordTimestamp] = []
            for sw in seg.get("words", []):
                seg_words.append(
                    WordTimestamp(
                        text=sw.get("word", "").strip(),
                        start=float(sw.get("start", 0.0)),
                        end=float(sw.get("end", 0.0)),
                    )
                )
            segments.append(
                TranscriptionSegment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=seg.get("text", "").strip(),
                    words=seg_words,
                )
            )

        language = raw.get("language", "es")
        duration = raw.get("duration")

        return TranscriptionResult(
            full_text=full_text if full_text else "transcription",
            words=words,
            segments=segments,
            language=language,
            duration=float(duration) if duration is not None else None,
        )

    # ------------------------------------------------------------------
    # Offset and deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_offset(result: TranscriptionResult, offset: float) -> TranscriptionResult:
        """
        Shifts all word and segment timestamps by the given offset.
        """
        shifted_words = [
            WordTimestamp(
                text=w.text,
                start=round(w.start + offset, 3),
                end=round(w.end + offset, 3),
                confidence=w.confidence,
                speaker=w.speaker,
            )
            for w in result.words
        ]

        shifted_segments = [
            TranscriptionSegment(
                start=round(seg.start + offset, 3),
                end=round(seg.end + offset, 3),
                text=seg.text,
                words=[
                    WordTimestamp(
                        text=w.text,
                        start=round(w.start + offset, 3),
                        end=round(w.end + offset, 3),
                        confidence=w.confidence,
                        speaker=w.speaker,
                    )
                    for w in seg.words
                ],
                speaker=seg.speaker,
                is_emoji=seg.is_emoji,
            )
            for seg in result.segments
        ]

        return TranscriptionResult(
            full_text=result.full_text,
            words=shifted_words,
            segments=shifted_segments,
            language=result.language,
            duration=result.duration,
        )

    @staticmethod
    def _deduplicate_overlap(
        words: List[WordTimestamp],
        prev_end: float,
        overlap: float,
    ) -> List[WordTimestamp]:
        """
        Removes words from the current chunk that fall within the overlap
        zone of the previous chunk.

        A word is considered a duplicate if its start timestamp is earlier
        than the previous chunk's end minus a small tolerance (half the overlap).
        """
        cutoff = prev_end - (overlap / 2.0)
        return [w for w in words if w.start >= cutoff]

    # ------------------------------------------------------------------
    # Error classification
    # ------------------------------------------------------------------

    def _classify_error(self, exc: Exception):
        """
        Maps Groq SDK exceptions to the AICORE-1 error hierarchy.

        - 401/403 -> AIAuthenticationError (non-retryable)
        - 429     -> AIRateLimitError (retryable)
        - 5xx     -> AIServerError (retryable)
        - Timeout -> AITimeoutError (retryable)
        - Connection errors -> AIConnectionError (retryable)
        """
        status_code = getattr(exc, "status_code", None)
        message = str(exc)

        # Groq SDK raises specific HTTP-based exceptions
        if status_code in (401, 403):
            return AIAuthenticationError(
                message=f"Groq authentication failed: {message}",
                provider=_PROVIDER_NAME,
                model=self._model,
                status_code=status_code,
                raw_error=exc,
            )

        if status_code == 429:
            retry_after = None
            if hasattr(exc, "response") and exc.response is not None:
                retry_after_str = getattr(exc.response.headers, "get", lambda *a: None)("retry-after")
                if retry_after_str:
                    try:
                        retry_after = float(retry_after_str)
                    except (ValueError, TypeError):
                        pass
            return AIRateLimitError(
                message=f"Groq rate limit exceeded: {message}",
                retry_after=retry_after,
                provider=_PROVIDER_NAME,
                model=self._model,
                raw_error=exc,
            )

        if status_code is not None and 500 <= status_code < 600:
            return AIServerError(
                message=f"Groq server error: {message}",
                status_code=status_code,
                provider=_PROVIDER_NAME,
                model=self._model,
                raw_error=exc,
            )

        # Timeout-like exceptions
        if isinstance(exc, (TimeoutError,)):
            return AITimeoutError(
                message=f"Groq request timed out: {message}",
                provider=_PROVIDER_NAME,
                model=self._model,
                raw_error=exc,
            )

        # Check for groq-specific timeout/connection types by class name
        exc_class_name = type(exc).__name__.lower()
        if "timeout" in exc_class_name:
            return AITimeoutError(
                message=f"Groq request timed out: {message}",
                provider=_PROVIDER_NAME,
                model=self._model,
                raw_error=exc,
            )

        if isinstance(exc, (ConnectionError, OSError)):
            return AIConnectionError(
                message=f"Failed to connect to Groq: {message}",
                provider=_PROVIDER_NAME,
                model=self._model,
                raw_error=exc,
            )

        if "connection" in exc_class_name:
            return AIConnectionError(
                message=f"Failed to connect to Groq: {message}",
                provider=_PROVIDER_NAME,
                model=self._model,
                raw_error=exc,
            )

        # Fallback: treat as server error (retryable) to be safe
        return AIServerError(
            message=f"Unexpected Groq error: {message}",
            status_code=status_code or 500,
            provider=_PROVIDER_NAME,
            model=self._model,
            raw_error=exc,
        )
