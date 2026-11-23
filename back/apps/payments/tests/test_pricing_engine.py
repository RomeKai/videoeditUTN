"""
CORE-06 · PricingEngine: multiplicadores, add-ons, descuentos, límites de plan y edge cases.
No toca la base de datos: los planes son instancias en memoria.
"""
from decimal import Decimal

import pytest

from apps.payments.services.pricing_engine import (
    FeatureNotAllowedError,
    PlanLimitExceededError,
    PricingEngine,
)

NO_FEATURES = {}


def cost(duration, resolution="720p", features=None, plan=None):
    return PricingEngine.calculate_render_cost(duration, resolution, features or NO_FEATURES, plan)


# --- Fórmula base (sin plan) -------------------------------------------------

@pytest.mark.parametrize(
    "resolution, expected",
    [
        ("720p", Decimal("30.00")),   # 60s * 0.5 * 1.0
        ("1080p", Decimal("45.00")),  # 60s * 0.5 * 1.5
        ("4K", Decimal("90.00")),     # 60s * 0.5 * 3.0
    ],
)
def test_resolution_multipliers(resolution, expected):
    assert cost(60, resolution) == expected


@pytest.mark.xfail(
    strict=True,
    reason="Bug: una resolución desconocida cae en silencio al multiplicador base "
           "(pricing_engine.py:66 y :80). Así la view le pasa aspect_ratio ('9:16') "
           "y nunca se aplican el multiplicador ni el límite del plan.",
)
@pytest.mark.parametrize("resolution", ["8K", "9:16"])
def test_unknown_resolution_is_rejected(resolution):
    with pytest.raises(ValueError):
        cost(60, resolution)


@pytest.mark.parametrize(
    "features, extra",
    [
        ({"seo_optimization": True}, Decimal("5.00")),
        ({"ai_thumbnail": True}, Decimal("15.00")),
        ({"seo_optimization": True, "ai_thumbnail": True}, Decimal("20.00")),
        ({"seo_optimization": False, "ai_thumbnail": False}, Decimal("0.00")),
        ({"feature_inexistente": True}, Decimal("0.00")),
    ],
)
def test_feature_addons_are_added_to_base(features, extra):
    assert cost(10, features=features) == Decimal("5.00") + extra


def test_result_has_two_decimals_and_rounds_half_up():
    # 3s * 0.5 * 1.5 = 2.25 -> con descuento 0.10 => 2.025 -> 2.03 (ROUND_HALF_UP)
    class Plan:
        name = "custom"
        max_video_duration_seconds = 100
        max_resolution = "4K"
        has_seo_optimization = True
        has_thumbnail_engine = True
        base_discount_rate = Decimal("0.10")

    result = cost(3, "1080p", plan=Plan())
    assert result == Decimal("2.03")
    assert result.as_tuple().exponent == -2


# --- Edge cases ----------------------------------------------------------------

def test_negative_duration_is_rejected():
    with pytest.raises(ValueError):
        cost(-1)


def test_zero_duration_charges_minimum_of_one_cent():
    assert cost(0) == Decimal("0.01")


def test_full_discount_still_charges_minimum(pro_plan):
    pro_plan.base_discount_rate = Decimal("1.00")
    assert cost(60, plan=pro_plan) == Decimal("0.01")


# --- Plan free ------------------------------------------------------------------

def test_free_plan_basic_render(free_plan):
    assert cost(60, "720p", plan=free_plan) == Decimal("30.00")


def test_free_plan_rejects_duration_over_limit(free_plan):
    with pytest.raises(PlanLimitExceededError):
        cost(61, plan=free_plan)


def test_free_plan_accepts_duration_exactly_at_limit(free_plan):
    assert cost(free_plan.max_video_duration_seconds, plan=free_plan) == Decimal("30.00")


@pytest.mark.parametrize("resolution", ["1080p", "4K"])
def test_free_plan_rejects_higher_resolutions(free_plan, resolution):
    with pytest.raises(PlanLimitExceededError):
        cost(10, resolution, plan=free_plan)


@pytest.mark.parametrize("feature", ["seo_optimization", "ai_thumbnail"])
def test_free_plan_rejects_premium_features(free_plan, feature):
    with pytest.raises(FeatureNotAllowedError):
        cost(10, features={feature: True}, plan=free_plan)


def test_free_plan_ignores_features_set_to_false(free_plan):
    assert cost(10, features={"ai_thumbnail": False}, plan=free_plan) == Decimal("5.00")


# --- Plan pro -------------------------------------------------------------------

def test_pro_plan_applies_discount_to_base_and_addons(pro_plan):
    # (120 * 0.5 * 3.0 + 5 + 15) * (1 - 0.20) = (180 + 20) * 0.8 = 160
    features = {"seo_optimization": True, "ai_thumbnail": True}
    assert cost(120, "4K", features, pro_plan) == Decimal("160.00")


def test_pro_plan_is_cheaper_than_no_plan(pro_plan):
    assert cost(60, "1080p", plan=pro_plan) < cost(60, "1080p")


def test_pro_plan_still_enforces_duration_limit(pro_plan):
    with pytest.raises(PlanLimitExceededError):
        cost(pro_plan.max_video_duration_seconds + 1, plan=pro_plan)


def test_plan_limit_errors_are_django_validation_errors():
    from django.core.exceptions import ValidationError
    assert issubclass(PlanLimitExceededError, ValidationError)
    assert issubclass(FeatureNotAllowedError, ValidationError)
