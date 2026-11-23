"""
User selection criteria for clip selection (PTE-02, #71).

An optional free-text preference such as "momentos de humor" or "partes
educativas". It reaches the selector as user DATA: isolated in its own block
of the user message and described to the model as a preference that can never
change the rules, the output format, the number of clips or the limits.

Pure module: no Django, no network.
"""

from typing import Any, Optional

MAX_SELECTION_CRITERIA_CHARS = 300


def normalize_selection_criteria(value: Any) -> Optional[str]:
    """
    The criterion as one clean line, or None when there is none: whitespace
    (newlines included) collapses to single spaces, non-printable characters
    are dropped and the result is capped at ``MAX_SELECTION_CRITERIA_CHARS``.
    """
    if value is None:
        return None
    text = "".join(ch for ch in str(value) if ch.isprintable() or ch.isspace())
    text = " ".join(text.split())[:MAX_SELECTION_CRITERIA_CHARS].strip()
    return text or None
