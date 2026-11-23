"""
Offline evaluation of the AI pilot's transcription (AICORE-15, #67).

Implements the two automatic measurements of the pilot's G1 gate (runbook
§1.1, AICORE-9 #43):

- WER of a hypothesis transcript against a human-corrected reference, after
  the normalization of runbook §3.3 (lowercase, no punctuation, numbers as
  digits, collapsed spaces, accents kept or stripped by a recorded decision).
- Timestamp error of sentence-start anchors (runbook §3.2): each anchor is
  matched to the hypothesis words that start it, inside a time window.

Pure module: no Django, no network, no dependency outside the standard
library. The report types carry numbers only, never reference or hypothesis
text.

Limits of the number normalization: integers written in Spanish words (up to
the millions) become digits on both sides; ordinals, decimals and "un"/"una"
used as articles are left as words. Both sides get the same treatment, so a
missed conversion only matters when one side used digits and the other words.
"""

import bisect
import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Normalization (runbook §3.3)
# ---------------------------------------------------------------------------

# Acute, grave, diaeresis and circumflex are "tildes"; the tilde of "ñ"
# (U+0303) is part of a letter and is always kept.
_ACCENT_MARKS = frozenset({"́", "̀", "̈", "̂"})
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d)[.,](?=\d{3}(?!\d))")
_NOT_WORD = re.compile(r"[^\w\s]|_")


@dataclass(frozen=True)
class Token:
    """A normalized word and the time it starts at (None for references)."""

    text: str
    start: Optional[float] = None


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(char for char in decomposed if char not in _ACCENT_MARKS)
    return unicodedata.normalize("NFC", kept)


def _split_words(text: str) -> List[str]:
    text = unicodedata.normalize("NFC", text).casefold()
    text = _THOUSANDS_SEPARATOR.sub("", text)  # "1.000" -> "1000"
    return _NOT_WORD.sub(" ", text).split()


def normalize_tokens(tokens: Iterable[Token], keep_accents: bool) -> List[Token]:
    """Normalizes timed words; a word that splits ("co-founder") keeps its start."""
    words: List[Token] = []
    for token in tokens:
        for piece in _split_words(token.text):
            words.append(Token(piece if keep_accents else strip_accents(piece), token.start))
    return _numbers_to_digits(words)


def normalize_text(text: str, keep_accents: bool) -> List[str]:
    return [token.text for token in normalize_tokens([Token(text)], keep_accents)]


# --- Spanish number words -> digits ------------------------------------------

_UNITS = {
    "cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
}
_TEENS = {
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintiuna": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_TENS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
_HUNDREDS = {"cien": 100, "ciento": 100}
for _prefix, _value in (
    ("dos", 200), ("tres", 300), ("cuatro", 400), ("quin", 500), ("seis", 600),
    ("sete", 700), ("ocho", 800), ("nove", 900),
):
    _stem = "quinient" if _prefix == "quin" else f"{_prefix}cient"
    _HUNDREDS[f"{_stem}os"] = _value
    _HUNDREDS[f"{_stem}as"] = _value
_THOUSAND = frozenset({"mil"})
_MILLION = frozenset({"millon", "millones"})
_ARTICLE_ONE = frozenset({"un", "una"})  # a number only before "millón"

# Which kind of word may follow which inside one number.
_NEXT = {
    None: {"unit", "teen", "ten", "hundred", "thousand"},
    "hundred": {"unit", "teen", "ten", "thousand", "million"},
    "ten": {"thousand", "million"},  # units only through "y"
    "teen": {"thousand", "million"},
    "unit": {"thousand", "million"},
    "thousand": {"unit", "teen", "ten", "hundred"},
    "million": {"unit", "teen", "ten", "hundred", "thousand"},
}


def _kind(key: str) -> Optional[Tuple[str, int]]:
    if key in _UNITS:
        return "unit", _UNITS[key]
    if key in _TEENS:
        return "teen", _TEENS[key]
    if key in _TENS:
        return "ten", _TENS[key]
    if key in _HUNDREDS:
        return "hundred", _HUNDREDS[key]
    if key in _THOUSAND:
        return "thousand", 1000
    if key in _MILLION:
        return "million", 1_000_000
    return None


def _number_at(keys: Sequence[str], start: int) -> Optional[Tuple[int, int]]:
    """Longest well-formed Spanish integer starting at ``start``: (value, words used)."""
    total, group, prev, i = 0, 0, None, start
    if keys[i] in _ARTICLE_ONE:
        if i + 1 < len(keys) and keys[i + 1] in _MILLION:
            group, prev, i = 1, "unit", i + 1
        else:
            return None
    while i < len(keys):
        key = keys[i]
        if prev == "ten" and key == "y" and i + 1 < len(keys) and keys[i + 1] in _UNITS \
                and keys[i + 1] != "cero":
            group += _UNITS[keys[i + 1]]
            prev, i = "unit", i + 2
            continue
        kind = _kind(key)
        if kind is None or kind[0] not in _NEXT[prev]:
            break
        name, value = kind
        if name == "thousand":
            total += max(group, 1) * 1000
            group = 0
        elif name == "million":
            total = (total + max(group, 1)) * 1_000_000
            group = 0
        else:
            group += value
        prev, i = name, i + 1
        if key == "cero":  # "cero" never combines
            break
    if i == start:
        return None
    return total + group, i - start


def _numbers_to_digits(tokens: Sequence[Token]) -> List[Token]:
    keys = [strip_accents(token.text) for token in tokens]
    out: List[Token] = []
    i = 0
    while i < len(tokens):
        found = _number_at(keys, i)
        if found is None:
            out.append(tokens[i])
            i += 1
            continue
        value, used = found
        out.append(Token(str(value), tokens[i].start))
        i += used
    return out


# ---------------------------------------------------------------------------
# WER
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WerResult:
    reference_words: int
    hypothesis_words: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> Optional[float]:
        return None if self.reference_words == 0 else self.errors / self.reference_words


def word_error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> WerResult:
    """
    Minimum edit alignment between two word sequences (Levenshtein over words).
    Ties prefer a match/substitution, then a deletion, then an insertion.
    O(len(reference) x len(hypothesis)) time, O(len(hypothesis)) memory.
    """
    vocabulary: dict = {}
    ref = [vocabulary.setdefault(word, len(vocabulary)) for word in reference]
    hyp = [vocabulary.setdefault(word, len(vocabulary)) for word in hypothesis]
    n, m = len(ref), len(hyp)

    # Row i holds, for each j, (cost, subs, dels, ins) of aligning ref[:i] with hyp[:j].
    cost = list(range(m + 1))
    subs = [0] * (m + 1)
    dels = [0] * (m + 1)
    ins = list(range(m + 1))
    for i in range(1, n + 1):
        word = ref[i - 1]
        new_cost = [i] + [0] * m
        new_subs = [0] * (m + 1)
        new_dels = [i] + [0] * m
        new_ins = [0] * (m + 1)
        for j in range(1, m + 1):
            mismatch = 0 if word == hyp[j - 1] else 1
            diagonal = cost[j - 1] + mismatch
            deletion = cost[j] + 1
            insertion = new_cost[j - 1] + 1
            if diagonal <= deletion and diagonal <= insertion:
                new_cost[j] = diagonal
                new_subs[j] = subs[j - 1] + mismatch
                new_dels[j] = dels[j - 1]
                new_ins[j] = ins[j - 1]
            elif deletion <= insertion:
                new_cost[j] = deletion
                new_subs[j] = subs[j]
                new_dels[j] = dels[j] + 1
                new_ins[j] = ins[j]
            else:
                new_cost[j] = insertion
                new_subs[j] = new_subs[j - 1]
                new_dels[j] = new_dels[j - 1]
                new_ins[j] = new_ins[j - 1] + 1
        cost, subs, dels, ins = new_cost, new_subs, new_dels, new_ins
    return WerResult(n, m, subs[m], dels[m], ins[m])


# ---------------------------------------------------------------------------
# Timestamp anchors (runbook §3.2)
# ---------------------------------------------------------------------------

@dataclass
class AnchorReport:
    total: int = 0
    errors_ms: List[float] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return len(self.errors_ms)

    @property
    def unmatched(self) -> int:
        return self.total - self.matched

    @property
    def median_ms(self) -> Optional[float]:
        return statistics.median(self.errors_ms) if self.errors_ms else None

    @property
    def p90_ms(self) -> Optional[float]:
        return percentile(self.errors_ms, 90)

    def merge(self, other: "AnchorReport") -> "AnchorReport":
        return AnchorReport(self.total + other.total, self.errors_ms + other.errors_ms)


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile (the value at rank ceil(pct/100 * n))."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def anchor_errors(
    anchors: Sequence[Tuple[float, str]],
    hypothesis: Sequence[Token],
    keep_accents: bool,
    window_s: float = 5.0,
    max_words: int = 3,
) -> AnchorReport:
    """
    Error (ms) between each anchor time and the start of the hypothesis words
    that open the same sentence. An anchor matches the first ``max_words``
    normalized words of its text (down to 2, or 1 for a one-word anchor) at a
    hypothesis start within ``window_s`` seconds; the closest such start wins.
    Anchors with no match are counted as unmatched, not as errors.
    """
    timed = sorted((t for t in hypothesis if t.start is not None), key=lambda t: t.start)
    words = [t.text for t in timed]
    starts = [float(t.start) for t in timed]
    report = AnchorReport()

    for anchor_time, anchor_text in anchors:
        report.total += 1
        wanted = normalize_text(anchor_text, keep_accents)[:max_words]
        if not wanted:
            continue
        low = bisect.bisect_left(starts, anchor_time - window_s)
        high = bisect.bisect_right(starts, anchor_time + window_s)
        best: Optional[float] = None
        for length in range(len(wanted), min(2, len(wanted)) - 1, -1):
            sequence = wanted[:length]
            candidates = [
                starts[i] for i in range(low, high)
                if words[i:i + length] == sequence
            ]
            if candidates:
                best = min(candidates, key=lambda s: abs(s - anchor_time))
                break
        if best is not None:
            report.errors_ms.append(abs(best - anchor_time) * 1000.0)
    return report
