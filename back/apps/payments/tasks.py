"""Periodic work of payments (CELERY_BEAT_SCHEDULE in backend/settings/base.py)."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.payments.tasks.apply_due_plan_changes', ignore_result=True)
def apply_due_plan_changes():
    """
    Applies the plan downgrades whose paid period ended and ends the paid
    periods nobody renewed (services/subscriptions.py), and rejects the
    payments whose checkout never opened or outlived
    PAYMENTS_CHECKOUT_TTL_SECONDS (services/payments.py). Each workspace is
    decided under its row lock, so a second beat, or a change or a
    confirmation made at the same time, cannot apply one twice.
    """
    from .services.payments import expire_checkouts, fail_stale_checkouts
    from .services.subscriptions import apply_due_plan_changes as apply_due

    applied = apply_due()
    failed = fail_stale_checkouts()
    expired = expire_checkouts()
    logger.info(
        'subscription.due_changes_done applied=%s stale_checkouts=%s expired_checkouts=%s', applied, failed, expired,
    )
    return applied
