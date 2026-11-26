"""
Prices for the web app before anything is created (C1 and the dialogs of D):

- ``pricing_values``: the numbers of PricingEngine and the upload limits
  (GET /api/v1/pricing/), so the browser estimates with the same formula.
- ``estimate``: what a configuration would cost the active workspace, with
  its plan and its balance (POST /api/v1/pricing/estimate/). The same rules
  as VideoProjectViewSet.create: a file is priced by its whole seconds, a link
  has a flat price, is always 720p and only pays the SEO add-on on top.
- ``review_eta_seconds``: how long the pipeline takes to leave a video ready
  for the review, from the stage times of the latest projects.
"""
import math
import statistics
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from ..models import Wallet
from .movements import money
from .pricing_engine import FeatureNotAllowedError, PlanLimitExceededError, PricingEngine

LINK_RESOLUTION_ERROR = "Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo."
FILE_DURATION_ERROR = "Mandá la duración del archivo, en segundos."

# "Saldo bajo" (map 1.11): below what an average video costs, 20 minutes at
# 720p with the plan's discount.
LOW_BALANCE_REFERENCE_SECONDS = 1200

# The stages before the review, and how many recent projects the ETA reads.
REVIEW_STAGES = ('audio_extracted', 'transcribed', 'moderated', 'clips_selected')
ETA_SAMPLES = 50


class EstimateRefused(Exception):
    def __init__(self, status, body):
        super().__init__(status)
        self.status = status
        self.body = body


def pricing_values():
    from apps.videos.serializers import UPLOAD_EXTENSIONS, UPLOAD_MAX_SIZE_BYTES
    from apps.videos.services.ai.prompt_parser import prompt_max_chars
    from apps.videos.services.ai.selection_criteria import MAX_SELECTION_CRITERIA_CHARS

    return {
        'base_cost_per_second_720p': money(PricingEngine.BASE_COST_PER_SECOND_720P),
        'resolution_multipliers': {key: money(value) for key, value in PricingEngine.RESOLUTION_MULTIPLIERS.items()},
        'feature_costs': {key: money(value) for key, value in PricingEngine.FEATURE_COSTS.items()},
        'link_flat_cost': money(PricingEngine.LINK_FLAT_COST),
        'min_cost': money(PricingEngine.MIN_COST),
        'token_price_usd': money(Decimal(settings.TOKEN_PRICE_USD)),
        'single_edit_max_duration_seconds': int(getattr(settings, 'AI_SINGLE_EDIT_MAX_DURATION_SECONDS', 600)),
        'upload_max_duration_seconds': int(getattr(settings, 'AI_UPLOAD_MAX_DURATION_SECONDS', 10800)),
        'upload_max_size_bytes': UPLOAD_MAX_SIZE_BYTES,
        'upload_extensions': list(UPLOAD_EXTENSIONS),
        'selection_criteria_max_chars': MAX_SELECTION_CRITERIA_CHARS,
        'prompt_max_chars': prompt_max_chars(),
    }


def low_balance_threshold(plan):
    rate = plan.base_discount_rate if plan is not None else Decimal('0')
    cost = (
        Decimal(LOW_BALANCE_REFERENCE_SECONDS) * PricingEngine.BASE_COST_PER_SECOND_720P
        * PricingEngine.RESOLUTION_MULTIPLIERS['720p'] * (Decimal('1') - rate)
    )
    return cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _plan_refusal(exc, plan):
    """The 403 of a plan limit, with the ``code`` the web app opens D3 with."""
    from apps.videos.services.media_validation import _format_duration

    code = getattr(exc, 'code', None)
    if code == 'duration':
        limit = plan.max_video_duration_seconds
        body = {'error': f"Tu plan {plan.name} permite videos de hasta {_format_duration(limit)}.", 'limit': limit}
    elif code == 'resolution':
        body = {'error': f"Tu plan {plan.name} renderiza hasta {plan.max_resolution}.", 'limit': plan.max_resolution}
    elif code == 'seo':
        body = {'error': f"Tu plan {plan.name} no incluye el SEO."}
    else:
        code, body = 'plan_limit', {'error': f"Tu plan {plan.name} no permite esta configuración."}
    return EstimateRefused(403, {**body, 'code': code})


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def review_eta_seconds(duration_seconds):
    """
    Seconds until a video of ``duration_seconds`` is ready for the review: the
    median, over the latest ETA_SAMPLES projects that got there, of the time
    their stages took per second of video (``metadata['stage_durations']``
    over the seconds priced). Only the stages' own time: not the wait in the
    queue. None without such projects, or for a video of 0 s.
    """
    from apps.videos.models import VideoProject

    if duration_seconds <= 0:
        return None
    rows = (
        VideoProject.objects.filter(metadata__stage_durations__has_key=REVIEW_STAGES[-1])
        .order_by('-created_at')
        .values_list('metadata__stage_durations', 'metadata__priced_duration_s', 'metadata__source_media__duration_s')
        [:ETA_SAMPLES]
    )
    ratios = []
    for stages, priced, measured in rows:
        video = priced if _number(priced) and priced > 0 else measured
        if not isinstance(stages, dict) or not _number(video) or video <= 0:
            continue
        spent = [stages.get(stage) for stage in REVIEW_STAGES]
        if all(_number(seconds) and seconds >= 0 for seconds in spent):
            ratios.append(sum(spent) / video)
    if not ratios:
        return None
    return int(round(statistics.median(ratios) * duration_seconds))


def estimate(workspace, *, source, duration_seconds=None, render_resolution=None, use_seo=False):
    """The contract's ``Estimate`` for ``workspace``, or EstimateRefused (400 or 403)."""
    plan = workspace.subscription_plan
    resolution = render_resolution or '720p'
    features = {'seo_optimization': bool(use_seo)}
    add_ons = PricingEngine.FEATURE_COSTS['seo_optimization'] if use_seo else Decimal('0')
    try:
        if source == 'link':
            if resolution != '720p':
                raise EstimateRefused(400, {'render_resolution': [LINK_RESOLUTION_ERROR]})
            base = PricingEngine.LINK_FLAT_COST
            # Checks the SEO against the plan; without it the engine still answers its floor.
            seo = PricingEngine.calculate_render_cost(
                duration_seconds=0, resolution='720p', features_requested=features, plan=plan,
            )
            total = base + (seo if use_seo else Decimal('0'))
            eta = None
        else:
            if duration_seconds is None:
                raise EstimateRefused(400, {'duration_seconds': [FILE_DURATION_ERROR]})
            seconds = int(duration_seconds)
            total = PricingEngine.calculate_render_cost(
                duration_seconds=seconds, resolution=resolution, features_requested=features, plan=plan,
            )
            base = (
                Decimal(seconds) * PricingEngine.BASE_COST_PER_SECOND_720P
                * PricingEngine.RESOLUTION_MULTIPLIERS[resolution]
            )
            eta = review_eta_seconds(seconds)
    except (PlanLimitExceededError, FeatureNotAllowedError) as exc:
        raise _plan_refusal(exc, plan)

    available = (
        Wallet.objects.filter(workspace_id=workspace.pk).values_list('available_balance', flat=True).first()
        or Decimal('0')
    )
    return {
        'base_cost': money(base),
        'add_ons_cost': money(add_ons),
        'discount_rate': money(plan.base_discount_rate if plan is not None else Decimal('0')),
        'discount_amount': money(max(Decimal('0'), base + add_ons - total)),
        'total': money(total),
        'available_balance': money(available),
        'balance_after': money(available - total),
        'sufficient': available >= total,
        'has_watermark': plan.has_watermark if plan is not None else True,
        'eta_seconds': eta,
    }
