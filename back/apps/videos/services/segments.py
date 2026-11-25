"""Validation of the paper-edit approved segments (shared by the view and the render task)."""
import math
from typing import Any, Dict, List


class InvalidSegmentsError(ValueError):
    """
    The approved segments do not have the expected shape. Messages are static (no user
    text). ``kind`` is ``"timestamps"`` for non-numeric/non-finite times, ``"too_long"``
    for a segment longer than ``check_segment_lengths`` allows, else ``"shape"``.
    """

    def __init__(self, message: str, kind: str = "shape"):
        super().__init__(message)
        self.kind = kind


def _as_time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidSegmentsError("Segment timestamps must be numbers.", kind="timestamps")
    try:
        # Numeric strings were accepted by the approval endpoint before; keep them.
        number = float(value)
    except ValueError:
        raise InvalidSegmentsError("Segment timestamps must be numbers.", kind="timestamps") from None
    if not math.isfinite(number):
        raise InvalidSegmentsError("Segment timestamps must be finite.", kind="timestamps")
    return number


def max_render_segment_seconds() -> float:
    """
    The longest segment a clip render finishes within its soft time limit:
    RENDER_CLIP_SOFT_TIME_LIMIT over the worst render seconds per second of
    clip (RENDER_CLIP_SECONDS_PER_SECOND, never below 1).
    """
    from django.conf import settings

    soft_limit = float(getattr(settings, 'RENDER_CLIP_SOFT_TIME_LIMIT', 2 * 3600))
    per_second = max(1.0, float(getattr(settings, 'RENDER_CLIP_SECONDS_PER_SECOND', 4.0)))
    return soft_limit / per_second


def check_segment_lengths(segments: List[Dict[str, Any]], max_seconds: float) -> None:
    """
    Raises ``InvalidSegmentsError`` (``kind="too_long"``) if a validated
    segment is longer than ``max_seconds``. The approval checks it: each
    segment renders as one clip. The paper edit of before does not.
    """
    for seg in segments:
        if seg["end"] - seg["start"] > max_seconds:
            raise InvalidSegmentsError("Segment longer than a clip render allows.", kind="too_long")


def validate_approved_segments(raw: Any) -> List[Dict[str, Any]]:
    """
    Returns the segments with float ``start``/``end`` (text kept as-is), or raises
    ``InvalidSegmentsError``. Requires a non-empty list of dicts with
    ``0 <= start < end`` and an optional string ``text``.
    """
    if not isinstance(raw, list) or not raw:
        raise InvalidSegmentsError("Segments must be a non-empty list.")

    validated: List[Dict[str, Any]] = []
    for seg in raw:
        if not isinstance(seg, dict):
            raise InvalidSegmentsError("Each segment must be an object.")
        if "start" not in seg or "end" not in seg:
            raise InvalidSegmentsError("Each segment needs 'start' and 'end'.")
        start = _as_time(seg["start"])
        end = _as_time(seg["end"])
        if start < 0 or end <= start:
            raise InvalidSegmentsError("Segment bounds must satisfy 0 <= start < end.")
        text = seg.get("text")
        if text is not None and not isinstance(text, str):
            raise InvalidSegmentsError("Segment text must be a string.")
        validated.append({**seg, "start": start, "end": end})
    return validated
