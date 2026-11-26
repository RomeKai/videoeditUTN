"""
The subscription of a workspace (gap 12): what GET /api/v1/subscription/
shows, the rules of POST /api/v1/subscription/change/ and what the beat task
applies when a paid period ends.

Before PAY-02 every move to a paid plan is a purchase, never the order of the
pricing page nor the price relative to the current plan:
- Any paid plan other than the current one (pricier, cheaper or the same
  price) needs a payment: nothing changes until the provider confirms it
  (services/payments.py), and then it applies at once with a paid month of
  its own. No proration: the rest of the current period is lost. So paying
  once never buys a cheaper paid plan for good.
- Only a plan without a price (the default, Gratis) is scheduled for the end
  of the paid period (plan_periods.py). Without a paid period there is
  nothing to wait for, and it applies at once.
- No change leaves the workspace with more seats in use (members plus
  pending invitations, apps.users.seats) than the new plan has. While a
  downgrade waits, check_seat_limit already counts its seats.
- Choosing the current plan cancels the scheduled downgrade and the plan
  payment waiting for its checkout.
- The plan payment waiting is read with its row locked, so a checkout that
  is still opening either lands before (and is closed at the provider) or
  finds it settled. One whose checkout never opened stops blocking after
  twice PAYMENTS_PROVIDER_TIMEOUT_SECONDS. A request whose payment was
  replaced while its checkout opened never gets another plan's checkout.
- When the paid period ends with no renewal paid (nothing charges one before
  PAY-02), apply_due_plan_changes applies the scheduled downgrade, or else
  puts the workspace on the default plan.

Every change locks the workspace row first (payments.lock_workspace);
settle_payment does too, so two changes, or a change and a payment
confirmation, never interleave.
"""
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.users.models import Workspace
from apps.users.seats import seats_used

from ..models import Payment, SubscriptionPlan
from .payment_providers import require_payment_provider
from .payments import checkout_is_stale, close_checkout, fail_stale_checkout, lock_workspace, open_checkout, settle_payment
from .plan_periods import PLAN_FIELDS, is_paid, paid_period_end, price_of, switch_plan
from .plans import get_default_plan_id

logger = logging.getLogger(__name__)

REPLACED = 'Elegiste otro plan antes de pagar este.'
KEPT = 'Elegiste seguir con tu plan antes de pagar este.'


class PlanChangeError(Exception):
    pass


class PlanNotFound(PlanChangeError):
    """The plan does not exist, is inactive or is not public."""


class NothingToCancel(PlanChangeError):
    """It is the current plan and nothing is scheduled or waiting for its payment."""


class CheckoutInProgress(PlanChangeError):
    """The same plan payment is still opening its checkout (a double submit): it is never replaced."""


class PlanChangeSuperseded(PlanChangeError):
    """Another plan was chosen (or the plan was kept) while this request's checkout opened."""


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
        # Nothing charges a renewal yet (PAY-02): an ended period drops the plan, it is never overdue.
        'status': 'active',
        'renews_at': paid_period_end(workspace, now),
        'pending_plan': pending,
    }


def needs_payment(current, target):
    """Every paid plan other than the current one is a purchase (before PAY-02). Only a free plan is not."""
    return is_paid(target)


def _pending_upgrade(workspace):
    """The plan payment waiting for its checkout, its row locked (the workspace's is already)."""
    return (
        Payment.objects.select_for_update()
        .filter(workspace=workspace, status=Payment.Status.PENDING, plan__isnull=False)
        .first()
    )


def _drop_pending_upgrade(workspace, reason, keep_plan=None):
    """
    The plan payment the workspace is waiting for: kept and returned when it
    is for ``keep_plan`` and its checkout is open; CheckoutInProgress when
    that same payment is still opening its checkout (unless its provider call
    was lost: then it is failed); otherwise rejected (the person chose
    something else) and returned second, as the locked row read again, to
    close its checkout after the commit.
    """
    pending = _pending_upgrade(workspace)
    if pending is None:
        return None, None
    if not pending.checkout_url and checkout_is_stale(pending):
        _, failed = fail_stale_checkout(pending)
        if failed:
            return None, None
    if keep_plan is not None and pending.plan_id == keep_plan.pk:
        if not pending.checkout_url:
            raise CheckoutInProgress()
        if pending.amount_usd == keep_plan.price_usd_monthly:
            return pending, None
    settled, _ = settle_payment(pending.pk, approved=False, failure_reason=reason)
    return None, settled


def change_plan(workspace_id, plan_id, *, user, return_url, now=None):
    """
    Apply, schedule or start paying the change to ``plan_id`` (see the module).
    Raises PlanNotFound, NothingToCancel, SeatsExceeded, CheckoutInProgress,
    PlanChangeSuperseded, PaymentsUnavailable (a paid plan and no provider:
    nothing changes) or CheckoutFailed.
    """
    now = now or timezone.now()
    new_payment = provider = replaced = None
    with transaction.atomic():
        workspace = lock_workspace(workspace_id)
        current = workspace.subscription_plan

        if current is not None and current.pk == plan_id:
            _, replaced = _drop_pending_upgrade(workspace, KEPT)
            if workspace.pending_plan_id is None and replaced is None:
                raise NothingToCancel()
            if workspace.pending_plan_id is not None:
                workspace.pending_plan = None
                workspace.pending_plan_effective_at = None
                workspace.save(update_fields=['pending_plan', 'pending_plan_effective_at'])
            logger.info(
                'subscription.change_canceled workspace=%s plan=%s payment=%s',
                workspace.pk, current.pk, replaced.pk if replaced else None,
            )
            applied = 'now'
        else:
            target = SubscriptionPlan.objects.filter(pk=plan_id, is_active=True, is_public=True).first()
            if target is None:
                raise PlanNotFound()
            paid = needs_payment(current, target)
            used = seats_used(workspace)
            if used > target.max_members:
                raise SeatsExceeded(used, target, downgrade=target.price_usd_monthly < price_of(current))

            if paid:
                # Checked before anything changes: without a provider nothing is paid and nothing moves.
                provider = require_payment_provider()
                reused, replaced = _drop_pending_upgrade(workspace, REPLACED, keep_plan=target)
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
                # A plan without a price: it waits for the end of the paid period.
                _, replaced = _drop_pending_upgrade(workspace, REPLACED)
                effective_at = paid_period_end(workspace, now)
                if effective_at is None:
                    switch_plan(workspace, target)
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
    payment = open_checkout(provider, new_payment, return_url)
    if payment.status != Payment.Status.PENDING or payment.plan_id != new_payment.plan_id:
        # Settled while its checkout opened: another plan was chosen, or the plan was kept.
        # Never hand out the checkout of another plan.
        raise PlanChangeSuperseded()
    if not payment.checkout_url:
        raise CheckoutInProgress()
    logger.info(
        'payments.started payment=%s kind=plan plan=%s amount_usd=%s provider=%s',
        payment.pk, payment.plan_id, payment.amount_usd, provider.name,
    )
    return PlanChange(applied, payment.checkout_url, workspace)


def _close_checkout(payment):
    # The payment is already rejected here: a late confirmation is logged (payment.late_paid).
    close_checkout(payment)


def _log_seats(workspace, plan, used):
    if used > plan.max_members:
        # Members from before keep their seats; nobody new fits until some leave (check_seat_limit).
        logger.warning(
            'subscription.over_member_limit workspace=%s seats_used=%s max_members=%s', workspace.pk, used, plan.max_members,
        )


def apply_due_plan_change(workspace_id, now=None):
    """Apply the scheduled downgrade of one workspace if its date came. True when it changed."""
    now = now or timezone.now()
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update(no_key=True).filter(pk=workspace_id).first()
        if workspace is None or workspace.pending_plan_id is None or workspace.pending_plan_effective_at > now:
            return False
        previous = workspace.subscription_plan_id
        target = workspace.pending_plan
        used = seats_used(workspace)
        # The new plan starts without a paid period: nothing charges it yet (PAY-02).
        switch_plan(workspace, target)
        workspace.save(update_fields=PLAN_FIELDS)
    logger.info(
        'subscription.plan_change_applied workspace=%s plan=%s previous_plan=%s seats_used=%s max_members=%s',
        workspace.pk, target.pk, previous, used, target.max_members,
    )
    _log_seats(workspace, target, used)
    return True


def end_paid_period(workspace_id, now=None):
    """
    A paid period that ended with nothing scheduled and no renewal paid: the
    workspace goes to the default plan (DEFAULT_PLAN_NAME). True when it
    changed. Without a default plan it keeps its plan, and it is logged.
    """
    now = now or timezone.now()
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update(no_key=True).filter(pk=workspace_id).first()
        if (
            workspace is None
            or workspace.pending_plan_id is not None
            or workspace.plan_renews_at is None
            or workspace.plan_renews_at > now
        ):
            return False
        default_id = get_default_plan_id()
        default = SubscriptionPlan.objects.filter(pk=default_id).first() if default_id else None
        if default is None:
            logger.warning('subscription.period_end_without_default workspace=%s', workspace.pk)
            return False
        previous = workspace.subscription_plan_id
        used = seats_used(workspace)
        switch_plan(workspace, default)
        workspace.save(update_fields=PLAN_FIELDS)
    logger.info(
        'subscription.period_ended workspace=%s plan=%s previous_plan=%s seats_used=%s max_members=%s',
        workspace.pk, default.pk, previous, used, default.max_members,
    )
    _log_seats(workspace, default, used)
    return True


def apply_due_plan_changes(now=None):
    """
    Every scheduled downgrade whose date came, then every paid period that
    ended with nothing scheduled. Returns how many workspaces changed. Each
    one is decided under its row lock, so it is safe to run twice; one
    failure never stops the rest.
    """
    now = now or timezone.now()
    due = list(
        Workspace.objects.filter(pending_plan__isnull=False, pending_plan_effective_at__lte=now)
        .order_by('pending_plan_effective_at')
        .values_list('pk', flat=True)
    )
    ended = list(
        Workspace.objects.filter(pending_plan__isnull=True, plan_renews_at__lte=now)
        .order_by('plan_renews_at')
        .values_list('pk', flat=True)
    )
    applied = 0
    for step, workspace_ids in ((apply_due_plan_change, due), (end_paid_period, ended)):
        for workspace_id in workspace_ids:
            try:
                applied += step(workspace_id, now=now)
            except Exception as exc:  # logged; the next run tries again
                logger.error('subscription.plan_change_failed workspace=%s error=%s', workspace_id, type(exc).__name__)
    return applied
