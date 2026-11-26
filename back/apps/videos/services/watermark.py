"""
PAY-01: the OneCreator logo that a plan with ``has_watermark`` renders over every
delivered video.

The server decides, at render time, from the workspace's plan: nothing in the
request can turn it off. If the logo cannot be applied the render fails (the
reservation is refunded) instead of delivering the video unmarked.
"""
import logging
import os

from django.conf import settings
from moviepy import CompositeVideoClip, ImageClip

logger = logging.getLogger(__name__)

WATERMARK_OPACITY = 0.4
# Share of the clip width the logo takes, and the gap to the corner.
WATERMARK_WIDTH_RATIO = 0.15
WATERMARK_MARGIN_RATIO = 0.03


class WatermarkUnavailableError(RuntimeError):
    """The logo is missing or unreadable, so a watermarked plan cannot be rendered."""


def project_needs_watermark(project):
    """
    True when the project's workspace plan has a watermark. A workspace without a
    plan has no limits (as in PricingEngine), so it renders without one.
    """
    plan = project.workspace.subscription_plan
    return bool(plan and plan.has_watermark)


def apply_watermark(clip):
    """Returns ``clip`` with the logo in the bottom right corner, for its whole duration."""
    path = settings.WATERMARK_PATH
    if not os.path.isfile(path):
        logger.error("render.watermark_missing")
        raise WatermarkUnavailableError("The watermark logo is not available.")
    try:
        width = max(2, round(clip.w * WATERMARK_WIDTH_RATIO))
        logo = ImageClip(path).resized(width=width).with_opacity(WATERMARK_OPACITY).with_duration(clip.duration)
        margin = round(clip.w * WATERMARK_MARGIN_RATIO)
        logo = logo.with_position((clip.w - logo.w - margin, clip.h - logo.h - margin))
        stamped = CompositeVideoClip([clip, logo], size=clip.size).with_duration(clip.duration)
    except Exception as exc:
        logger.error("render.watermark_failed error=%s", type(exc).__name__)
        raise WatermarkUnavailableError("The watermark logo could not be applied.") from exc
    if clip.audio is not None:
        stamped = stamped.with_audio(clip.audio)
    return stamped
