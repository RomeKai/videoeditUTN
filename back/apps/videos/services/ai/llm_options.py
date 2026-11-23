"""
Per-call LiteLLM options shared by the clip-selection paths (V2 provider and
the legacy selector), following the per-environment model profiles of ADR-012.

Reasoning control (AICORE-14, #66): ``AI_LLM_REASONING_EFFORT`` caps the
model's hidden reasoning, which Gemini bills as output tokens. LiteLLM maps
the value to Gemini 3's ``thinkingLevel`` (an older Gemini gets a thinking
budget). Unset means the provider default, exactly as before. The value is
only sent to models LiteLLM knows can reason, so a non-reasoning fallback
model never receives a parameter it would reject.
"""

import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# Values every reasoning model accepts through LiteLLM. "minimal" falls back to
# the lowest level a model supports (gemini-3.8-flash: "low").
REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def reasoning_effort_setting() -> Optional[str]:
    """
    The configured reasoning effort, or None when unset. A value outside
    ``REASONING_EFFORTS`` is a deployment error, raised before any paid call.
    """
    raw = str(getattr(settings, "AI_LLM_REASONING_EFFORT", "") or "").strip().lower()
    if not raw:
        return None
    if raw not in REASONING_EFFORTS:
        raise ImproperlyConfigured(
            f"Invalid AI_LLM_REASONING_EFFORT={raw!r}. "
            f"Expected one of: {', '.join(REASONING_EFFORTS)} (or empty for the provider default)."
        )
    return raw


def reasoning_kwargs(model: str, effort: Optional[str]) -> Dict[str, Any]:
    """``{"reasoning_effort": effort}`` for a model that can reason, else ``{}``."""
    if not effort:
        return {}
    import litellm

    try:
        supported = bool(litellm.supports_reasoning(model=model))
    except Exception:  # unknown model / provider: never send an unknown parameter
        supported = False
    if not supported:
        logger.info("llm.reasoning_effort_skipped model=%s reason=unsupported_model", model)
        return {}
    return {"reasoning_effort": effort}


def apply_litellm_defaults() -> None:
    """
    Process-wide LiteLLM defaults for the AI core. Thought summaries are not
    requested from Gemini: the pipeline never reads them, and the thinking
    itself is still capped (and billed) according to the reasoning effort.
    """
    import litellm

    litellm.gemini_include_thoughts_default = False
