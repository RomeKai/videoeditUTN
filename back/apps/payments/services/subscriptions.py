"""
The subscription of a workspace (gap 12): what GET /api/v1/subscription/
shows, the rules of POST /api/v1/subscription/change/ and the scheduled
downgrades that the beat task applies.

- Going up (a plan later in ``sort_order``, the order of the pricing page)
  to a plan with a price needs a payment: nothing changes until the provider
  confirms it (services/payments.py). To a plan without a price it applies at once.
- Going down waits for the end of the paid period (plan_periods.py). Without
  a paid period there is nothing to wait for, and it applies at once.
- No change, up or down, leaves the workspace with more seats in use
  (members plus pending invitations, apps.users.seats) than the new plan has.
- Choosing the current plan cancels a scheduled downgrade.

Every change locks the workspace row first; settle_payment does too, so two
changes, or a change and a payment confirmation, never interleave.
"""
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.users.models import Workspace
from apps.users.seats import seats_used

from ..models import Payment, SubscriptionPlan
from .payment_providers import get_payment_provider, require_payment_provider
from .payments import open_checkout, settle_payment
from .plan_periods import PLAN_FIELDS, is_paid, paid_period_end, switch_plan
from .plans import get_default_plan_id

logger = logging.getLogger(__name__)

REPLACED = 'Elegiste otro plan antes de pagar este.'


class PlanChangeError(Exception):
    pass


class PlanNotFound(PlanChangeError):
    """The plan does not exist, is inactive or is not public."""


class NothingToCancel(PlanChangeError):
    """It is the current plan and no change is scheduled."""


class SeatsExceeded(PlanChangeError):
    def __init__(self, used, plan, downgrade):
        super().__init__()
        self.used = used
        self.plan = plan
        self.downgrade = downgrade

    @property
    def message(self):
        verb = 'bajar' if self.downgrade else 'cambiar de plan'
        return (
            f'Ocupás {self.used} lugares y el plan {self.plan.name} tiene {self.plan.max_members}. '
            f'Quitá miembros o invitaciones antes de {verb}.'
        )


@dataclass
class PlanChange:
    # 'now' or 'period_end'.
    applied: str
    # The provider's checkout when the upgrade needs a payment.
    checkout_url: str | None
    workspace: Workspace


def current_plan(workspace):
    """
    The plan of the workspace. One without a plan (from before the default
    plan existed: see assign_default_plan) shows the default plan; None if
    there is none either.
    """
    if workspace.subscription_plan_id is not None:
        return workspace.subscription_plan
    default_id = get_default_plan_id()
    return SubscriptionPlan.objects.filter(pk=default_id).first() if default_id else None


def subscription_payload(workspace, now=None):
    """What SubscriptionSerializer writes, or None when there is no plan to show."""
    now = now or timezone.now()
    plan = current_plan(workspace)
    if plan is None:
        return None
    pending = None
    if workspace.pending_plan_id is not None:
        pending = {
            'id': workspace.pending_plan_id,
            'name': workspace.pending_plan.name,
            'effective_at': workspace.pending_plan_effective_at,
        }
    return {
        'plan': plan,
        # Nothing charges a renewal yet (PAY-02), so nothing can be overdue.
        'status': 'active',
        'renews_at': paid_period_end(workspace, now),
        'pending_plan': pending,
    }


def is_upgrade(current, target):
    """Later in the pricing page's order. Like the web app (PlansScreen): a tie is not an upgrade."""
    return current is None or target.sort_order > current.sort_order


def _replace_pending_upgrade(workspace, keep_plan=None):
    """
    The upgrade payment the workspace is waiting for, if it is for ``keep_plan``.
    One for another plan is rejected (the person chose something else) and
    returned in the second place, to close its checkout after the commit.
    """
    pending = Payment.objects.filter(workspace=workspace, status=Payment.Status.PENDING, plan__isnull=False).first()
    if pending is None:
        return None, None
    if keep_plan is not None and pending.plan_id == keep_plan.pk and pending.amount_usd == keep_plan.price_usd_monthly and pending.checkout_url:
        return pending, None
    settle_payment(pending.pk, approved=False, failure_reason=REPLACED)
    return None, pending


def change_plan(workspace_id, plan_id, *, user, return_url, now=None):
    """
    Apply, schedule or start paying the change to ``plan_id`` (see the module).
    Raises PlanNotFound, NothingToCancel, SeatsExceeded, PaymentsUnavailable
    (an upgrade with a price and no provider: nothing changes) or CheckoutFailed.
    """
    now = now or timezone.now()
    new_payment = provider = replaced = None
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
        current = workspace.subscription_plan

        if current is not None and current.pk == plan_id:
            if workspace.pending_plan_id is None:
                raise NothingToCancel()
            workspace.pending_plan = None
            workspace.pending_plan_effective_at = None
            workspace.save(update_fields=['pending_plan', 'pending_plan_effective_at'])
            logger.info('subscription.change_canceled workspace=%s plan=%s', workspace.pk, current.pk)
            return PlanChange('now', None, workspace)

        target = SubscriptionPlan.objects.filter(pk=plan_id, is_active=True, is_public=True).first()
        if target is None:
            raise PlanNotFound()
        upgrade = is_upgrade(current, target)
        used = seats_used(workspace)
        if used > target.max_members:
            raise SeatsExceeded(used, target, downgrade=not upgrade)

        if upgrade and is_paid(target):
            # Checked before anything changes: without a provider nothing is paid and nothing moves.
            provider = require_payment_provider()
            reused, replaced = _replace_pending_upgrade(workspace, keep_plan=target)
            if reused is not None:
                logger.info('subscription.upgrade_payment_reused workspace=%s payment=%s', workspace.pk, reused.pk)
                return PlanChange('now', reused.checkout_url, workspace)
            new_payment = Payment.objects.create(
                workspace=workspace,
                created_by=user,
                plan=target,
                tokens=0,
                amount_usd=target.price_usd_monthly,
                provider=provider.name,
            )
            applied = 'now'
        else:
            _, replaced = _replace_pending_upgrade(workspace)
            effective_at = None if upgrade else paid_period_end(workspace, now)
            if effective_at is None:
                switch_plan(workspace, target, paid_until=None)
                workspace.save(update_fields=PLAN_FIELDS)
                applied = 'now'
            else:
                workspace.pending_plan = target
                workspace.pending_plan_effective_at = effective_at
                workspace.save(update_fields=['pending_plan', 'pending_plan_effective_at'])
                applied = 'period_end'
            logger.info(
                'subscription.changed workspace=%s plan=%s previous_plan=%s applied=%s',
                workspace.pk, target.pk, current.pk if current else None, applied,
            )

    if replaced is not None:
        _close_checkout(replaced)
    if new_payment is None:
        return PlanChange(applied, None, workspace)
    open_checkout(provider, new_payment, return_url)
    logger.info(
        'payments.started payment=%s kind=plan plan=%s amount_usd=%s provider=%s',
        new_payment.pk, target.pk, new_payment.amount_usd, provider.name,
    )
    return PlanChange(applied, new_payment.checkout_url, workspace)


def _close_checkout(payment):
    provider = get_payment_provider()
    if provider is None or provider.name != payment.provider:
        return
    try:
        provider.cancel_checkout(payment)
    except Exception as exc:  # the payment is already rejected here: a late confirmation changes nothing
        logger.warning('payments.cancel_checkout_failed payment=%s error=%s', payment.pk, type(exc).__name__)


def apply_due_plan_change(workspace_id, now=None):
    """Apply the scheduled downgrade of one workspace if its date came. True when it changed."""
    now = now or timezone.now()
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().filter(pk=workspace_id).first()
        if workspace is None or workspace.pending_plan_id is None or workspace.pending_plan_effective_at > now:
            return False
        previous = workspace.subscription_plan_id
        target = workspace.pending_plan
        used = seats_used(workspace)
        # The new plan starts without a paid period: nothing charges it yet (PAY-02).
        switch_plan(workspace, target, paid_until=None)
        workspace.save(update_fields=PLAN_FIELDS)
    logger.info(
        'subscription.plan_change_applied workspace=%s plan=%s previous_plan=%s seats_used=%s max_members=%s',
        workspace.pk, target.pk, previous, used, target.max_members,
    )
    if used > target.max_members:
        # Invited while the change waited: they keep their seats, nobody new fits until some leave.
        logger.warning(
            'subscription.over_member_limit workspace=%s seats_used=%s max_members=%s', workspace.pk, used, target.max_members,
        )
    return True


def apply_due_plan_changes(now=None):
    """Every scheduled downgrade whose date came. Returns how many were applied. One failure never stops the rest."""
    now = now or timezone.now()
    due = list(
        Workspace.objects.filter(pending_plan__isnull=False, pending_plan_effective_at__lte=now)
        .order_by('pending_plan_effective_at')
        .values_list('pk', flat=True)
    )
    applied = 0
    for workspace_id in due:
        try:
            applied += apply_due_plan_change(workspace_id, now=now)
        except Exception as exc:  # logged; the next run tries again
            logger.error('subscription.plan_change_failed workspace=%s error=%s', workspace_id, type(exc).__name__)
    return applied
