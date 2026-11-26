"""
What a render costs. One place for the approval of a paper edit and for the
auto-render of the selected clips ("Renderizar sin revisar"), so both charge
the same for the same seconds. And what a new project costs
(``price_new_project``), for the creation and the estimate.
"""

import math
from dataclasses import dataclass
from decimal import Decimal


def pricing_resolution(project):
    """
    The resolution tier PricingEngine checks against the plan. It used to get
    the aspect ratio ('9:16'), which it read as 720p. The tier is the
    project's render_resolution (chosen at creation or set by Prompt-to-Edit),
    else 720p, which is also what the render outputs without one.
    """
    from typing import get_args

    from apps.videos.services.ai.contracts import RenderResolutionValue

    requested = (project.metadata or {}).get('render_resolution')
    return requested if requested in get_args(RenderResolutionValue) else '720p'


def render_cost(project, seconds):
    """
    Tokens to render ``seconds`` of video for ``project``: only the render. The
    paid add-ons (SEO, thumbnail) were charged with the project at creation
    (ADR-013). Raises PlanLimitExceededError / FeatureNotAllowedError like
    PricingEngine.
    """
    from apps.payments.services.pricing_engine import PricingEngine

    return PricingEngine.calculate_render_cost(
        # Rounded first: a float sum of exact seconds (84.00000000000001) must
        # not bill one more second.
        duration_seconds=int(math.ceil(round(float(seconds), 3))),
        resolution=pricing_resolution(project),
        features_requested={},
        plan=project.workspace.subscription_plan,
    )


LINK_RESOLUTION_ERROR = "Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo."


class NewProjectRefused(Exception):
    """A configuration the creation answers with a 400: ``body`` is that answer."""

    def __init__(self, body):
        super().__init__('refused')
        self.body = body


@dataclass(frozen=True)
class ProjectPrice:
    """What creating a project costs, split as the estimate shows it."""

    base: Decimal       # the video: whole seconds × rate × resolution, or the link's flat price
    add_ons: Decimal    # list price of the add-ons asked for (SEO, thumbnail)
    total: Decimal      # what is reserved: with the plan's discount, rounded


def price_new_project(*, plan, source, resolution='720p', duration_seconds=None, use_seo=False, use_ai_thumbnail=False):
    """
    The price of a new project: what VideoProjectViewSet.create reserves and
    what POST pricing/estimate/ shows, from one place.

    - ``'file'``: its whole seconds through PricingEngine.calculate_render_cost,
      with the plan's limits and discount. Longer than
      AI_UPLOAD_MAX_DURATION_SECONDS is the 400 of the upload validation.
    - ``'link'``: its duration is unknown, so it is LINK_FLAT_COST, only in
      720p (400 otherwise), plus the SEO add-on with the discount (ADR-013).

    Raises NewProjectRefused (a 400) or the engine's PlanLimitExceededError /
    FeatureNotAllowedError (a 403, their ``code`` says which limit).
    """
    from django.conf import settings

    from apps.payments.services.pricing_engine import PricingEngine

    resolution = resolution or '720p'
    if source == 'link':
        if resolution != '720p':
            # The flat link price does not know the duration, so it cannot
            # price a higher tier; an auto-rendered link would get it free.
            raise NewProjectRefused({"error": LINK_RESOLUTION_ERROR})
        seo = bool(use_seo)
        # Checks the SEO against the plan; without it the engine still answers its floor.
        add_on = PricingEngine.calculate_render_cost(
            duration_seconds=0, resolution='720p', features_requested={'seo_optimization': seo}, plan=plan,
        )
        base = PricingEngine.LINK_FLAT_COST
        add_ons = PricingEngine.FEATURE_COSTS['seo_optimization'] if seo else Decimal('0')
        return ProjectPrice(base=base, add_ons=add_ons, total=base + (add_on if seo else Decimal('0')))

    from .media_validation import InvalidUploadError, _format_duration

    ceiling = getattr(settings, 'AI_UPLOAD_MAX_DURATION_SECONDS', 10800)
    if duration_seconds > ceiling:
        raise NewProjectRefused({'source_file': [InvalidUploadError('too_long', limit=_format_duration(ceiling)).message]})
    seconds = int(duration_seconds)
    features = {'seo_optimization': bool(use_seo), 'ai_thumbnail': bool(use_ai_thumbnail)}
    total = PricingEngine.calculate_render_cost(
        duration_seconds=seconds, resolution=resolution, features_requested=features, plan=plan,
    )
    base = (
        Decimal(seconds) * PricingEngine.BASE_COST_PER_SECOND_720P
        * PricingEngine.RESOLUTION_MULTIPLIERS.get(resolution, Decimal('1.0'))
    )
    add_ons = sum((PricingEngine.FEATURE_COSTS[name] for name, asked in features.items() if asked), Decimal('0'))
    return ProjectPrice(base=base, add_ons=add_ons, total=total)
