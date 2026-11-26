import colorsys
import logging
import math
import os
import re

import cv2
import numpy as np
from django.conf import settings
from moviepy import CompositeVideoClip, VideoClip
from PIL import Image, ImageDraw, ImageFont

from .interface import BaseLayout
from .standard import FillLayout

logger = logging.getLogger(__name__)

TRENDING_DEFAULT_COLOR = "#FF2D55"
TRENDING_TEXT_MAX_CHARS = 60
# Border around the video, as a share of the shorter side of the output.
TRENDING_BORDER = 0.035
# Band for the overlay text above the video, as a share of the height
# (vertical outputs have more height to spare).
TRENDING_TEXT_BAND_VERTICAL = 0.12
TRENDING_TEXT_BAND_WIDE = 0.16
# The gradient turns once every this many seconds.
TRENDING_ROTATION_SECONDS = 6.0
# The gradient is drawn this many times smaller and scaled up: it is smooth.
_GRADIENT_DOWNSCALE = 4
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _even(value):
    value = int(value)
    return max(2, value - value % 2)


def parse_color(value):
    """An ``#RRGGBB`` color as an RGB tuple; the default color when it is not one."""
    if not isinstance(value, str) or not _HEX.match(value):
        value = TRENDING_DEFAULT_COLOR
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def companion_color(rgb):
    """The other end of the gradient: the same color with the hue turned 60 degrees and brighter."""
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    r, g, b = colorsys.hls_to_rgb((h + 1 / 6) % 1.0, min(1.0, l * 1.15 + 0.1), s)
    return tuple(int(round(c * 255)) for c in (r, g, b))


def trending_geometry(target_w, target_h, has_text):
    """
    Where the video and the text go: ``(border, band_h, inner_w, inner_h)``.
    The video sits ``border`` from every edge and below the text band
    (``band_h`` is 0 without text); its size is even.
    """
    border = max(2, _even(round(min(target_w, target_h) * TRENDING_BORDER)))
    band_h = 0
    if has_text:
        share = TRENDING_TEXT_BAND_VERTICAL if target_w / target_h < 0.75 else TRENDING_TEXT_BAND_WIDE
        band_h = _even(round(target_h * share))
    inner_w = _even(target_w - 2 * border)
    inner_h = _even(target_h - 2 * border - band_h)
    return border, band_h, inner_w, inner_h


def gradient_frame(width, height, color_a, color_b, angle):
    """A linear gradient from ``color_a`` to ``color_b`` along ``angle`` (radians), RGB uint8."""
    small_w = max(2, width // _GRADIENT_DOWNSCALE)
    small_h = max(2, height // _GRADIENT_DOWNSCALE)
    xs = np.linspace(-1.0, 1.0, small_w, dtype=np.float32)[None, :]
    ys = np.linspace(-1.0, 1.0, small_h, dtype=np.float32)[:, None]
    mix = (math.cos(angle) * xs + math.sin(angle) * ys) / math.sqrt(2) * 0.5 + 0.5
    a = np.array(color_a, dtype=np.float32)
    b = np.array(color_b, dtype=np.float32)
    small = a + (b - a) * mix[..., None]
    small = np.clip(small, 0, 255).astype(np.uint8)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def _font(size):
    path = os.path.join(settings.FONTS_DIR, "Montserrat-Bold.ttf")
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def render_text(text, max_w, band_h):
    """
    The overlay text as an RGBA image of ``max_w x band_h``: white with a dark
    outline, centred, as large as fits (the size goes down until it does).
    """
    image = Image.new("RGBA", (max_w, band_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    size = max(8, int(band_h * 0.55))
    while True:
        font = _font(size)
        stroke = max(1, size // 12)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if right - left <= max_w * 0.92 or size <= 8:
            break
        size -= 2
    x = (max_w - (right - left)) // 2 - left
    y = (band_h - (bottom - top)) // 2 - top
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return np.array(image)


class TrendingLayout(BaseLayout):
    """
    Layout 'Trending' (CORE-03, TikTok trends): the video framed by a
    gradient border that turns slowly, with an optional text above it.

    ``layout_options``: ``trending_color`` (``#RRGGBB``, one end of the
    gradient; the other end is the same color with the hue turned) and
    ``trending_text`` (up to TRENDING_TEXT_MAX_CHARS; empty means no text
    band). The video inside is the ``fill`` crop, so face tracking follows
    the face as in ``fill``. Emojis are not drawn: the font has no glyphs
    for them.
    """
    def apply(self, clip):
        target_w, target_h = self.target_w, self.target_h
        color_a = parse_color(self.layout_options.get("trending_color"))
        color_b = companion_color(color_a)
        text = str(self.layout_options.get("trending_text") or "").strip()[:TRENDING_TEXT_MAX_CHARS]

        border, band_h, inner_w, inner_h = trending_geometry(target_w, target_h, bool(text))
        text_rgba = render_text(text, inner_w, band_h) if text else None

        def background(t):
            angle = 2 * math.pi * (t / TRENDING_ROTATION_SECONDS)
            frame = gradient_frame(target_w, target_h, color_a, color_b, angle)
            if text_rgba is not None:
                region = frame[border:border + band_h, border:border + inner_w].astype(np.float32)
                alpha = text_rgba[..., 3:4].astype(np.float32) / 255.0
                region = region * (1 - alpha) + text_rgba[..., :3].astype(np.float32) * alpha
                frame[border:border + band_h, border:border + inner_w] = region.astype(np.uint8)
            return frame

        bg = VideoClip(background, duration=clip.duration)

        video = FillLayout(
            inner_w, inner_h, use_facetracking=self.use_facetracking,
            editing_style=self.editing_style, static=self.static,
        ).apply(clip)
        video = video.with_position((border, border + band_h))

        final = CompositeVideoClip([bg, video], size=(target_w, target_h))
        if clip.audio:
            final = final.with_audio(clip.audio)
        logger.info("layout.trending text=%s", bool(text))
        return final
