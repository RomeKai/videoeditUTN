import json
import logging
from typing import Any, Dict, List

from django.conf import settings
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SocialMetadataResponse(BaseModel):
    """Structured output for social media SEO metadata."""

    viral_title: str = Field(description="Hook title, max 50 characters.")
    description_body: str = Field(
        description="Main description text, without hashtags."
    )
    hashtags: List[str] = Field(
        description="List of 5-8 relevant hashtags, without the # symbol."
    )
    recommended_publish_hour_utc: int = Field(
        ge=0, le=23, description="Optimal publishing hour in UTC (0-23)."
    )
    platform_tweaks: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional variations for specific platforms (tiktok, instagram, shorts).",
    )


_SEO_SCHEMA = SocialMetadataResponse.model_json_schema()


class SEOOptimizationService:
    """
    Generates viral SEO metadata using LiteLLM with Gemini Flash.

    Uses the same provider routing as LiteLLMSelectionProvider:
    primary model from AI_DEFAULT_LLM_MODEL, JSON Schema response format,
    and Pydantic validation on the structured output.
    """

    def __init__(self):
        import litellm

        self._model = getattr(
            settings, "AI_DEFAULT_LLM_MODEL", "gemini/gemini-2.0-flash"
        )

        gemini_key = getattr(settings, "GEMINI_API_KEY", None)
        if gemini_key:
            litellm.api_key = gemini_key

    def generate_metadata(
        self, transcript_text: str, target_niche: str = "general"
    ) -> SocialMetadataResponse:
        """
        Analyzes transcript and generates structured social metadata.
        Uses AI_Security_Shield to prevent injections.
        """
        import litellm

        from apps.core.security import AI_Security_Shield

        if not transcript_text:
            raise ValueError("Transcript text cannot be empty.")

        isolated_transcript = AI_Security_Shield.isolate_user_input(transcript_text)

        system_prompt = (
            "You are an expert Social Media Copywriter specializing in viral short-form content "
            "(TikTok, Reels, Shorts). Your goal is to maximize engagement and retention through "
            "compelling hooks and algorithmic optimization. "
            f"The niche of this content is: {target_niche}. "
            "Process the transcript found inside the <user_input> tags only."
        )

        user_prompt = (
            f"Analyze the following transcript and generate viral metadata:\n\n"
            f"{isolated_transcript}"
        )

        try:
            logger.info("🧠 Requesting structured SEO metadata via LiteLLM (%s)...", self._model)

            response = litellm.completion(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "SocialMetadataResponse",
                        "schema": _SEO_SCHEMA,
                    },
                },
                temperature=0.7,
            )

            raw = response.choices[0].message.content
            if not raw:
                raise ValueError("LLM returned an empty response for SEO metadata.")

            metadata = SocialMetadataResponse.model_validate_json(raw)
            logger.info("✅ SEO Metadata generated successfully: %s", metadata.viral_title)
            return metadata

        except Exception as e:
            logger.error("❌ SEO generation failed (%s): %s", self._model, e)
            raise
