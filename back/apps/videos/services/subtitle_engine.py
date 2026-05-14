import textwrap
import logging
from typing import List, Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field, field_validator
from moviepy import TextClip, CompositeVideoClip
from django.conf import settings
import os

logger = logging.getLogger(__name__)

class SubtitleSegment(BaseModel):
    """
    Pydantic model representing a single subtitle segment with timing, text, and metadata.
    Consumes OpenAI enriched JSON.
    """
    start: float
    end: float
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class StyleConfig(BaseModel):
    """
    Pydantic model for subtitle styling configuration.
    Defines the visual appearance and relative positioning of subtitles.
    """
    font_path: str
    font_size_percent: float = Field(default=0.05, description="Font size as a percentage of target height (0.01 to 0.2)")
    primary_color: str = Field(default="white")
    stroke_color: str = Field(default="black")
    stroke_width: float = Field(default=1.5, ge=0.0)
    y_position_percent: float = Field(default=0.65, description="Vertical position as percentage of height (0.0 to 1.0)")

    @field_validator("font_size_percent", "y_position_percent")
    @classmethod
    def validate_percentages(cls, value: float) -> float:
        """Ensures percentage values are within the valid 0.0 to 1.0 range."""
        if not 0 <= value <= 1:
            raise ValueError("Percentage must be between 0 and 1")
        return value

class SubtitleEngine:
    """
    Advanced Subtitle Engine for Viral SaaS.
    Responsible for processing subtitle segments and generating MoviePy TextClips.
    Follows SOLID principles, MoviePy 2.0+ standards, and H.264 compatibility requirements.
    """

    def __init__(self, style_config: StyleConfig):
        """
        Initializes the engine with a specific style configuration.
        """
        self.style = style_config
        # Ensure font path is absolute if it's relative to assets
        if not os.path.isabs(self.style.font_path):
            self.style.font_path = os.path.join(settings.BASE_DIR, 'assets', 'fonts', self.style.font_path)

    @staticmethod
    def validate_even_dimension(value: float) -> int:
        """
        Utility to ensure that calculated dimensions and positions are even numbers (H.264 requirement).
        """
        val = int(round(value))
        return val if val % 2 == 0 else max(2, val - 1)

    def _wrap_text(self, text: str, max_chars: int = 25) -> str:
        """
        Applies word wrapping to long subtitle strings to ensure readability.
        """
        if not text:
            return ""
        return textwrap.fill(text, width=max_chars)

    def generate_subtitle_clips(
        self, 
        segments: List[SubtitleSegment], 
        target_w: int, 
        target_h: int
    ) -> List[TextClip]:
        """
        Transforms a list of SubtitleSegments into positioned MoviePy TextClips.
        FORCED FIX: Uses method='caption' and bounding box to prevent horizontal overflow.
        """
        # 1. Calculate dynamic font size based on target height
        font_size = self.validate_even_dimension(target_h * self.style.font_size_percent)
        
        # 2. Enforce Social Media Safe Zones (TikTok/Reels compliance)
        safe_y_limit = 0.70
        y_pos_percent = min(self.style.y_position_percent, safe_y_limit)
        y_pos = self.validate_even_dimension(target_h * y_pos_percent)
        
        # 3. CRITICAL FIX: Bounding box (85% of width)
        # This margin ensures text never touches the screen edges.
        max_clip_width = self.validate_even_dimension(target_w * 0.85)
        
        clips: List[TextClip] = []
        
        for segment in segments:
            if not segment.text or not segment.text.strip():
                continue
            
            # Note: Word Wrap is now handled NATIVELY by MoviePy/ImageMagick
            duration = segment.end - segment.start
            if duration <= 0:
                continue
            
            try:
                # MoviePy 2.0+ TextClip with 'caption' method
                # This combination (size with None height + method='caption')
                # forces automatic line breaks and vertical centering within the box.
                clip = TextClip(
                    text=segment.text,
                    font=self.style.font_path,
                    font_size=font_size,
                    color=self.style.primary_color,
                    stroke_color=self.style.stroke_color,
                    stroke_width=self.style.stroke_width,
                    method='caption',
                    size=(max_clip_width, None),
                    text_align="center"
                )
                
                clip = (
                    clip
                    .with_start(segment.start)
                    .with_duration(duration)
                    .with_position(("center", y_pos))
                )
                
                clips.append(clip)
            except Exception as e:
                logger.error(f"Error creating TextClip for segment '{segment.text}': {e}")
                
        return clips

    def add_subtitles(self, video_clip, segments_data: List[Dict[str, Any]]):
        """
        Main entry point for the engine.
        Args:
            video_clip: The base MoviePy VideoClip.
            segments_data: List of dictionaries from OpenAI JSON.
        """
        # Parse segments into Pydantic models
        segments = [SubtitleSegment(**seg) for seg in segments_data]
        
        target_w, target_h = video_clip.size
        subtitle_clips = self.generate_subtitle_clips(segments, target_w, target_h)
        
        if not subtitle_clips:
            return video_clip

        final_composition = CompositeVideoClip([video_clip] + subtitle_clips, size=video_clip.size)
        final_composition.duration = video_clip.duration
        
        return final_composition
