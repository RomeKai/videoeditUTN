"""
Persists AI usage as ``AIUsageRecord`` rows (AICORE-8).

Metrics are observability, not the critical path:

- Rows are INSERT-ONLY (``create`` in its own SHORT transaction): one row per
  actual execution. A redelivery or lost-ownership re-run shares the same
  ``attempt`` but is a distinct billed execution, so it must never overwrite an
  earlier row. A worker killed mid-call writes nothing (invisible by design).
- A failed write is logged (``metrics.write_failed``, exception CLASS NAME only)
  and swallowed: the pipeline stage must never fail because a metric did not
  persist.
- Only numbers, model names and the CLASS name of an error reach the table or
  the logs. Never ``str(exc)``, prompts, titles or transcripts.

Call these helpers AFTER the stage artifact is persisted and outside any
``transaction.atomic()`` block that also performs I/O.
"""

import logging
import math
import re
from decimal import Decimal
from typing import List, Optional, Sequence

from django.db import transaction

from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.contracts import ProviderUsage
from apps.videos.services.ai.errors import is_billing_uncertain
from apps.videos.services.ai.pricing import PRICING_VERSION

logger = logging.getLogger(__name__)

_MAX_ATTEMPT = 32767  # PositiveSmallIntegerField
_COST_QUANTUM = Decimal("1E-9")


def failure_usage(
    exc: BaseException,
    provider: str,
    model: str,
    latency_seconds: float,
    role: str = "primary",
) -> ProviderUsage:
    """
    Usage for a failed call that reported no usage of its own: zero tokens, the
    measured latency and the exception CLASS name. The cost is 0 (providers do
    not charge a call that never answered) unless a timeout/dropped connection
    leaves the billing unknown, in which case it is None, never a false zero.
    """
    return ProviderUsage(
        provider=provider,
        model=model,
        role=role,
        duration_seconds=max(0.0, round(latency_seconds, 3)),
        estimated_cost_usd=None if is_billing_uncertain(exc) else 0.0,
        pricing_version=PRICING_VERSION,
        error_code=type(exc).__name__,
    )


def usages_for_failure(
    exc: BaseException,
    provider: str,
    model: str,
    latency_seconds: float,
) -> List[ProviderUsage]:
    """
    Rows to write when a provider call raised: the attempts the error carries
    (billed ones included), or one zero-token failure row built from the
    measured latency. Every returned usage is marked as failed.
    """
    carried = list(getattr(exc, "usage_attempts", None) or [])
    if not carried:
        return [failure_usage(exc, provider, model, latency_seconds)]
    return [
        usage if usage.error_code else usage.model_copy(update={"error_code": type(exc).__name__})
        for usage in carried
    ]


def usages_for_success(result) -> List[ProviderUsage]:
    """Every attempt of an AIExecutionResult (just ``usage`` when none were tracked)."""
    return list(result.attempts) or [result.usage]


def _cost_to_decimal(cost: Optional[float]) -> Optional[Decimal]:
    if cost is None or not math.isfinite(cost):
        return None
    return Decimal(repr(float(cost))).quantize(_COST_QUANTUM)


_SAFE_MODEL_CHARS = re.compile(r"[^A-Za-z0-9._:/-]")


def _safe_model_name(name: Optional[str]) -> Optional[str]:
    """
    Provider-reported model name restricted to [A-Za-z0-9._:/-] (max 64). It comes
    from a provider response, so anything else is dropped; nothing left => NULL.
    """
    if not name:
        return None
    return _SAFE_MODEL_CHARS.sub("", name)[:64] or None


def _record_one(project_id, stage: str, pipeline_version: str, attempt: int, usage: ProviderUsage) -> None:
    defaults = {
        "pipeline_version": pipeline_version,
        "provider": usage.provider[:64],
        "model": usage.model[:64],
        "resolved_model": _safe_model_name(usage.resolved_model),
        "success": usage.error_code is None,
        "error_code": usage.error_code[:64] if usage.error_code else None,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "audio_seconds": usage.audio_seconds,
        "latency_seconds": usage.duration_seconds,
        "cached": usage.cached,
        "estimated_cost_usd": _cost_to_decimal(usage.estimated_cost_usd),
        "pricing_version": usage.pricing_version,
    }
    with transaction.atomic():
        AIUsageRecord.objects.create(
            project_id=project_id,
            stage=stage,
            attempt=attempt,
            role=usage.role,
            **defaults,
        )


def record_usages(
    project_id,
    stage: str,
    pipeline_version: str,
    usages: Sequence[ProviderUsage],
    attempt: Optional[int] = None,
) -> int:
    """
    Writes one row per usage. ``attempt`` defaults to the project's current
    ``pipeline_attempts``. Never raises: returns how many rows were written.
    """
    written = 0
    try:
        if attempt is None:
            attempt = VideoProject.objects.values_list("pipeline_attempts", flat=True).get(pk=project_id)
        attempt = min(int(attempt), _MAX_ATTEMPT)
    except Exception as exc:
        _log_write_failed(project_id, stage, exc)
        return written

    for usage in usages:
        try:
            _record_one(project_id, stage, pipeline_version, attempt, usage)
            written += 1
        except Exception as exc:
            _log_write_failed(project_id, stage, exc)
    return written


def _log_write_failed(project_id, stage: str, exc: BaseException) -> None:
    # Class name only: a DB or provider message could echo user data.
    logger.warning(
        "metrics.write_failed project_id=%s stage=%s error=%s",
        project_id, stage, type(exc).__name__,
        extra={"project_id": str(project_id), "stage": stage},
    )
