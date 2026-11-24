"""
Prompt-to-Edit, second half (PTE-01b, #73): the config parsed from the user's
prompt is checked against the workspace plan and applied to the project at the
start of ingestion. Selection and render then run unchanged.

Decisions:

- **When it is parsed:** as the first step of the ingestion task, never inside
  the upload request, so creating a project does not depend on the LLM
  provider being up. The outcome is stored in ``metadata['pte']`` (visible in
  the project detail): ``applied`` (what changed), ``ignored`` (field and
  reason) and ``status``.
- **Plan:** the plan-gated requests (resolution and paid add-ons) are checked
  with ``PricingEngine`` against the workspace plan. What the plan forbids is
  ignored with reason ``plan``; it never fails the request.
- **Price:** the tokens were reserved when the project was created, so a
  request the plan allows but that would raise the price is ignored with
  reason ``cost``: a prompt never changes what the user pays.
- **Explicit wins:** a setting the user sent explicitly in the request is
  kept; the parsed value is ignored with reason ``explicit``.
- **Parser failure:** the project continues with its defaults; ``status`` is
  ``failed`` with a static ``error_code``. Usage rows are recorded either way.

Nothing the user or the model wrote is logged.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from django.db import transaction

from apps.videos.services.ai.contracts import PLAN_GATED_FIELDS, ProjectConfig

logger = logging.getLogger(__name__)

# ProjectConfig fields stored in VideoProject.metadata instead of a column.
METADATA_FIELDS = frozenset({
    "max_clips", "selection_criteria", "render_resolution", "use_seo", "use_ai_thumbnail",
})
# Base the creation price is computed with (the view passes no paid add-ons and
# no resolution tier PricingEngine knows, i.e. the 720p multiplier).
_BASE_RESOLUTION = "720p"
# Price comparisons only need relative costs; a known duration is not required.
_PRICE_PROBE_SECONDS = 60


@dataclass
class ValidationOutcome:
    applied: Dict[str, Any] = field(default_factory=dict)
    ignored: List[Dict[str, str]] = field(default_factory=list)


class ConfigValidator:
    """Cuts a parsed ProjectConfig down to what the plan, the price and the request allow."""

    @classmethod
    def validate(
        cls,
        config: ProjectConfig,
        plan: Any,
        explicit_fields: Iterable[str] = (),
    ) -> ValidationOutcome:
        explicit = set(explicit_fields or ())
        outcome = ValidationOutcome()
        for name, value in config.requested().items():
            if name in explicit:
                outcome.ignored.append({"field": name, "reason": "explicit"})
                continue
            if name in PLAN_GATED_FIELDS:
                reason = cls._gated_reason(name, value, plan)
                if reason:
                    outcome.ignored.append({"field": name, "reason": reason})
                    continue
            outcome.applied[name] = value
        return outcome

    @staticmethod
    def _request(name: str, value: Any):
        resolution = value if name == "render_resolution" else _BASE_RESOLUTION
        features = {
            "seo_optimization": bool(value) if name == "use_seo" else False,
            "ai_thumbnail": bool(value) if name == "use_ai_thumbnail" else False,
        }
        return resolution, features

    @classmethod
    def _gated_reason(cls, name: str, value: Any, plan: Any) -> Optional[str]:
        from apps.payments.services.pricing_engine import (
            FeatureNotAllowedError,
            PlanLimitExceededError,
            PricingEngine,
        )

        resolution, features = cls._request(name, value)
        try:
            # Duration 0: the plan's duration limit was already enforced at creation.
            PricingEngine.calculate_render_cost(
                duration_seconds=0, resolution=resolution, features_requested=features, plan=plan
            )
        except (PlanLimitExceededError, FeatureNotAllowedError):
            return "plan"
        requested = PricingEngine.calculate_render_cost(
            duration_seconds=_PRICE_PROBE_SECONDS, resolution=resolution,
            features_requested=features, plan=None,
        )
        base = PricingEngine.calculate_render_cost(
            duration_seconds=_PRICE_PROBE_SECONDS, resolution=_BASE_RESOLUTION,
            features_requested={}, plan=None,
        )
        if Decimal(requested) > Decimal(base):
            return "cost"
        return None


def explicit_config_fields(raw: Any) -> List[str]:
    """ProjectConfig fields the request sent explicitly (``layout`` is the alias of ``render_layout``)."""
    keys = set(getattr(raw, "keys", lambda: [])())
    if "layout" in keys:
        keys.add("render_layout")
    return sorted(name for name in ProjectConfig.model_fields if name in keys)


class PromptParseRetry(Exception):
    """
    A transient parser error while the parser still has retries of its own:
    the ingestion task re-queues itself (PTE-01b) without spending the
    retries its stages need.
    """


def apply_prompt_to_project(project_id, can_retry: bool = False, capacity=None) -> Optional[Dict[str, Any]]:
    """
    Parses ``metadata['pte_prompt']`` once and applies what is allowed. Returns
    the stored outcome, or None for a project created without a prompt or
    already handled (idempotent: a redelivered task does not parse again).

    - A transient provider error (429, 5xx, timeout) is raised while the
      parser still has retries of its own (``can_retry``): the ingestion
      re-queues itself without spending the stages' retries, and the prompt is
      not lost. A definitive error, or the last parser retry, stores
      ``status=failed`` and goes on with the defaults.
    - ``capacity`` (AICORE-11) reserves the call before it is made and settles
      it with the real usage; it may raise CapacityUnavailable (the task waits).
    """
    from apps.videos.models import VideoProject
    from apps.videos.services.ai.errors import is_retryable_error, is_task_time_limit
    from apps.videos.services.ai.prompt_parser import PromptParser, clean_prompt, record_prompt_parse_usage

    project = VideoProject.objects.select_related("workspace__subscription_plan").get(pk=project_id)
    metadata = project.metadata or {}
    prompt = metadata.get("pte_prompt")
    if prompt is None or metadata.get("pte"):
        return None

    reservation = capacity.reserve(clean_prompt(prompt)) if capacity is not None and clean_prompt(prompt) else None
    try:
        result = PromptParser().parse(prompt, metadata.get("duration"))
    except Exception as exc:
        if capacity is not None:
            capacity.settle(reservation, exc=exc)
        if is_task_time_limit(exc):
            raise
        record_prompt_parse_usage(project_id, exc)
        if can_retry and is_retryable_error(exc):
            logger.warning("prompt_parse.transient project_id=%s error=%s", project_id, type(exc).__name__)
            raise PromptParseRetry() from exc
        outcome = {"status": "failed", "error_code": type(exc).__name__, "applied": {}, "ignored": []}
        _store(project_id, {}, outcome)
        logger.warning("prompt_parse.failed project_id=%s error=%s", project_id, type(exc).__name__)
        return outcome

    if capacity is not None:
        capacity.settle(reservation, result=result)
    record_prompt_parse_usage(project_id, result)
    validation = ConfigValidator.validate(
        result.data, project.workspace.subscription_plan, metadata.get("pte_explicit") or (),
    )
    outcome = {
        "status": "applied" if result.attempts else "empty",
        "applied": validation.applied,
        "ignored": validation.ignored,
    }
    _store(project_id, validation.applied, outcome)
    logger.info(
        "prompt_parse.applied project_id=%s applied=%d ignored=%d",
        project_id, len(validation.applied), len(validation.ignored),
    )
    return outcome


def give_up_prompt(project_id, error_code: str) -> Dict[str, Any]:
    """The prompt could not be parsed in time: the project keeps its settings."""
    outcome = {"status": "failed", "error_code": error_code, "applied": {}, "ignored": []}
    _store(project_id, {}, outcome)
    logger.warning("prompt_parse.failed project_id=%s error=%s", project_id, error_code)
    return outcome


def _store(project_id, applied: Dict[str, Any], outcome: Dict[str, Any]) -> None:
    """Writes the applied settings and the outcome together, under a row lock."""
    from apps.videos.models import VideoProject

    columns = {k: v for k, v in applied.items() if k not in METADATA_FIELDS}
    with transaction.atomic():
        project = VideoProject.objects.select_for_update().get(pk=project_id)
        metadata = dict(project.metadata or {})
        metadata.update({k: v for k, v in applied.items() if k in METADATA_FIELDS})
        metadata["pte"] = outcome
        VideoProject.objects.filter(pk=project_id).update(metadata=metadata, **columns)
