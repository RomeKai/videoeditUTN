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
PUBLIC_PLANS_CACHE_KEY = 'payments:public-plans:v3'  # v3: allow_social_accounts
# Every plan change drops the entry (see invalidate_public_plans). The short TTL
# is the safety net for a change that skips the signals, or for a request that
# read the rows just before a commit and cached them just after it.
PUBLIC_PLANS_CACHE_TTL = 5 * 60


def _cache_call(operation, call, *args):
    """
    The cache is an optimization: when it fails (Redis unreachable, for example)
    the list is read from the database and a change still commits. Logs the
    error class only.
    """
    try:
        return call(*args)
    except Exception as exc:  # any backend error: the backends raise their own classes
        logger.warning('plans.cache_unavailable operation=%s error=%s', operation, type(exc).__name__)
        return None


def get_public_plans():
    """Active and public plans in display order. Cached for PUBLIC_PLANS_CACHE_TTL."""
    payload = _cache_call('get', cache.get, PUBLIC_PLANS_CACHE_KEY)
    if payload is None:
        plans = SubscriptionPlan.objects.filter(is_active=True, is_public=True)
        payload = PublicPlanSerializer(plans, many=True).data
        _cache_call('set', cache.set, PUBLIC_PLANS_CACHE_KEY, payload, PUBLIC_PLANS_CACHE_TTL)
    return payload


def _drop_public_plans():
    _cache_call('delete', cache.delete, PUBLIC_PLANS_CACHE_KEY)


def invalidate_public_plans(using='default'):
    """
    Drop the cached list once the current transaction commits (at once outside one).

    Deleting it inside the transaction would let another request read the old
    rows before the commit and cache them again. A cache error never fails the
    change: it is logged, and the TTL ends the stale entry. Call it after any
    change that skips the model signals, such as `QuerySet.update()` on plans.
    """
    transaction.on_commit(_drop_public_plans, using=using, robust=True)


def get_default_plan_id():
    """
    Id of the plan a new workspace starts on: the one named by settings.DEFAULT_PLAN_NAME.

    None, logged and never raised, when the setting is empty, the plan does not
    exist yet (before the payments migrations ran), it is inactive, or the lookup
    fails: a sign-up always gets its workspace, and `assign_default_plan` fixes
    those workspaces later. Only `id` and `is_active` are read, without ordering,
    so code that runs before a migration that adds a plan column still works.
    """
    name = getattr(settings, 'DEFAULT_PLAN_NAME', '')
    if not name:
        return None
    try:
        # A savepoint: a failed query must not break the sign-up's transaction.
        with transaction.atomic():
            row = SubscriptionPlan.objects.filter(name=name).order_by().values_list('pk', 'is_active').first()
    except Exception as exc:
        logger.warning('plans.default_plan_lookup_failed error=%s', type(exc).__name__)
        return None
    if row is None:
        logger.warning('plans.default_plan_missing setting=DEFAULT_PLAN_NAME')
        return None
    plan_id, is_active = row
    if not is_active:
        logger.warning('plans.default_plan_inactive plan_id=%s', plan_id)
        return None
    return plan_id
