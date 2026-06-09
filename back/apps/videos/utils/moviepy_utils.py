"""
MoviePy 2.0+ Rendering Defaults & Utils.
Centralizes codec, fps, and quality parameters for the entire SaaS.
"""
from decimal import Decimal

# --- RENDERING DEFAULTS ---
RENDER_FPS = 24
RENDER_CODEC = 'libx264'
RENDER_AUDIO_CODEC = 'aac'
RENDER_PRESET = 'fast'
FFMPEG_PARAMS = ['-pix_fmt', 'yuv420p', '-profile:v', 'main']

# --- LAYOUT CONSTANTS ---
REELS_ASPECT_RATIO = 9/16
SQUARE_ASPECT_RATIO = 1/1
LANDSCAPE_ASPECT_RATIO = 16/9

def ensure_even(val: int) -> int:
    """Ensures value is even for H.264 compatibility."""
    val = int(round(val))
    return val if val % 2 == 0 else val + 1
