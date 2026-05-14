import textwrap
import logging
import os
import numpy as np
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator
from moviepy import TextClip, CompositeVideoClip, ImageClip
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

class WordData(BaseModel):
    text: str
    start: float
    end: float
    confidence: float = 1.0

class SubtitleSegmentV2(BaseModel):
    text: str
    start: float
    end: float
    words: List[WordData] = Field(default_factory=list)

class SubtitleConfigV2(BaseModel):
    font_path: str = "Montserrat-Bold.ttf"
    font_size_percent: float = 0.05
    user_scale_factor: float = 1.0
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFFF00"
    stroke_color: str = "#000000"
    stroke_width: int = 4
    words_per_box: int = 3
    y_position_percent: float = 0.70
    animation_type: str = "pop" # pop, none

class SubtitleEngineV2:
    """
    Advanced Subtitle Engine V2 - TikTok/Reels Style.
    Features: Chunking, Pop-in animations, Karaoke highlighting.
    """
    def __init__(self, config: SubtitleConfigV2):
        self.config = config
        self.font_abs_path = os.path.join(settings.BASE_DIR, 'assets', 'fonts', config.font_path)
        if not os.path.exists(self.font_abs_path):
            # Fallback to a default if not found
            self.font_abs_path = "Arial"

    def _chunk_segments(self, raw_segments: List[Dict[str, Any]]) -> List[SubtitleSegmentV2]:
        """
        Groups raw word-level timestamps into chunks of 'words_per_box'.
        """
        all_words = []
        for seg in raw_segments:
            if "words" in seg and seg["words"]:
                for w in seg["words"]:
                    all_words.append(WordData(**w))
            else:
                # If no word-level timestamps, treat segment as one word (fallback)
                all_words.append(WordData(text=seg["text"], start=seg["start"], end=seg["end"]))

        chunks = []
        for i in range(0, len(all_words), self.config.words_per_box):
            group = all_words[i:i + self.config.words_per_box]
            if not group: continue
            
            chunks.append(SubtitleSegmentV2(
                text=" ".join([w.text for w in group]),
                start=group[0].start,
                end=group[-1].end,
                words=group
            ))
        return chunks

    def _render_karaoke_frame(self, chunk: SubtitleSegmentV2, t: float, font_size: int, video_w: int):
        """
        Renders a multi-line subtitle frame with the current word highlighted.
        Ensures text never overflows video_w.
        """
        # 1. Setup
        active_word_index = -1
        for i, word in enumerate(chunk.words):
            if word.start <= t <= word.end:
                active_word_index = i
                break
        if active_word_index == -1:
            if t < chunk.words[0].start: active_word_index = 0
            else: active_word_index = len(chunk.words) - 1

        font = ImageFont.truetype(self.font_abs_path, font_size)
        max_content_w = int(video_w * 0.85) # 85% of screen width
        
        # 2. Layout Logic (Multi-line Word Wrap)
        lines = []
        current_line = []
        current_line_w = 0
        space_w = 15 # fixed space width for simplicity in V2
        
        temp_img = Image.new("RGBA", (video_w, 200))
        temp_draw = ImageDraw.Draw(temp_img)

        for i, word in enumerate(chunk.words):
            text = word.text.upper()
            w = temp_draw.textbbox((0, 0), text, font=font)[2]
            color = self.config.highlight_color if i == active_word_index else self.config.primary_color
            
            # If word exceeds max width alone, it's a huge word, but usually it fits.
            # If current line + word exceeds max width, move to next line.
            if current_line and (current_line_w + space_w + w) > max_content_w:
                lines.append({"words": current_line, "width": current_line_w})
                current_line = []
                current_line_w = 0
            
            if current_line:
                current_line_w += space_w
            
            current_line.append({"text": text, "width": w, "color": color})
            current_line_w += w
            
        if current_line:
            lines.append({"words": current_line, "width": current_line_w})

        # 3. Final Image Generation
        line_height = int(font_size * 1.3)
        padding = 40
        img_w = video_w # Always use full width for centering stability
        img_h = (len(lines) * line_height) + (padding * 2)
        
        # Ensure img_h is even
        img_h = img_h if img_h % 2 == 0 else img_h + 1
        
        img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        curr_y = padding + (line_height // 2)
        
        for line in lines:
            # Start X for this line to be centered
            curr_x = (img_w - line["width"]) // 2
            
            for word_obj in line["words"]:
                # Draw word with stroke
                draw.text(
                    (curr_x, curr_y), 
                    word_obj["text"], 
                    font=font, 
                    fill=word_obj["color"], 
                    stroke_width=self.config.stroke_width, 
                    stroke_fill=self.config.stroke_color, 
                    anchor="lm"
                )
                curr_x += word_obj["width"] + space_w
            
            curr_y += line_height
            
        return np.array(img)

    def generate_clips(self, video_clip, raw_segments: List[Dict[str, Any]]):
        video_w, video_h = video_clip.size
        chunks = self._chunk_segments(raw_segments)
        
        # Base font size calculation
        base_font_size = int(video_h * self.config.font_size_percent * self.config.user_scale_factor)
        # Ensure even
        base_font_size = base_font_size if base_font_size % 2 == 0 else base_font_size + 1
        
        subtitle_clips = []
        y_pos = int(video_h * self.config.y_position_percent)
        
        for chunk in chunks:
            duration = chunk.end - chunk.start
            if duration <= 0: continue
            
            # Create a clip that calls the karaoke renderer for each frame
            def make_frame(t):
                # t here is relative to the clip start
                global_t = chunk.start + t
                return self._render_karaoke_frame(chunk, global_t, base_font_size, video_w)

            # We need to pre-calculate the size or use a fixed size for the ImageClip/VideoClip
            # For simplicity in this V2, we'll use an updated VideoClip approach
            from moviepy import VideoClip
            
            # Temporary render to get size
            sample_frame = self._render_karaoke_frame(chunk, chunk.start, base_font_size, video_w)
            h, w, _ = sample_frame.shape
            
            chunk_clip = VideoClip(make_frame, duration=duration).with_start(chunk.start)
            
            # Apply Pop-in Animation
            if self.config.animation_type == "pop":
                def pop_zoom(t):
                    if t < 0.1:
                        return 0.8 + (0.2 * (t / 0.1))
                    return 1.0
                chunk_clip = chunk_clip.resized(pop_zoom)

            chunk_clip = chunk_clip.with_position(("center", y_pos))
            subtitle_clips.append(chunk_clip)
            
        return subtitle_clips
