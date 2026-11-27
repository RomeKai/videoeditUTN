"""
The life of a Payment: started with a checkout of the provider, settled once.

Money rules:
- Settling (``settle_payment``) locks the workspace row and then the payment
  row (always in that order, like ``subscriptions.change_plan``). Only a
  ``pending`` payment changes: a second confirmation, or an approval after a
  rejection, returns the payment as it is. So a token purchase is credited
  once and an upgrade payment changes the plan once.
- The credit is one DEPOSIT + COMPLETED Transaction (category PURCHASE) in the workspace's wallet,
  written in the same database transaction as the new balance and the
  payment's status, and linked from ``Payment.credit_transaction`` (unique,
  and required by a check constraint on an approved token purchase).
- A checkout is handed out only for a payment that is still pending: the URL
  is written with ``status=pending`` in the same UPDATE, and a checkout that
  arrives for a payment settled meanwhile is closed at the provider.
- A request that repeats its ``Idempotency-Key`` (per workspace) gets the
  payment the first one started, never a second checkout; while that one is
  still opening its checkout it answers PurchaseInProgress. A failed checkout
  frees its key, so the same key can try again.
- An attempt whose checkout never opened (the process died inside the
  provider call) counts as failed twice PAYMENTS_PROVIDER_TIMEOUT_SECONDS
  after that call started (``checkout_started_at``, stamped right before it):
  the next request, or the beat task (fail_stale_checkouts), decides it again
  on the locked row, rejects it and frees its key, so nothing stays "in
  progress" for good. A row whose checkout was stored meanwhile is left alone.
- A checkout that opened lives PAYMENTS_CHECKOUT_TTL_SECONDS from that same
  ``checkout_started_at``: past it, the beat task (expire_checkouts) decides
  it again on the locked row (workspace, then payment), rejects it and closes
  it at the provider. A row that a confirmation settled meanwhile is left
  alone. An expired checkout is never handed out again.
- A confirmation for a payment already rejected (replaced, canceled, expired,
  or its checkout failed) changes nothing: it is logged as
  ``payment.late_paid`` so staff can refund it by hand.
- Creating a token purchase locks the workspace row first, like every other
  change of its payments: a workspace delete that holds it either ends first
  (and the purchase finds no workspace) or sees the pending payment and
  refuses (apps/users/views_workspaces.py).

The workspace row is locked FOR NO KEY UPDATE: it serializes plan changes and
settlements without blocking the rows that point to the workspace.
Logs carry ids, amounts and error classes only.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from apps.notifications.services import notify_payment_settled
from apps.users.models import Workspace
from apps.users.seats import seats_used

from ..models import Payment, Transaction, Wallet
from .payment_providers import get_payment_provider, require_payment_provider
from .plan_periods import PLAN_FIELDS, start_paid_period

logger = logging.getLogger(__name__)

DEFAULT_REJECTION = 'El proveedor de pagos rechazó el pago.'
CHECKOUT_FAILED = 'No pudimos abrir el checkout del proveedor de pagos.'
CHECKOUT_EXPIRED = 'El checkout venció sin que se pagara.'


class CheckoutFailed(Exception):
    """The provider did not open the checkout. The payment is left rejected: nothing was charged."""


class IdempotencyKeyReused(Exception):
    """The Idempotency-Key already started another payment (other tokens or a plan)."""


class PurchaseInProgress(Exception):
    """The Idempotency-Key started a purchase that is still opening its checkout."""


def format_tokens(tokens):
    """1000 → "1.000", as the app writes amounts."""
    return f'{tokens:,}'.replace(',', '.')


def lock_workspace(workspace_id):
    """The workspace row, locked until the transaction ends (FOR NO KEY UPDATE)."""
    return Workspace.objects.select_for_update(no_key=True).get(pk=workspace_id)


def _fail_checkout(payment):
    """
    The checkout did not open: the payment is rejected and frees its
    Idempotency-Key for a new attempt. A payment that a confirmation settled
    first keeps its status and its key.
    """
    settled, _ = settle_payment(payment.pk, approved=False, failure_reason=CHECKOUT_FAILED, notify=False)
    if Payment.objects.filter(pk=payment.pk, status=Payment.Status.REJECTED).update(idempotency_key=None):
        settled.idempotency_key = None
    return settled


def stale_checkout_cutoff(now=None):
    """Before this moment, a pending payment still without a checkout lost its provider call."""
    return (now or timezone.now()) - timedelta(seconds=2 * settings.PAYMENTS_PROVIDER_TIMEOUT_SECONDS)


def checkout_is_stale(payment, now=None):
    """Pending, without a checkout, and its provider call (``checkout_started_at``) started over twice the timeout ago."""
    return (
        payment.status == Payment.Status.PENDING
        and not payment.checkout_url
        and payment.checkout_started_at < stale_checkout_cutoff(now)
    )


def _close(provider, payment):
    try:
        provider.cancel_checkout(payment)
    except Exception as exc:  # nothing can pay a settled payment anyway: a late payment is logged
        logger.warning('payments.cancel_checkout_failed payment=%s error=%s', payment.pk, type(exc).__name__)


def close_checkout(payment):
    """Close the provider's checkout of a settled payment, when its provider is the one configured. Never raises."""
    provider = get_payment_provider()
    if provider is not None and provider.name == payment.provider:
        _close(provider, payment)


def fail_stale_checkout(payment, now=None):
    """
    An attempt whose checkout never opened: rejected and its key freed, if the
    row, locked (workspace first, then payment, like settle_payment), is still
    pending without a checkout and stale. A row whose checkout was stored
    meanwhile, or that a confirmation settled, is left as it is. Returns
    ``(payment, failed)``. A checkout that opens later is closed (open_checkout).
    """
    payment_id = getattr(payment, 'pk', payment)
    workspace_id = Payment.objects.values_list('workspace_id', flat=True).get(pk=payment_id)
    with transaction.atomic():
        lock_workspace(workspace_id)
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if not checkout_is_stale(locked, now):
            return locked, False
        logger.warning('payments.stale_checkout_failed payment=%s provider=%s', locked.pk, locked.provider)
        settled = _fail_checkout(locked)
    if settled.status == Payment.Status.REJECTED and settled.provider_reference:
        close_checkout(settled)
    return settled, settled.status == Payment.Status.REJECTED


def checkout_lifetime():
    """PAYMENTS_CHECKOUT_TTL_SECONDS as the app writes it: "24 h", or "90 min" when it is not whole hours."""
    seconds = settings.PAYMENTS_CHECKOUT_TTL_SECONDS
    if seconds % 3600 == 0:
        return f'{seconds // 3600} h'
    return f'{-(-seconds // 60)} min'


def payment_pending_message():
    """
    What a person can do about a payment that waits for its provider: pay it,
    or wait for its checkout to expire. It covers token purchases too, and
    the web has no way to cancel one of those (a plan payment has: see
    plan_payment_pending_message).
    """
    return f'Hay un pago en curso: terminalo, o esperá a que venza ({checkout_lifetime()} después de abrirlo).'


def plan_payment_pending_message(current_plan_name):
    """
    The same for a plan payment, which the web can cancel: Planes offers to
    keep the current plan («Seguir con …»), and that rejects the payment.
    """
    return f'Hay un pago en curso: terminalo, o elegí «Seguir con {current_plan_name}» en Planes para cancelarlo.'


def checkout_expiry_cutoff(now=None):
    """Before this moment, an open checkout outlived PAYMENTS_CHECKOUT_TTL_SECONDS."""
    return (now or timezone.now()) - timedelta(seconds=settings.PAYMENTS_CHECKOUT_TTL_SECONDS)


def checkout_expires_at(payment):
    """When an open checkout stops being handed out (see checkout_is_expired); None while it is still opening."""
    if not payment.checkout_url:
        return None
    return payment.checkout_started_at + timedelta(seconds=settings.PAYMENTS_CHECKOUT_TTL_SECONDS)


def checkout_is_expired(payment, now=None):
    """Pending, with a checkout, and its provider call (``checkout_started_at``) started over the checkout's lifetime ago."""
    return (
        payment.status == Payment.Status.PENDING
        and bool(payment.checkout_url)
        and payment.checkout_started_at < checkout_expiry_cutoff(now)
    )


def expire_checkout(payment, now=None):
    """
    A checkout nobody paid in its lifetime: rejected and closed at the
    provider, if the row, locked (workspace first, then payment, like
    settle_payment), is still pending with an expired checkout. A row that a
    confirmation settled meanwhile is left as it is; a confirmation that
    arrives later is a late payment (``payment.late_paid``). Keeps its
    Idempotency-Key: a replay answers this payment, rejected. Returns
    ``(payment, expired)``; ``payment`` is None if the row is gone.
    """
    payment_id = getattr(payment, 'pk', payment)
    workspace_id = Payment.objects.filter(pk=payment_id).values_list('workspace_id', flat=True).first()
    if workspace_id is None:
        return None, False
    with transaction.atomic():
        lock_workspace(workspace_id)
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if not checkout_is_expired(locked, now):
            return locked, False
        logger.info('payments.checkout_expired payment=%s provider=%s', locked.pk, locked.provider)
        settled, _ = settle_payment(locked.pk, approved=False, failure_reason=CHECKOUT_EXPIRED, notify=False)
    if settled.status == Payment.Status.REJECTED:
        close_checkout(settled)
    return settled, settled.status == Payment.Status.REJECTED


def expire_checkouts(now=None):
    """The beat's sweep of expire_checkout. Each row is decided again under its lock. Returns how many it rejected."""
    candidates = list(
        Payment.objects.filter(status=Payment.Status.PENDING, checkout_started_at__lt=checkout_expiry_cutoff(now))
        .exclude(checkout_url='')
        .values_list('pk', flat=True)
    )
    expired = 0
    for payment_id in candidates:
        try:
            expired += expire_checkout(payment_id, now=now)[1]
        except Exception as exc:  # logged; the next run tries again
            logger.error('payments.checkout_expiry_failed payment=%s error=%s', payment_id, type(exc).__name__)
    return expired


def fail_stale_checkouts(now=None):
    """The beat's sweep of fail_stale_checkout. Each row is decided again under its lock. Returns how many it rejected."""
    candidates = list(
        Payment.objects.filter(
            status=Payment.Status.PENDING, checkout_url='', checkout_started_at__lt=stale_checkout_cutoff(now),
        ).values_list('pk', flat=True)
    )
    failed = 0
    for payment_id in candidates:
        try:
            failed += fail_stale_checkout(payment_id, now=now)[1]
        except Exception as exc:  # logged; the next run tries again
            logger.error('payments.stale_checkout_sweep_failed payment=%s error=%s', payment_id, type(exc).__name__)
    return failed


def _pending_now(payment):
    """What a request whose payment was settled gets instead: the workspace's plan payment pending now, or this one as it is."""
    current = None
    if payment.plan_id is not None:
        current = Payment.objects.filter(
            workspace_id=payment.workspace_id, status=Payment.Status.PENDING, plan__isnull=False,
        ).first()
    return current or Payment.objects.get(pk=payment.pk)


def open_checkout(provider, payment, return_url):
    """
    Ask the provider for the checkout of a new payment and keep its URL and
    reference, only while the payment is still pending. When it was settled
    meanwhile (another plan was chosen, the plan was kept) the new checkout
    is closed and the payment pending now is returned instead (the
    workspace's upgrade waiting for its payment, or this one as it is).
    """
    # Staleness counts from here: whatever came before (closing a replaced checkout) does not eat its time.
    started_at = timezone.now()
    if not Payment.objects.filter(pk=payment.pk, status=Payment.Status.PENDING).update(checkout_started_at=started_at):
        # Settled before its checkout opened: nothing is asked of the provider.
        logger.info('payments.checkout_not_started payment=%s provider=%s', payment.pk, provider.name)
        return _pending_now(payment)
    payment.checkout_started_at = started_at
    try:
        checkout = provider.start_checkout(payment, return_url)
    except Exception as exc:  # any provider error: the payment must not stay pending
        logger.warning(
            'payments.checkout_failed payment=%s provider=%s error=%s', payment.pk, provider.name, type(exc).__name__,
        )
        _fail_checkout(payment)
        raise CheckoutFailed() from exc
    payment.checkout_url = checkout.url
    payment.provider_reference = checkout.reference
    try:
        with transaction.atomic():
            stored = Payment.objects.filter(pk=payment.pk, status=Payment.Status.PENDING).update(
                checkout_url=checkout.url, provider_reference=checkout.reference,
            )
    except DataError as exc:  # a URL longer than the column: never a 500 with a checkout open
        logger.warning('payments.checkout_not_stored payment=%s error=%s', payment.pk, type(exc).__name__)
        _close(provider, payment)
        _fail_checkout(payment)
        raise CheckoutFailed() from exc
    if stored:
        return payment
    logger.info('payments.checkout_orphaned payment=%s provider=%s', payment.pk, provider.name)
    _close(provider, payment)
    return _pending_now(payment)


def _replay(payment, tokens, package_id):
    """The payment a repeated key answers, or None when its attempt was lost (its key is free again)."""
    if payment.plan_id is not None or payment.tokens != tokens or payment.package_id != package_id:
        raise IdempotencyKeyReused()
    if payment.status == Payment.Status.PENDING and not payment.checkout_url:
        if not checkout_is_stale(payment):
            raise PurchaseInProgress()
        # Decided again under the lock: its checkout may have been stored since it was read.
        payment, failed = fail_stale_checkout(payment)
        if failed:
            return None
        if payment.status == Payment.Status.PENDING and not payment.checkout_url:
            raise PurchaseInProgress()
    if checkout_is_expired(payment):
        # Never hand out a checkout past its lifetime: the replay answers it rejected.
        payment, _ = expire_checkout(payment)
        if payment is None:
            # Gone with its workspace, deleted meanwhile: never answer the copy read before.
            raise Payment.DoesNotExist()
    return payment


def start_token_purchase(workspace, *, user, tokens, amount_usd, package_id, return_url, idempotency_key=None):
    """
    A pending token purchase with the provider's checkout. Returns
    ``(payment, created)``: ``created`` is False when ``idempotency_key``
    already started this same purchase. Raises PaymentsUnavailable (no
    provider: nothing is written), IdempotencyKeyReused, PurchaseInProgress,
    CheckoutFailed, or Workspace.DoesNotExist or Payment.DoesNotExist (the
    workspace was deleted meanwhile: nothing is written).
    """
    provider = require_payment_provider()
    if idempotency_key:
        existing = Payment.objects.filter(workspace=workspace, idempotency_key=idempotency_key).first()
        replayed = _replay(existing, tokens, package_id) if existing is not None else None
        if replayed is not None:
            return replayed, False
    try:
        with transaction.atomic():
            # The workspace's row first (Workspace.DoesNotExist once it was deleted): a delete that
            # holds it ends before this, or sees this payment pending and refuses.
            lock_workspace(workspace.pk)
            payment = Payment.objects.create(
                workspace=workspace,
                created_by=user,
                tokens=tokens,
                amount_usd=amount_usd,
                package_id=package_id,
                provider=provider.name,
                idempotency_key=idempotency_key or None,
            )
    except IntegrityError:
        # Another request with the same key got there first.
        existing = Payment.objects.filter(workspace=workspace, idempotency_key=idempotency_key).first() if idempotency_key else None
        replayed = _replay(existing, tokens, package_id) if existing is not None else None
        if replayed is None:
            raise
        return replayed, False
    payment = open_checkout(provider, payment, return_url)
    logger.info(
        'payments.started payment=%s kind=tokens tokens=%s amount_usd=%s provider=%s',
        payment.pk, tokens, amount_usd, provider.name,
    )
    return payment, True


def _credit(payment, now):
    wallet, _ = Wallet.objects.select_for_update().get_or_create(workspace_id=payment.workspace_id)
    amount = Decimal(payment.tokens)
    wallet.available_balance += amount
    wallet.save(update_fields=['available_balance', 'updated_at'])
    payment.credit_transaction = Transaction.objects.create(
        wallet=wallet,
        created_by_id=payment.created_by_id,
        amount=amount,
        transaction_type=Transaction.Type.DEPOSIT,
        status=Transaction.Status.COMPLETED,
        category=Transaction.Category.PURCHASE,
        description=f'Compra de {format_tokens(payment.tokens)} tokens',
    )
    payment.balance_after = wallet.available_balance


def settle_payment(payment_id, *, approved, failure_reason='', notify=True):
    """
    Approve or reject a pending payment, once. Approving a token purchase
    credits its tokens; approving an upgrade payment puts the workspace on its
    plan with a paid period of one month from now, in the workspace's time
    zone (and drops a scheduled downgrade). Returns ``(payment, changed)``;
    ``changed`` is False when the payment was already settled, and then
    nothing is written (a late approval of a rejected one is logged).

    ``notify``: tells who started the payment how it ended (the bell). False
    for a rejection that is not the provider's answer: a checkout that never
    opened or expired, a plan the person replaced or chose not to pay. "We
    could not charge you" would be false there.
    """
    workspace_id = Payment.objects.values_list('workspace_id', flat=True).get(pk=payment_id)
    with transaction.atomic():
        workspace = lock_workspace(workspace_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status != Payment.Status.PENDING:
            logger.info('payments.settle_ignored payment=%s status=%s', payment.pk, payment.status)
            if approved and payment.status == Payment.Status.REJECTED:
                # Paid through a checkout that no longer counts: the money arrived, nothing changes.
                logger.warning(
                    'payment.late_paid payment=%s workspace=%s amount_usd=%s tokens=%s plan=%s provider=%s reference=%s',
                    payment.pk, payment.workspace_id, payment.amount_usd, payment.tokens, payment.plan_id,
                    payment.provider, payment.provider_reference or '-',
                )
            return payment, False
        now = timezone.now()
        if not approved:
            payment.status = Payment.Status.REJECTED
            payment.failure_reason = (failure_reason or DEFAULT_REJECTION)[:255]
        elif payment.plan_id is not None:
            # No proration: the whole month of the new plan, from now.
            start_paid_period(workspace, payment.plan, now)
            workspace.save(update_fields=PLAN_FIELDS)
            payment.status = Payment.Status.APPROVED
            used = seats_used(workspace)
            if used > payment.plan.max_members:
                # Members from before keep their seats; nobody new fits until some leave (check_seat_limit).
                logger.warning(
                    'subscription.over_member_limit workspace=%s seats_used=%s max_members=%s',
                    workspace.pk, used, payment.plan.max_members,
                )
        else:
            _credit(payment, now)
            payment.status = Payment.Status.APPROVED
        payment.checkout_url = ''
        payment.settled_at = now
        payment.save()
        if notify:
            notify_payment_settled(payment.pk)
    logger.info(
        'payments.settled payment=%s kind=%s status=%s tokens=%s amount_usd=%s',
        payment.pk, 'plan' if payment.plan_id else 'tokens', payment.status, payment.tokens, payment.amount_usd,
    )
    return payment, True
