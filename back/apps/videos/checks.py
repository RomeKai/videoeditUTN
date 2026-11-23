"""
Startup check of the AI configuration (ADR-012 D-C3, AICORE-9).

Runs with every management command (``check``, ``migrate``, ``runserver``), so
a bad pilot or production configuration stops the deploy instead of showing
up in the first invoice. Only settings are read: no provider is called.
"""

from django.conf import settings
from django.core.checks import Error, Tags, register


def _configured_models():
    """(setting name, model, price kind) of every model the AI core may call."""
    models = [
        ("AI_DEFAULT_LLM_MODEL", getattr(settings, "AI_DEFAULT_LLM_MODEL", None), "llm"),
        ("AI_FALLBACK_LLM_MODEL", getattr(settings, "AI_FALLBACK_LLM_MODEL", None), "llm"),
    ]
    if getattr(settings, "AI_CORE_V2_ENABLED", False) and _groq_transcription():
        models.append((
            "AI_DEFAULT_TRANSCRIPTION_MODEL",
            getattr(settings, "AI_DEFAULT_TRANSCRIPTION_MODEL", None),
            "asr",
        ))
    return [(name, str(model).strip(), kind) for name, model, kind in models if model and str(model).strip()]


@register(Tags.compatibility)
def check_ai_configuration(app_configs=None, **kwargs):
    from apps.videos.services.ai.pricing import has_current_price

    errors = []
    for name, model, kind in _configured_models():
        if "latest" in model.lower():
            errors.append(Error(
                f"{name}={model!r} is a moving alias.",
                hint="Pin a model version: aliases change target without notice and have no stable price.",
                id="videos.E001",
            ))
        elif not has_current_price(model, kind):
            errors.append(Error(
                f"{name}={model!r} has no current price in apps/videos/services/ai/pricing.py.",
                hint="Add its price (with PRICING_VERSION) by PR, or configure a priced model.",
                id="videos.E002",
            ))

    if getattr(settings, "AI_CORE_V2_ENABLED", False):
        from django.core.exceptions import ImproperlyConfigured

        from apps.videos.services.transcription_engine import TranscriptionEngine

        try:
            TranscriptionEngine.resolve_backend(True)
        except ImproperlyConfigured as exc:
            errors.append(Error(str(exc), id="videos.E003"))

    errors.extend(_reasoning_errors())
    errors.extend(_capacity_errors())
    return errors


def _reasoning_errors():
    from django.core.exceptions import ImproperlyConfigured

    from apps.videos.services.ai.llm_options import reasoning_effort_setting

    try:
        reasoning_effort_setting()
    except ImproperlyConfigured as exc:
        return [Error(str(exc), id="videos.E004")]
    return []


def _capacity_errors():
    """With AI_CAPACITY_ENABLED, every model the pipeline calls needs valid limits."""
    if not getattr(settings, "AI_CAPACITY_ENABLED", False):
        return []
    from django.core.exceptions import ImproperlyConfigured

    from apps.videos.services.ai.capacity import RESOURCES, buckets_for
    from apps.videos.services.ai.llm_credentials import resolve_api_key

    # The models the pipeline reserves (tasks._selection_models): the primary,
    # the fallback when it has a key, and Groq for the V2 transcription.
    primary = getattr(settings, "AI_DEFAULT_LLM_MODEL", None)
    fallback = getattr(settings, "AI_FALLBACK_LLM_MODEL", None)
    models = [primary] if primary else []
    if fallback and fallback != primary and resolve_api_key(fallback):
        models.append(fallback)
    if getattr(settings, "AI_CORE_V2_ENABLED", False) and _groq_transcription():
        models.append(f"groq/{getattr(settings, 'AI_DEFAULT_TRANSCRIPTION_MODEL', '')}")

    errors = []
    for model in models:
        try:
            for resource in RESOURCES:  # validates keys and values of every resource
                buckets_for(model, resource)
        except ImproperlyConfigured as exc:
            errors.append(Error(str(exc), id="videos.E005"))
    return errors


def _groq_transcription():
    from django.core.exceptions import ImproperlyConfigured

    from apps.videos.services.transcription_engine import TranscriptionBackend, TranscriptionEngine

    try:
        return TranscriptionEngine.resolve_backend(True) is TranscriptionBackend.GROQ
    except ImproperlyConfigured:
        return False  # reported as videos.E003
