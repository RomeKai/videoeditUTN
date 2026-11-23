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

Anchored mode (AICORE-16, #68): when word-level timestamps are given, every
clip is also anchored to the audio. Without them (segment-level or no
transcript) only the rules above apply, as before.

5. Word snapping: the start moves to the nearest word start and the end to the
   nearest word end within ``snap_tolerance_seconds``. A boundary that falls
   inside a word always moves to that word's edge: no word is ever cut.
6. Target duration ``[min_seconds, max_seconds]`` (15-60 s by default) using
   natural boundaries: the end of a word followed by a pause of at least
   ``pause_seconds``, a sentence end (``.``, ``?``, ``!``, ``…``) or the last word.
   - Too long: the end moves back to the last natural boundary within the max.
   - Too short: the end moves forward to the next natural boundary that reaches
     the min, or else the start moves back to the closest natural start, as
     long as the result does not overlap a higher-scored clip.
   - Otherwise the clip is dropped and counted (``duration_unmet``).

Survivors keep the order the model returned them in. Only counts are logged,
never titles, reasoning or any other model output (log privacy, PR #56).
"""

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from pydantic import ValidationError

from apps.videos.services.ai.contracts import ViralClip

logger = logging.getLogger(__name__)

# Shorter clips are degenerate (e.g. a 0.2 s tail left after clamping).
MIN_CLIP_SECONDS = 1.0

_SENTENCE_END = (".", "!", "?", "…")
# Below this share of whitespace-free items the transcript is segment-level.
_WORD_LEVEL_SHARE = 0.9
_EPSILON = 1e-6


@dataclass(frozen=True)
class ClipRules:
    """Boundary rules of anchored mode (settings ``AI_CLIP_*``)."""

    min_seconds: float = 15.0
    max_seconds: float = 60.0
    snap_tolerance_seconds: float = 1.0
    pause_seconds: float = 0.5

    def __post_init__(self):
        values = (self.min_seconds, self.max_seconds, self.snap_tolerance_seconds, self.pause_seconds)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values):
            raise ValueError("Clip rules must be finite, non-negative numbers.")
        if self.min_seconds <= 0 or self.max_seconds < self.min_seconds:
            raise ValueError("Clip rules need 0 < min_seconds <= max_seconds.")

    @classmethod
    def from_settings(cls) -> "ClipRules":
        from django.conf import settings

        return cls(
            min_seconds=float(getattr(settings, "AI_CLIP_MIN_SECONDS", cls.min_seconds)),
            max_seconds=float(getattr(settings, "AI_CLIP_MAX_SECONDS", cls.max_seconds)),
            snap_tolerance_seconds=float(
                getattr(settings, "AI_CLIP_SNAP_TOLERANCE_SECONDS", cls.snap_tolerance_seconds)
            ),
            pause_seconds=float(getattr(settings, "AI_CLIP_PAUSE_SECONDS", cls.pause_seconds)),
        )


@dataclass(frozen=True)
class _Word:
    start: float
    end: float
    natural_start: bool  # a natural boundary right before the word
    natural_end: bool  # a natural boundary right after the word


def word_timeline(
    items: Optional[Sequence[Any]], pause_seconds: float, video_duration: Optional[float] = None
) -> Optional[List[_Word]]:
    """
    Word timeline with natural boundaries, or None when ``items`` are not
    word-level (segments, plain text or nothing): anchored mode needs words.
    """
    parsed = []
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        try:
            start, end = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if not text or not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            continue
        if video_duration and start >= video_duration:
            continue
        parsed.append((start, end, text))
    if not parsed:
        return None
    single = sum(1 for _, _, text in parsed if not any(ch.isspace() for ch in text))
    if single < _WORD_LEVEL_SHARE * len(parsed):
        return None

    parsed.sort(key=lambda p: p[0])
    natural_end = []
    for i, (_, end, text) in enumerate(parsed):
        last = i == len(parsed) - 1
        natural_end.append(
            last or text.endswith(_SENTENCE_END) or parsed[i + 1][0] - end >= pause_seconds
        )
    return [
        _Word(start, end, i == 0 or natural_end[i - 1], natural_end[i])
        for i, (start, end, _) in enumerate(parsed)
    ]


def _snap_start(value: float, words: List[_Word], tolerance: float) -> float:
    nearest = min(words, key=lambda w: abs(w.start - value))
    if abs(nearest.start - value) <= tolerance:
        return nearest.start
    for word in words:  # inside a word: never cut it
        if word.start < value < word.end:
            return word.start
    return value


def _snap_end(value: float, words: List[_Word], tolerance: float) -> float:
    nearest = min(words, key=lambda w: abs(w.end - value))
    if abs(nearest.end - value) <= tolerance:
        return nearest.end
    for word in words:
        if word.start < value < word.end:
            return word.end
    return value


def _fit_duration(
    clip: ViralClip,
    words: List[_Word],
    rules: ClipRules,
    min_length: float,
    accepted: Sequence[ViralClip],
) -> Tuple[Optional[ViralClip], str]:
    """The clip inside ``[min_length, max_seconds]`` at natural boundaries, and how."""
    length = clip.end - clip.start
    if min_length - _EPSILON <= length <= rules.max_seconds + _EPSILON:
        return clip, "ok"

    if length > rules.max_seconds:
        ends = [
            w.end for w in words
            if w.natural_end and clip.start + min_length - _EPSILON <= w.end
            and w.end <= clip.start + rules.max_seconds + _EPSILON
        ]
        if not ends:
            return None, "unmet"
        return clip.model_copy(update={"end": max(ends)}), "trimmed"

    def fits(candidate: ViralClip) -> bool:
        return not any(_overlaps(candidate, other) for other in accepted)

    forward = [
        w.end for w in words
        if w.natural_end and w.end >= clip.start + min_length - _EPSILON
        and w.end - clip.start <= rules.max_seconds + _EPSILON
    ]
    if forward:
        candidate = clip.model_copy(update={"end": min(forward)})
        if fits(candidate):
            return candidate, "extended"
    backward = [
        w.start for w in words
        if w.natural_start and w.start <= clip.end - min_length + _EPSILON
        and clip.end - w.start <= rules.max_seconds + _EPSILON
    ]
    if backward:
        candidate = clip.model_copy(update={"start": max(backward)})
        if fits(candidate):
            return candidate, "extended"
    return None, "unmet"


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
    # Anchored mode only (word-level timestamps).
    anchored: bool = False
    snapped: int = 0
    extended: int = 0
    trimmed: int = 0
    duration_unmet: int = 0

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
    words: Optional[Sequence[Any]] = None,
    rules: Optional[ClipRules] = None,
) -> Tuple[List[ViralClip], ClipSanitationReport]:
    """
    Returns the renderable clips among ``candidates`` (dicts or ViralClip) and a
    report of what was discarded. ``video_duration`` None skips the range checks
    (legacy callers that could not resolve it); a non-positive ``target_count``
    means no cap. ``words`` (the transcript items with ``start``/``end``/``text``)
    turns on anchored mode when they are word-level; ``rules`` defaults to the
    ``AI_CLIP_*`` settings.
    """
    report = ClipSanitationReport()
    in_range: List[Tuple[int, ViralClip]] = []

    timeline = None
    if words:
        rules = rules or ClipRules.from_settings()
        timeline = word_timeline(words, rules.pause_seconds, video_duration)
    report.anchored = timeline is not None
    if timeline:
        span = timeline[-1].end - timeline[0].start
        if video_duration is not None and video_duration > 0:
            span = min(span, float(video_duration))
        # A video shorter than the minimum can still give one whole clip.
        anchored_min = min(rules.min_seconds, span)

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

        if timeline:
            start = _snap_start(clip.start, timeline, rules.snap_tolerance_seconds)
            end = _snap_end(clip.end, timeline, rules.snap_tolerance_seconds)
            if video_duration is not None and video_duration > 0:
                end = min(end, float(video_duration))
            if (start, end) != (clip.start, clip.end):
                report.snapped += 1
            if end <= start:
                # Snapped onto a single point: let the duration rule extend it.
                end = start
            clip = clip.model_copy(update={"start": start, "end": end})
        elif clip.end - clip.start < min_length:
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
        if timeline:
            # Higher scores are fitted first, so they keep priority on the timeline.
            others = [other for _, other in accepted]
            fitted, outcome = _fit_duration(clip, timeline, rules, anchored_min, others)
            if fitted is None:
                report.duration_unmet += 1
                continue
            if outcome == "extended":
                report.extended += 1
            elif outcome == "trimmed":
                report.trimmed += 1
            clip = fitted
        accepted.append((index, clip))

    accepted.sort(key=lambda item: item[0])
    clips = [clip for _, clip in accepted]
    report.kept = len(clips)

    adjusted = report.clamped + report.snapped + report.extended + report.trimmed
    if report.dropped or adjusted:
        logger.info(
            "selection.clips_sanitized received=%d kept=%d invalid=%d out_of_range=%d "
            "too_short=%d overlapping=%d excess=%d clamped=%d anchored=%s snapped=%d "
            "extended=%d trimmed=%d duration_unmet=%d",
            report.received,
            report.kept,
            report.invalid,
            report.out_of_range,
            report.too_short,
            report.overlapping,
            report.excess,
            report.clamped,
            report.anchored,
            report.snapped,
            report.extended,
            report.trimmed,
            report.duration_unmet,
        )
    return clips, report
