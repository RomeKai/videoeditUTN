"""Public plans (PAY-03): the cached payload of `GET /api/v1/plans/` and the default plan."""
from django.core.cache import cache

from apps.payments.models import SubscriptionPlan
from apps.payments.serializers import PublicPlanSerializer

PUBLIC_PLANS_CACHE_KEY = 'payments:public-plans'
PUBLIC_PLANS_CACHE_TTL = 60 * 60


def get_public_plans():
    """Active plans in display order. Cached for an hour; any plan change refreshes it."""
    payload = cache.get(PUBLIC_PLANS_CACHE_KEY)
    if payload is None:
        plans = SubscriptionPlan.objects.filter(is_active=True)
        payload = PublicPlanSerializer(plans, many=True).data
        cache.set(PUBLIC_PLANS_CACHE_KEY, payload, PUBLIC_PLANS_CACHE_TTL)
    return payload


def invalidate_public_plans():
    cache.delete(PUBLIC_PLANS_CACHE_KEY)
