"""Public plans (PAY-03): the cached payload of `GET /api/v1/plans/` and the default plan."""
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from apps.payments.models import SubscriptionPlan
from apps.payments.serializers import PublicPlanSerializer

logger = logging.getLogger(__name__)

# The suffix is the payload version: bump it when the payload or the filter
# changes, so a deploy never serves what the previous code cached.
PUBLIC_PLANS_CACHE_KEY = 'payments:public-plans:v2'
# Every plan change drops the entry (see invalidate_public_plans). The short TTL
# is the safety net for a change that skips the signals, or for a request that
# read the rows just before a commit and cached them just after it.
PUBLIC_PLANS_CACHE_TTL = 5 * 60


def get_public_plans():
    """Active and public plans in display order. Cached for PUBLIC_PLANS_CACHE_TTL."""
    payload = cache.get(PUBLIC_PLANS_CACHE_KEY)
    if payload is None:
        plans = SubscriptionPlan.objects.filter(is_active=True, is_public=True)
        payload = PublicPlanSerializer(plans, many=True).data
        cache.set(PUBLIC_PLANS_CACHE_KEY, payload, PUBLIC_PLANS_CACHE_TTL)
    return payload


def invalidate_public_plans(using='default'):
    """
    Drop the cached list once the current transaction commits (at once outside one).

    Deleting it inside the transaction would let another request read the old
    rows before the commit and cache them again. Call it after any change that
    skips the model signals, such as `QuerySet.update()` on plans.
    """
    transaction.on_commit(lambda: cache.delete(PUBLIC_PLANS_CACHE_KEY), using=using)


def get_default_plan():
    """
    The plan a new workspace starts on: the one named by settings.DEFAULT_PLAN_NAME.

    None when the setting is empty or the plan does not exist yet (for example,
    before the payments migrations ran). That is logged and never raised, so it
    does not block a sign-up; `assign_default_plan` fixes those workspaces later.
    """
    name = getattr(settings, 'DEFAULT_PLAN_NAME', '')
    if not name:
        return None
    plan = SubscriptionPlan.objects.filter(name=name).first()
    if plan is None:
        logger.warning('plans.default_plan_missing setting=DEFAULT_PLAN_NAME')
    return plan
