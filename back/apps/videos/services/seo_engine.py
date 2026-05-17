import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from django.conf import settings
from openai import OpenAI

logger = logging.getLogger(__name__)

class SocialMetadataResponse(BaseModel):
    """
    Structured output for social media SEO metadata.
    Enforced by OpenAI Structured Outputs.
    """
    viral_title: str = Field(description="Hook title, max 50 characters.")
    description_body: str = Field(description="Main description text, without hashtags.")
    hashtags: List[str] = Field(description="List of 5-8 relevant hashtags, without the # symbol.")
    recommended_publish_hour_utc: int = Field(ge=0, le=23, description="Optimal publishing hour in UTC (0-23).")
    platform_tweaks: Dict[str, str] = Field(
        default_factory=dict, 
        description="Optional variations for specific platforms (tiktok, instagram, shorts)."
    )

class SEOOptimizationService:
    """
    Service for generating viral SEO metadata using LLMs.
    Uses OpenAI gpt-4o-mini with Structured Outputs for efficiency.
    """
    
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = "gpt-4o-mini" # User requested efficiency; gpt-4o-mini is perfect for this.

    def generate_metadata(self, transcript_text: str, target_niche: str = "general") -> SocialMetadataResponse:
        """
        Analyzes transcript and generates structured social metadata.
        """
        if not transcript_text:
            raise ValueError("Transcript text cannot be empty.")

        system_prompt = (
            "You are an expert Social Media Copywriter specializing in viral short-form content (TikTok, Reels, Shorts). "
            "Your goal is to maximize engagement and retention through compelling hooks and algorithmic optimization. "
            f"The niche of this content is: {target_niche}."
        )
        
        user_prompt = f"Analyze the following transcript and generate viral metadata:\n\n{transcript_text}"

        try:
            logger.info("🧠 Requesting structured SEO metadata from OpenAI...")
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=SocialMetadataResponse,
            )

            # Extract the structured response
            metadata = completion.choices[0].message.parsed
            logger.info(f"✅ SEO Metadata generated successfully: {metadata.viral_title}")
            return metadata

        except Exception as e:
            logger.error(f"❌ OpenAI SEO generation failed: {e}")
            raise e
