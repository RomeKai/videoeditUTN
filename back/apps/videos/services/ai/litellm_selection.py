"""
LiteLLM Clip Selection Provider (AI Core V2).

Selects viral-worthy clips from a transcription using structured LLM output.
Uses LiteLLM as an embedded SDK (no proxy) with Gemini 3.8 Flash as primary
and gpt-4o-mini as optional fallback.

Design constraints:
- JSON Schema for structured output is generated from Pydantic contracts.
- Explicit caching is disabled; implicit provider caching is tracked.
- At most one fallback attempt on primary failure/invalid result.
- Never logs prompts, transcriptions, or raw LLM responses.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    ViralClip,
)
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIContractValidationError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
)

logger = logging.getLogger(__name__)

# Schema generated once from Pydantic contracts
_CLIP_SELECTION_SCHEMA = ClipSelectionResult.model_json_schema()


class LiteLLMSelectionProvider:
    """
    Selects viral clips from transcription text using LLM structured output.

    Responsibilities:
    1. Build a prompt with transcription context and selection criteria.
    2. Call LiteLLM completion with JSON Schema response format.
    3. Parse and validate the structured response against Pydantic contracts.
    4. Validate clip timestamps against the actual video duration.
    5. Fall back to gpt-4o-mini if primary fails AND OPENAI_API_KEY exists.
    6. Track token usage including implicit cache hits.
    """

    def __init__(
        self,
        primary_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ):
        self._primary_model = primary_model or getattr(
            settings, "AI_DEFAULT_LLM_MODEL", "gemini/gemini-3.8-flash"
        )
        self._fallback_model = fallback_model or getattr(
            settings, "AI_FALLBACK_LLM_MODEL", "gpt-4o-mini"
        )

        # Set API keys for LiteLLM from Django settings
        import litellm

        gemini_key = getattr(settings, "GEMINI_API_KEY", None)
        if gemini_key:
            litellm.api_key = gemini_key

        openai_key = getattr(settings, "OPENAI_API_KEY", None)
        if openai_key:
            import os
            os.environ.setdefault("OPENAI_API_KEY", openai_key)

        # Disable explicit caching
        litellm.cache = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_clips(
        self,
        transcript: str,
        video_duration: float,
        target_count: int = 3,
        editing_style: str = "dynamic",
    ) -> AIExecutionResult[ClipSelectionResult]:
        """
        Analyzes a transcription and selects viral-worthy clips.

        Args:
            transcript: Full transcription text to analyze.
            video_duration: Total video duration in seconds for validation.
            target_count: Number of clips to select.
            editing_style: Editing style hint for the LLM.

        Returns:
            AIExecutionResult wrapping a validated ClipSelectionResult.
        """
        if not transcript or not transcript.strip():
            raise ValueError("Transcript text is required for clip selection.")
        if video_duration <= 0:
            raise ValueError("Video duration must be positive.")

        messages = self._build_messages(
            transcript=transcript,
            video_duration=video_duration,
            target_count=target_count,
            editing_style=editing_style,
        )

        # Try primary model
        try:
            result, usage = self._call_and_parse(
                model=self._primary_model,
                messages=messages,
                video_duration=video_duration,
            )

            logger.info(
                "✅ [LiteLLMSelection] Primary model succeeded: %d clips, "
                "tokens=%d/%d, latency=%.3fs",
                len(result.clips),
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.duration_seconds,
            )

            return AIExecutionResult[ClipSelectionResult](
                data=result, usage=usage, success=True,
            )

        except Exception as primary_exc:
            logger.warning(
                "⚠️ [LiteLLMSelection] Primary model failed: %s",
                type(primary_exc).__name__,
            )

            # Attempt fallback if available
            if not self._has_fallback():
                logger.error(
                    "❌ [LiteLLMSelection] No fallback available (OPENAI_API_KEY not set)."
                )
                raise self._classify_error(primary_exc) from primary_exc

            try:
                result, usage = self._call_and_parse(
                    model=self._fallback_model,
                    messages=messages,
                    video_duration=video_duration,
                )

                logger.info(
                    "✅ [LiteLLMSelection] Fallback model succeeded: %d clips, "
                    "tokens=%d/%d, latency=%.3fs",
                    len(result.clips),
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.duration_seconds,
                )

                return AIExecutionResult[ClipSelectionResult](
                    data=result, usage=usage, success=True,
                )

            except Exception as fallback_exc:
                logger.error(
                    "❌ [LiteLLMSelection] Fallback model also failed: %s",
                    type(fallback_exc).__name__,
                )
                raise self._classify_error(fallback_exc) from fallback_exc

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _call_and_parse(
        self,
        model: str,
        messages: List[Dict[str, str]],
        video_duration: float,
    ) -> Tuple[ClipSelectionResult, ProviderUsage]:
        """
        Calls LiteLLM and parses the structured response.
        """
        response, usage = self._call_llm(model, messages)
        result = self._parse_response(response, video_duration)
        return result, usage

    def _call_llm(
        self,
        model: str,
        messages: List[Dict[str, str]],
    ) -> Tuple[str, ProviderUsage]:
        """
        Makes a single LiteLLM completion call with structured output.

        Returns:
            Tuple of (raw_content_string, ProviderUsage).
        """
        import litellm

        try:
            start = time.monotonic()
            response = litellm.completion(
                model=model,
                messages=messages,
                response_format={
                    "type": "json_object",
                    "response_schema": _CLIP_SELECTION_SCHEMA,
                },
                temperature=0.7,
            )
            latency = time.monotonic() - start

            content = response.choices[0].message.content
            usage = self._extract_usage(response, model, latency)

            return content, usage

        except Exception as exc:
            raise self._classify_error(exc) from exc

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        transcript: str,
        video_duration: float,
        target_count: int,
        editing_style: str,
    ) -> List[Dict[str, str]]:
        """
        Constructs the system and user messages for clip selection.
        """
        system_prompt = (
            "You are an expert viral content strategist and video editor. "
            "Your task is to analyze a video transcription and identify the most "
            "engaging, shareable moments that would perform well as short-form content "
            "(TikTok, Instagram Reels, YouTube Shorts).\n\n"
            "RULES:\n"
            f"- Select exactly {target_count} clips.\n"
            f"- The video is {video_duration:.1f} seconds long. "
            "All timestamps must be within [0, video_duration].\n"
            "- Each clip should be 15-60 seconds long.\n"
            "- Clips must not overlap significantly.\n"
            f"- Editing style: {editing_style}.\n"
            "- Provide a catchy title, virality score (0-100), reasoning, "
            "optional hook text, and relevant hashtags for each clip.\n\n"
            "Respond with a valid JSON object matching the provided schema."
        )

        user_prompt = (
            f"Analyze the following transcription and select {target_count} viral clips.\n\n"
            f"VIDEO DURATION: {video_duration:.1f} seconds\n\n"
            f"TRANSCRIPTION:\n{transcript}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    # ------------------------------------------------------------------
    # Response parsing and validation
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        raw_content: str,
        video_duration: float,
    ) -> ClipSelectionResult:
        """
        Parses LLM JSON output into a validated ClipSelectionResult.
        """
        try:
            data = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AIContractValidationError(
                message=f"LLM response is not valid JSON: {type(exc).__name__}",
                provider="litellm",
                raw_response=None,  # Never log raw response
            ) from exc

        try:
            result = ClipSelectionResult(**data)
        except Exception as exc:
            raise AIContractValidationError(
                message=f"LLM response does not match ClipSelectionResult schema: {exc}",
                provider="litellm",
                raw_response=None,
            ) from exc

        # Validate clips against video duration
        result.clips = self._validate_clips(result.clips, video_duration)

        return result

    @staticmethod
    def _validate_clips(
        clips: List[ViralClip],
        video_duration: float,
    ) -> List[ViralClip]:
        """
        Filters out clips whose timestamps exceed the video duration.
        Clips that start beyond the video duration are removed entirely.
        Clips whose end exceeds the duration are clamped.
        """
        validated: List[ViralClip] = []
        for clip in clips:
            if clip.start >= video_duration:
                logger.warning(
                    "⚠️ [LiteLLMSelection] Dropping clip '%s': start (%.1f) >= duration (%.1f)",
                    clip.title,
                    clip.start,
                    video_duration,
                )
                continue

            if clip.end > video_duration:
                logger.info(
                    "📐 [LiteLLMSelection] Clamping clip '%s' end from %.1f to %.1f",
                    clip.title,
                    clip.end,
                    video_duration,
                )
                clip = ViralClip(
                    start=clip.start,
                    end=video_duration,
                    title=clip.title,
                    virality_score=clip.virality_score,
                    reasoning=clip.reasoning,
                    hook_text=clip.hook_text,
                    hashtags=clip.hashtags,
                )

            validated.append(clip)

        return validated

    # ------------------------------------------------------------------
    # Usage extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_usage(
        response: Any,
        model: str,
        latency: float,
    ) -> ProviderUsage:
        """
        Extracts token usage from a LiteLLM response, including implicit cache tokens.
        """
        usage = getattr(response, "usage", None)

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # Implicit cache tracking (Gemini reports this automatically)
        cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # Determine provider name from model string
        provider = model.split("/")[0] if "/" in model else "openai"

        return ProviderUsage(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=round(latency, 3),
            cached=cache_read_tokens > 0,
        )

    # ------------------------------------------------------------------
    # Fallback logic
    # ------------------------------------------------------------------

    def _has_fallback(self) -> bool:
        """
        Returns True only if OPENAI_API_KEY is configured.
        """
        openai_key = getattr(settings, "OPENAI_API_KEY", None)
        return bool(openai_key)

    # ------------------------------------------------------------------
    # Error classification
    # ------------------------------------------------------------------

    def _classify_error(self, exc: Exception):
        """
        Maps LiteLLM and provider exceptions to the AICORE-1 error hierarchy.
        """
        status_code = getattr(exc, "status_code", None)
        message = str(exc)

        if status_code in (401, 403):
            return AIAuthenticationError(
                message=f"LLM authentication failed: {message}",
                provider="litellm",
                status_code=status_code,
                raw_error=exc,
            )

        if status_code == 429:
            return AIRateLimitError(
                message=f"LLM rate limit exceeded: {message}",
                provider="litellm",
                raw_error=exc,
            )

        if status_code is not None and 500 <= status_code < 600:
            return AIServerError(
                message=f"LLM server error: {message}",
                status_code=status_code,
                provider="litellm",
                raw_error=exc,
            )

        if isinstance(exc, (TimeoutError,)):
            return AITimeoutError(
                message=f"LLM request timed out: {message}",
                provider="litellm",
                raw_error=exc,
            )

        exc_name = type(exc).__name__.lower()
        if "timeout" in exc_name:
            return AITimeoutError(
                message=f"LLM request timed out: {message}",
                provider="litellm",
                raw_error=exc,
            )

        if isinstance(exc, (ConnectionError, OSError)):
            return AIConnectionError(
                message=f"Failed to connect to LLM provider: {message}",
                provider="litellm",
                raw_error=exc,
            )

        if "connection" in exc_name:
            return AIConnectionError(
                message=f"Failed to connect to LLM provider: {message}",
                provider="litellm",
                raw_error=exc,
            )

        # AIContractValidationError passes through
        if isinstance(exc, AIContractValidationError):
            return exc

        # Default: server error (retryable)
        return AIServerError(
            message=f"Unexpected LLM error: {message}",
            status_code=status_code or 500,
            provider="litellm",
            raw_error=exc,
        )
