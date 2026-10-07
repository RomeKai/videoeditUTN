import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# --- 1. STRATEGY INTERFACE ---

class ClipSelectionStrategy(ABC):
    """Abstract interface for AI selection models following the Strategy Pattern."""

    @abstractmethod
    def get_model_name(self) -> str:
        """Returns the specific model string."""
        pass

    @abstractmethod
    def get_max_tokens_context(self) -> int:
        """Returns the token limit for the context window."""
        pass

    @abstractmethod
    def select_clips(self, prompt: str) -> str:
        """Executes the AI request and returns raw JSON string."""
        pass


# --- 2. CONCRETE STRATEGIES (Gemini via LiteLLM) ---

class GeminiFlashStrategy(ClipSelectionStrategy):
    """Primary strategy: Gemini 2.0 Flash via LiteLLM. 1M context, lowest cost."""

    def get_model_name(self) -> str:
        return getattr(settings, "AI_DEFAULT_LLM_MODEL", "gemini/gemini-flash-latest")

    def get_max_tokens_context(self) -> int:
        return 1000000

    def select_clips(self, prompt: str) -> str:
        import litellm

        model = self.get_model_name()
        logger.info("🧠 Querying %s via LiteLLM", model)

        gemini_key = getattr(settings, "GEMINI_API_KEY", None)
        if gemini_key:
            litellm.api_key = gemini_key

        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": "You are a JSON API. You only respond with valid JSON objects."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        content = response.choices[0].message.content
        if content is None:
            raise ValueError(f"AI Model {model} returned an empty response.")
        return content


class GeminiFallbackStrategy(ClipSelectionStrategy):
    """Fallback strategy: Gemini 1.5 Flash via LiteLLM. Same API key, proven stability."""

    def get_model_name(self) -> str:
        return getattr(settings, "AI_FALLBACK_LLM_MODEL", "gemini/gemini-flash-lite-latest")

    def get_max_tokens_context(self) -> int:
        return 1000000

    def select_clips(self, prompt: str) -> str:
        import litellm

        model = self.get_model_name()
        logger.info("🧠 Querying fallback %s via LiteLLM", model)

        gemini_key = getattr(settings, "GEMINI_API_KEY", None)
        if gemini_key:
            litellm.api_key = gemini_key

        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": "You are a JSON API. You only respond with valid JSON objects."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        content = response.choices[0].message.content
        if content is None:
            raise ValueError(f"AI Model {model} returned an empty response.")
        return content


# --- 3. CONTEXT (SELECTION ENGINE) ---

class SelectionEngine:
    """Engine responsible for orchestrating viral moment detection using AI."""

    @staticmethod
    def _get_strategy(level: str) -> ClipSelectionStrategy:
        """
        Factory Method to select the appropriate AI strategy.

        All levels now route through Gemini via LiteLLM.
        The 'level' parameter controls model quality:
        - 'fast' (default): Gemini 2.0 Flash (low cost, high speed)
        - 'smart'/'pro'/'gemini': Gemini 2.0 Flash (same model, future-proofed for Pro tier)
        """
        api_key = getattr(settings, "GEMINI_API_KEY", None)
        if not api_key:
            raise ValueError(
                "Configuration Error: GEMINI_API_KEY is missing in settings. "
                "All LLM operations require a Gemini API key."
            )

        return GeminiFlashStrategy()

    @staticmethod
    def _calculate_clip_count(duration_seconds: Optional[float]) -> int:
        """Estimates ideal number of clips based on source duration."""
        if not duration_seconds:
            return 3

        minutes = duration_seconds / 60
        if minutes < 2: return 1
        elif minutes < 5: return 2
        elif minutes < 15: return 4
        elif minutes < 30: return 8
        else: return 12

    @staticmethod
    def select_viral_clips(
        transcription_data: Dict[str, Any],
        project_title: str,
        editing_style: str = 'dynamic',
        duration: Optional[float] = None,
        intelligence_level: str = 'fast'
    ) -> List[Dict[str, Any]]:
        """Main client method for viral clip selection."""
        # 1. Initialize strategy
        strategy = SelectionEngine._get_strategy(intelligence_level)

        # 2. Data Preparation
        target_clips = SelectionEngine._calculate_clip_count(duration)

        # Approximate character limit based on token context (avg 4 chars per token)
        char_limit = strategy.get_max_tokens_context() * 3
        full_text = str(transcription_data.get('full_text', ''))[:char_limit]

        # AI Core V2 Provider Routing
        if getattr(settings, "AI_CORE_V2_ENABLED", False) and getattr(settings, "GEMINI_API_KEY", None):
            try:
                from apps.videos.services.ai.litellm_selection import LiteLLMSelectionProvider

                logger.info("🧠 [SelectionEngine] Routing via LiteLLMSelectionProvider (AI Core V2)...")
                provider = LiteLLMSelectionProvider()
                video_dur = float(duration) if duration and duration > 0 else 300.0
                exec_result = provider.select_clips(
                    transcript=full_text,
                    video_duration=video_dur,
                    target_count=target_clips,
                    editing_style=editing_style,
                )
                return [
                    {
                        "start": clip.start,
                        "end": clip.end,
                        "title": clip.title,
                        "virality_score": clip.virality_score,
                        "reasoning": clip.reasoning,
                    }
                    for clip in exec_result.data.clips
                ]
            except Exception as e:
                logger.warning(
                    "⚠️ [SelectionEngine] AI Core V2 selection failed, falling back to strategy: %s",
                    e,
                )

        # 3. AI Prompt Construction
        prompt = f"""
        Act as an expert video editor.
        Project: "{project_title}". Editing Style: {editing_style}.
        
        Goal: Identify the top {target_clips} most viral moments from the transcript.
        
        Rules:
        - Strict JSON output.
        - Clip duration: 15-60 seconds.
        - Virality score: 0-100.
        
        Transcript:
        {full_text}
        
        Expected JSON Schema:
        {{ "clips": [ {{ "start": 0.0, "end": 10.0, "title": "...", "virality_score": 80, "reasoning": "..." }} ] }}
        """

        try:
            content = strategy.select_clips(prompt)
            # Cleanup possible markdown code blocks from AI response
            if content.startswith("```json"):
                content = content.replace("```json", "").replace("```", "").strip()

            data = json.loads(content)

            # Extract list from wrapper object if present
            clips = data.get('clips', data) if isinstance(data, dict) else data

            if not isinstance(clips, list):
                logger.warning("AI returned a non-list format for clips. Wrapping in list.")
                return [clips] if clips else []

            return clips

        except Exception as e:
            logger.error(f"❌ SelectionEngine Error: {e}")
            raise e
