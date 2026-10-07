"""
Timestamped transcript formatter for LLM clip selection (AICORE-6).

Turns word- or segment-level ASR output into rigid ``[START-END] text`` lines so
the LLM can only choose cut points that exist in the source audio.

Pure module: no Django, no network, no heavy imports. Safe to import anywhere.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Line contract shared with the system prompt. Changing it requires updating
# LINE_FORMAT_DESCRIPTION and the prompt rules in litellm_selection.py.
LINE_FORMAT_DESCRIPTION = "[START-END] text   (START and END are seconds with 2 decimals)"

_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_END = (".", "!", "?", "…")


@dataclass(frozen=True)
class TimestampedTranscript:
    """Formatted transcript plus the metadata needed to validate LLM output."""

    text: str
    line_count: int
    first_start: Optional[float]
    last_end: Optional[float]
    truncated: bool

    @property
    def is_empty(self) -> bool:
        return self.line_count == 0


def _sanitize_text(raw: Any) -> str:
    """
    Removes characters that could break or spoof the line structure:
    newlines would create fake lines and square brackets could fake a
    ``[START-END]`` prefix injected through the audio itself.
    """
    text = str(raw or "")
    text = text.replace("[", "(").replace("]", ")")
    return _WHITESPACE_RE.sub(" ", text).strip()


def _coerce_item(item: Mapping[str, Any]) -> Optional[Tuple[float, float, str]]:
    try:
        start = float(item.get("start"))
        end = float(item.get("end"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end < start:
        return None
    text = _sanitize_text(item.get("text"))
    if not text:
        return None
    return start, end, text


def build_timestamped_transcript(
    items: Iterable[Mapping[str, Any]],
    max_block_seconds: float = 10.0,
    max_gap_seconds: float = 1.5,
    min_sentence_block_seconds: float = 3.0,
    max_chars: int = 400_000,
) -> TimestampedTranscript:
    """
    Groups ASR items (words or segments with ``start``, ``end``, ``text``)
    into lines of at most ``max_block_seconds``.

    A block is closed when:
    - adding the next item would exceed ``max_block_seconds``;
    - there is a silence longer than ``max_gap_seconds`` (natural cut point);
    - the block ends a sentence and already lasts ``min_sentence_block_seconds``.

    ``max_chars`` caps the output so the prompt fits the smallest context window
    in the fallback chain (gpt-4o-mini: 128K tokens); truncation is logged.
    """
    parsed = [p for p in (_coerce_item(i) for i in items if isinstance(i, Mapping)) if p]
    parsed.sort(key=lambda p: p[0])

    lines: List[str] = []
    total_chars = 0
    truncated = False
    first_start: Optional[float] = None
    last_end: Optional[float] = None

    block_start: Optional[float] = None
    block_end = 0.0
    block_words: List[str] = []

    def flush() -> bool:
        nonlocal total_chars, first_start, last_end, block_start, block_words
        if block_start is None:
            return True
        line = f"[{block_start:.2f}-{block_end:.2f}] {' '.join(block_words)}"
        if total_chars + len(line) + 1 > max_chars:
            return False
        lines.append(line)
        total_chars += len(line) + 1
        first_start = block_start if first_start is None else first_start
        last_end = block_end
        block_start, block_words = None, []
        return True

    for start, end, text in parsed:
        if block_start is not None:
            too_long = end - block_start > max_block_seconds
            gap = start - block_end > max_gap_seconds
            sentence_done = (
                block_words[-1].endswith(_SENTENCE_END)
                and block_end - block_start >= min_sentence_block_seconds
            )
            if too_long or gap or sentence_done:
                if not flush():
                    truncated = True
                    break
        if block_start is None:
            block_start = start
        block_end = max(block_end, end) if block_words else end
        block_words.append(text)
    else:
        if not flush():
            truncated = True

    if truncated:
        logger.warning(
            "transcript_formatter.truncated lines=%d max_chars=%d last_end=%s",
            len(lines),
            max_chars,
            last_end,
        )

    return TimestampedTranscript(
        text="\n".join(lines),
        line_count=len(lines),
        first_start=first_start,
        last_end=last_end,
        truncated=truncated,
    )
