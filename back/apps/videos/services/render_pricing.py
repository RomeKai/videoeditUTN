"""
What a render costs. One place for the approval of a paper edit and for the
auto-render of the selected clips ("Renderizar sin revisar"), so both charge
the same for the same seconds.
"""

import math


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
