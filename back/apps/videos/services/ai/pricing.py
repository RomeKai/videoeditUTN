"""
Versioned AI price table (AICORE-8).

Prices live in code (not in the environment) so every tariff change goes
through a reviewable PR, and ``PRICING_VERSION`` is stored with each usage
record. Costs are computed with ``Decimal``; callers convert at the boundary.

Rules:
- Models are keyed by ``(provider, model)`` so ``groq/openai/gpt-oss-120b`` and
  ``openai/gpt-4o-mini`` can never collide. LiteLLM strings are split on the
  first ``/`` when it names a known provider; a bare LLM name defaults to
  ``openai`` (same convention as ``LiteLLMSelectionProvider._extract_usage``)
  and a bare ASR name defaults to ``groq``.
- Each model maps to price PERIODS (``valid_from`` / ``valid_until``, both
  inclusive, ``None`` = open). The lookup date is ``on`` or today (UTC) from an
  injectable clock.
- Unknown model => ``None`` plus a ``pricing.unknown_model`` log line. Known
  model without a period covering the date => ``None`` plus
  ``pricing.no_price_for_date``. NEVER 0: a false zero would silently pass the
  cost criterion. Each (reason, model) is logged at most once per process.
- Aliases such as ``gemini-flash-latest`` are intentionally NOT priced: the
  official docs say they are hot-swapped to the newest release of a variant
  without naming the target model, so no stable price exists. Groq Llama
  models ("Contact Sales") and preview models are unpriced for the same reason.
- When the cached-input price is unknown (``None``), cached tokens are billed
  at the regular input rate (conservative: never under-estimates).
- Logs never contain prompts, transcripts or any user text.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, Optional, Set, Tuple

from apps.videos.services.ai.contracts import ProviderUsage

logger = logging.getLogger(__name__)

# Date the prices below were last checked against the official pages.
PRICING_VERSION = "2026-10-09"

_MILLION = Decimal(1_000_000)
_SECONDS_PER_HOUR = Decimal(3600)

# Providers recognised as the first segment of a LiteLLM model string.
_KNOWN_PROVIDERS = ("gemini", "openai", "groq")
_DEFAULT_LLM_PROVIDER = "openai"
_DEFAULT_ASR_PROVIDER = "groq"

ModelKey = Tuple[str, str]


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# Injectable clock: tests patch this attribute instead of freezing time globally.
_clock: Callable[[], date] = _utc_today


@dataclass(frozen=True)
class LLMPrice:
    """USD per million tokens, valid for ``valid_from``..``valid_until`` (inclusive)."""

    input_per_mtok: Decimal
    cached_input_per_mtok: Optional[Decimal]
    output_per_mtok: Decimal
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None


@dataclass(frozen=True)
class ASRPrice:
    """USD per hour of audio, with the provider's minimum billed length per request."""

    per_audio_hour: Decimal
    min_billable_seconds: Decimal = Decimal(0)
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None


def _d(value: str) -> Decimal:
    return Decimal(value)


# Gemini: https://ai.google.dev/gemini-api/docs/pricing
#   standard paid tier, text input. Output includes thinking tokens.
#   source: Gemini pricing page, retrieved 2026-10-08 (2.5 family) and
#   2026-10-09 (3.x family, per research pass; not re-fetched).
# OpenAI: https://developers.openai.com/api/docs/pricing (retrieved 2026-10-08),
#   Standard tier. (https://openai.com/api/pricing/ answered 403 to the fetcher.)
# Groq: https://console.groq.com/docs/models, per-model pages,
#   source: Groq models pages, retrieved 2026-10-09. No cached-input price is
#   published for gpt-oss, hence ``None``.
#
# New 3.x entries start at the retrieval date: earlier dates have no verified
# price, so they resolve to None instead of an unverified number.
_RETRIEVED = date(2026, 10, 9)
_INTRO_END = date(2026, 12, 31)
_POST_INTRO_START = date(2027, 1, 1)


def _gemini_intro_schedule() -> Tuple[LLMPrice, LLMPrice]:
    """3.6/3.7/3.8 Flash: introductory price until 2026-12-31 inclusive, then list price."""
    return (
        LLMPrice(_d("0.75"), _d("0.075"), _d("3.75"), _RETRIEVED, _INTRO_END),
        LLMPrice(_d("1.50"), _d("0.15"), _d("7.50"), _POST_INTRO_START, None),
    )


LLM_PRICES: Dict[ModelKey, Tuple[LLMPrice, ...]] = {
    ("gemini", "gemini-2.5-flash"): (LLMPrice(_d("0.30"), _d("0.03"), _d("2.50")),),
    ("gemini", "gemini-2.5-flash-lite"): (LLMPrice(_d("0.10"), _d("0.01"), _d("0.40")),),
    ("gemini", "gemini-3.8-flash"): _gemini_intro_schedule(),
    ("gemini", "gemini-3.7-flash"): _gemini_intro_schedule(),
    ("gemini", "gemini-3.6-flash"): _gemini_intro_schedule(),
    ("gemini", "gemini-3.5-flash"): (
        LLMPrice(_d("1.50"), _d("0.15"), _d("9.00"), _RETRIEVED),
    ),
    ("gemini", "gemini-3.5-flash-lite"): (
        LLMPrice(_d("0.30"), _d("0.03"), _d("2.50"), _RETRIEVED),
    ),
    ("gemini", "gemini-3.1-flash-lite"): (
        LLMPrice(_d("0.25"), _d("0.025"), _d("1.50"), _RETRIEVED),
    ),
    ("openai", "gpt-4o-mini"): (LLMPrice(_d("0.15"), _d("0.075"), _d("0.60")),),
    ("groq", "openai/gpt-oss-120b"): (
        LLMPrice(_d("0.15"), None, _d("0.60"), _RETRIEVED),
    ),
    ("groq", "openai/gpt-oss-20b"): (
        LLMPrice(_d("0.075"), None, _d("0.30"), _RETRIEVED),
    ),
}

# Groq model pages (retrieved 2026-10-08):
#   https://console.groq.com/docs/model/whisper-large-v3-turbo -> $0.04/hour
#   https://console.groq.com/docs/model/whisper-large-v3       -> $0.111/hour
#   https://console.groq.com/docs/speech-to-text -> "Minimum Billed Length: 10 seconds"
#   per request. https://groq.com/pricing itself exposes no prices to the fetcher.
ASR_PRICES: Dict[ModelKey, Tuple[ASRPrice, ...]] = {
    ("groq", "whisper-large-v3-turbo"): (ASRPrice(_d("0.04"), Decimal(10)),),
    ("groq", "whisper-large-v3"): (ASRPrice(_d("0.111"), Decimal(10)),),
}


def _model_key(model: str, default_provider: str) -> ModelKey:
    """``"groq/openai/gpt-oss-120b"`` -> ``("groq", "openai/gpt-oss-120b")``."""
    name = (model or "").strip().lower()
    head, sep, rest = name.partition("/")
    if sep and head in _KNOWN_PROVIDERS and rest:
        return head, rest
    return default_provider, name


# (reason, "provider/model") pairs already logged by this process.
_logged: Set[Tuple[str, str]] = set()


def reset_log_dedupe() -> None:
    """Forget which warnings were emitted (tests only)."""
    _logged.clear()


def _log_once(reason: str, key: ModelKey, on: Optional[date] = None) -> None:
    label = f"{key[0]}/{key[1]}"
    marker = (reason, label)
    if marker in _logged:
        return
    _logged.add(marker)
    if on is None:
        logger.warning("pricing.%s model=%s", reason, label)
    else:
        logger.warning("pricing.%s model=%s date=%s", reason, label, on.isoformat())


def _period_for(periods, on: date):
    for period in periods:
        if period.valid_from is not None and on < period.valid_from:
            continue
        if period.valid_until is not None and on > period.valid_until:
            continue
        return period
    return None


def _lookup(table, key: ModelKey, on: Optional[date]):
    periods = table.get(key)
    if periods is None:
        _log_once("unknown_model", key)
        return None
    when = on if on is not None else _clock()
    period = _period_for(periods, when)
    if period is None:
        _log_once("no_price_for_date", key, when)
    return period


def estimate_llm_cost(
    model: str, usage: ProviderUsage, on: Optional[date] = None
) -> Optional[Decimal]:
    """
    Cost in USD for one LLM call, or ``None`` when the model has no known price
    (unknown model, or no price period covering ``on``; default today, UTC).

    ``usage.prompt_tokens`` is treated as the total prompt (cached tokens
    included, as LiteLLM/OpenAI/Gemini report it); the cached share is billed
    at the cached rate (or the regular input rate when the cached price is
    unknown) and the rest at the regular input rate.
    """
    price = _lookup(LLM_PRICES, _model_key(model, _DEFAULT_LLM_PROVIDER), on)
    if price is None:
        return None

    cached_rate = (
        price.cached_input_per_mtok
        if price.cached_input_per_mtok is not None
        else price.input_per_mtok
    )
    cached = min(usage.cache_read_tokens, usage.prompt_tokens)
    fresh = usage.prompt_tokens - cached
    return (
        Decimal(fresh) * price.input_per_mtok
        + Decimal(cached) * cached_rate
        + Decimal(usage.completion_tokens) * price.output_per_mtok
    ) / _MILLION


def estimate_asr_cost(
    model: str, seconds: float, on: Optional[date] = None
) -> Optional[Decimal]:
    """
    Cost in USD for ONE transcription request of ``seconds`` of audio, or
    ``None`` when the model has no known price. The provider's minimum
    billable length is applied per request, so multi-chunk callers must sum
    this function over chunks.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("seconds must be a finite number >= 0")
    price = _lookup(ASR_PRICES, _model_key(model, _DEFAULT_ASR_PROVIDER), on)
    if price is None:
        return None
    if seconds == 0:
        return Decimal(0)

    billable = max(Decimal(str(seconds)), price.min_billable_seconds)
    return billable / _SECONDS_PER_HOUR * price.per_audio_hour
