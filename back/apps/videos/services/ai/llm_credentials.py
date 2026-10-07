"""
Per-model credential resolution for LiteLLM calls (AICORE-6 / ADR-001 D3).

LiteLLM routes by the model prefix (``gemini/``, ``openai/``, ...). Passing the
key per call keeps the fallback chain cross-provider without mutating the
process-global ``litellm.api_key``, which would otherwise leak the Gemini key
into OpenAI requests (and vice versa) inside the same worker.
"""

from typing import Dict, Optional

from django.conf import settings

# Model prefix -> Django setting holding the provider key.
_PREFIX_TO_SETTING: Dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
}

# LiteLLM treats un-prefixed model names (e.g. "gpt-4o-mini") as OpenAI.
_DEFAULT_PROVIDER = "openai"


def provider_for_model(model: str) -> str:
    """Returns the provider prefix LiteLLM will route ``model`` to."""
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    return _DEFAULT_PROVIDER


def resolve_api_key(model: str) -> Optional[str]:
    """
    Returns the API key for the provider of ``model`` or ``None`` when the
    provider is unknown or its key is not configured.
    """
    setting_name = _PREFIX_TO_SETTING.get(provider_for_model(model))
    if not setting_name:
        return None
    value = getattr(settings, setting_name, None)
    return value or None
