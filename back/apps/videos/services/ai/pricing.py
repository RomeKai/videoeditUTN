"""
Versioned AI price table (AICORE-8).

Prices live in code (not in the environment) so every tariff change goes
through a reviewable PR, and ``PRICING_VERSION`` is stored with each usage
record. Costs are computed with ``Decimal``; callers convert at the boundary.

Rules:
- Unknown model => ``None`` plus a ``pricing.unknown_model`` log line (model
  name only). NEVER 0: a false zero would silently pass the cost criterion.
- Aliases such as ``gemini-flash-latest`` are intentionally NOT priced: the
  official docs say they are hot-swapped to the newest release of a variant
  without naming the target model, so no stable price exists.
- Logs never contain prompts, transcripts or any user text.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from apps.videos.services.ai.contracts import ProviderUsage

logger = logging.getLogger(__name__)

# Date the prices below were last checked against the official pages.
PRICING_VERSION = "2026-10-08"

_MILLION = Decimal(1_000_000)
_SECONDS_PER_HOUR = Decimal(3600)

# LiteLLM-style provider prefixes stripped before the table lookup, so
# "gemini/gemini-2.5-flash" and "gemini-2.5-flash" resolve to the same key.
_PROVIDER_PREFIXES = ("gemini/", "openai/", "groq/")


@dataclass(frozen=True)
class LLMPrice:
    """USD per million tokens."""

    input_per_mtok: Decimal
    cached_input_per_mtok: Decimal
    output_per_mtok: Decimal


@dataclass(frozen=True)
class ASRPrice:
    """USD per hour of audio, with the provider's minimum billed length per request."""

    per_audio_hour: Decimal
    min_billable_seconds: Decimal = Decimal(0)


# Gemini: https://ai.google.dev/gemini-api/docs/pricing (retrieved 2026-10-08),
#   standard paid tier, text input. Output includes thinking tokens.
# OpenAI: https://developers.openai.com/api/docs/pricing (retrieved 2026-10-08),
#   Standard tier. (https://openai.com/api/pricing/ answered 403 to the fetcher.)
LLM_PRICES: Dict[str, LLMPrice] = {
    "gemini-2.5-flash": LLMPrice(Decimal("0.30"), Decimal("0.03"), Decimal("2.50")),
    "gemini-2.5-flash-lite": LLMPrice(Decimal("0.10"), Decimal("0.01"), Decimal("0.40")),
    "gpt-4o-mini": LLMPrice(Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
}

# Groq model pages (retrieved 2026-10-08):
#   https://console.groq.com/docs/model/whisper-large-v3-turbo -> $0.04/hour
#   https://console.groq.com/docs/model/whisper-large-v3       -> $0.111/hour
#   https://console.groq.com/docs/speech-to-text -> "Minimum Billed Length: 10 seconds"
#   per request. https://groq.com/pricing itself exposes no prices to the fetcher.
ASR_PRICES: Dict[str, ASRPrice] = {
    "whisper-large-v3-turbo": ASRPrice(Decimal("0.04"), Decimal(10)),
    "whisper-large-v3": ASRPrice(Decimal("0.111"), Decimal(10)),
}


def _normalize_model(model: str) -> str:
    key = (model or "").strip().lower()
    for prefix in _PROVIDER_PREFIXES:
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


def _log_unknown(model: str) -> None:
    logger.warning("pricing.unknown_model model=%s", model)


def estimate_llm_cost(model: str, usage: ProviderUsage) -> Optional[Decimal]:
    """
    Cost in USD for one LLM call, or ``None`` when the model has no known price.

    ``usage.prompt_tokens`` is treated as the total prompt (cached tokens
    included, as LiteLLM/OpenAI/Gemini report it); the cached share is billed
    at the cached rate and the rest at the regular input rate.
    """
    price = LLM_PRICES.get(_normalize_model(model))
    if price is None:
        _log_unknown(model)
        return None

    cached = min(usage.cache_read_tokens, usage.prompt_tokens)
    fresh = usage.prompt_tokens - cached
    return (
        Decimal(fresh) * price.input_per_mtok
        + Decimal(cached) * price.cached_input_per_mtok
        + Decimal(usage.completion_tokens) * price.output_per_mtok
    ) / _MILLION


def estimate_asr_cost(model: str, seconds: float) -> Optional[Decimal]:
    """
    Cost in USD for ONE transcription request of ``seconds`` of audio, or
    ``None`` when the model has no known price. The provider's minimum
    billable length is applied per request, so multi-chunk callers must sum
    this function over chunks.
    """
    if seconds < 0:
        raise ValueError("seconds must be >= 0")
    price = ASR_PRICES.get(_normalize_model(model))
    if price is None:
        _log_unknown(model)
        return None
    if seconds == 0:
        return Decimal(0)

    billable = max(Decimal(str(seconds)), price.min_billable_seconds)
    return billable / _SECONDS_PER_HOUR * price.per_audio_hour
