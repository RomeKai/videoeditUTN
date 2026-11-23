"""
Clip candidate sanitation shared by the V2 selection provider and the legacy
selector (kill switch), so both paths persist clips under the same rules.

The prompt asks the LLM for non-overlapping clips inside the video, but nothing
forces it to comply. Each candidate is validated on its own: one malformed clip
no longer discards an answer that was already paid for, and the clips that do
survive are guaranteed to be renderable:

1. Valid ``ViralClip`` (numbers, title, reasoning, score 0-100, end > start).
2. Inside the video: dropped when it starts at/after the end, clamped when it
   ends after it, dropped when what remains is shorter than ``MIN_CLIP_SECONDS``.
3. Non-overlapping: on conflict the higher ``virality_score`` wins.
4. At most ``target_count`` clips (highest scores first).

Survivors keep the order the model returned them in. Only counts are logged,
never titles, reasoning or any other model output (log privacy, PR #56).
"""

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Tuple

from pydantic import ValidationError

from apps.videos.services.ai.contracts import ViralClip

logger = logging.getLogger(__name__)

# Shorter clips are degenerate (e.g. a 0.2 s tail left after clamping).
MIN_CLIP_SECONDS = 1.0


@dataclass
class ClipSanitationReport:
    """How many candidates were dropped or adjusted, and why (counts only)."""

    received: int = 0
    invalid: int = 0
    out_of_range: int = 0
    too_short: int = 0
    overlapping: int = 0
    excess: int = 0
    clamped: int = 0
    kept: int = 0

    @property
    def dropped(self) -> int:
        return self.received - self.kept


def _overlaps(a: ViralClip, b: ViralClip) -> bool:
    # Touching clips (a.end == b.start) do not overlap.
    return a.start < b.end and b.start < a.end


def _to_clip(candidate: Any) -> Optional[ViralClip]:
    if isinstance(candidate, ViralClip):
        clip = candidate
    elif isinstance(candidate, Mapping):
        try:
            clip = ViralClip.model_validate(dict(candidate))
        except (ValidationError, TypeError, ValueError):
            return None
    else:
        return None
    # json.loads accepts NaN/Infinity. An infinite end is still clamped to the
    # video duration below; a NaN or infinite start can never be rendered.
    if not math.isfinite(clip.start) or math.isnan(clip.end):
        return None
    return clip


def sanitize_clips(
    candidates: Iterable[Any],
    video_duration: Optional[float],
    target_count: Optional[int] = None,
) -> Tuple[List[ViralClip], ClipSanitationReport]:
    """
    Returns the renderable clips among ``candidates`` (dicts or ViralClip) and a
    report of what was discarded. ``video_duration`` None skips the range checks
    (legacy callers that could not resolve it); a non-positive ``target_count``
    means no cap.
    """
    report = ClipSanitationReport()
    in_range: List[Tuple[int, ViralClip]] = []

    for index, candidate in enumerate(candidates):
        report.received += 1
        clip = _to_clip(candidate)
        if clip is None:
            report.invalid += 1
            continue

        if video_duration is not None and video_duration > 0:
            if clip.start >= video_duration:
                report.out_of_range += 1
                continue
            if clip.end > video_duration:
                clip = clip.model_copy(update={"end": float(video_duration)})
                report.clamped += 1
            min_length = min(MIN_CLIP_SECONDS, float(video_duration))
        elif math.isinf(clip.end):
            report.invalid += 1
            continue
        else:
            min_length = MIN_CLIP_SECONDS

        if clip.end - clip.start < min_length:
            report.too_short += 1
            continue

        in_range.append((index, clip))

    cap = target_count if isinstance(target_count, int) and target_count > 0 else None
    # Highest score first; ties keep the model's order (sorted is stable).
    ranked = sorted(in_range, key=lambda item: -item[1].virality_score)
    accepted: List[Tuple[int, ViralClip]] = []
    for index, clip in ranked:
        if any(_overlaps(clip, other) for _, other in accepted):
            report.overlapping += 1
            continue
        if cap is not None and len(accepted) >= cap:
            report.excess += 1
            continue
        accepted.append((index, clip))

    accepted.sort(key=lambda item: item[0])
    clips = [clip for _, clip in accepted]
    report.kept = len(clips)

    if report.dropped or report.clamped:
        logger.info(
            "selection.clips_sanitized received=%d kept=%d invalid=%d out_of_range=%d "
            "too_short=%d overlapping=%d excess=%d clamped=%d",
            report.received,
            report.kept,
            report.invalid,
            report.out_of_range,
            report.too_short,
            report.overlapping,
            report.excess,
            report.clamped,
        )
    return clips, report
