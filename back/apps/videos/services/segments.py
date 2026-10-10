"""Validation of the paper-edit approved segments (shared by the view and the render task)."""
import math
from typing import Any, Dict, List


class InvalidSegmentsError(ValueError):
    """The approved segments do not have the expected shape. Messages are static: no user text."""


def _as_time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidSegmentsError("Segment timestamps must be numbers.")
    try:
        # Numeric strings were accepted by the approval endpoint before; keep them.
        number = float(value)
    except ValueError:
        raise InvalidSegmentsError("Segment timestamps must be numbers.") from None
    if not math.isfinite(number):
        raise InvalidSegmentsError("Segment timestamps must be finite.")
    return number


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
