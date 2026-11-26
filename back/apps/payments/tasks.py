"""Periodic work of payments (CELERY_BEAT_SCHEDULE in backend/settings/base.py)."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.payments.tasks.apply_due_plan_changes', ignore_result=True)
def apply_due_plan_changes():
    """
    Applies the plan downgrades whose paid period ended
    (services/subscriptions.py). Each workspace is decided under its row lock,
    so a second beat, or a change made at the same time, cannot apply one twice.
    """
    from .services.subscriptions import apply_due_plan_changes as apply_due

    applied = apply_due()
    logger.info('subscription.due_changes_done applied=%s', applied)
    return applied
