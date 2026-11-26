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
  provider call) counts as failed after twice PAYMENTS_PROVIDER_TIMEOUT_SECONDS:
  the next request, or the beat task (fail_stale_checkouts), rejects it and
  frees its key, so nothing stays "in progress" for good.
- A confirmation for a payment already rejected (replaced, canceled, or its
  checkout failed) changes nothing: it is logged as ``payment.late_paid`` so
  staff can refund it by hand.

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

from apps.users.models import Workspace

from ..models import Payment, Transaction, Wallet
from .payment_providers import require_payment_provider
from .plan_periods import PLAN_FIELDS, start_paid_period

logger = logging.getLogger(__name__)

DEFAULT_REJECTION = 'El proveedor de pagos rechazó el pago.'
CHECKOUT_FAILED = 'No pudimos abrir el checkout del proveedor de pagos.'


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
    """The checkout did not open: the payment is rejected and frees its Idempotency-Key for a new attempt."""
    settled, _ = settle_payment(payment.pk, approved=False, failure_reason=CHECKOUT_FAILED)
    Payment.objects.filter(pk=payment.pk).update(idempotency_key=None)
    settled.idempotency_key = None
    return settled


def stale_checkout_cutoff(now=None):
    """Before this moment, a pending payment still without a checkout lost its provider call."""
    return (now or timezone.now()) - timedelta(seconds=2 * settings.PAYMENTS_PROVIDER_TIMEOUT_SECONDS)


def checkout_is_stale(payment, now=None):
    return (
        payment.status == Payment.Status.PENDING
        and not payment.checkout_url
        and payment.created_at < stale_checkout_cutoff(now)
    )


def fail_stale_checkout(payment):
    """An attempt whose checkout never opened: rejected, its key freed. A checkout that opens later is closed (open_checkout)."""
    logger.warning('payments.stale_checkout_failed payment=%s provider=%s', payment.pk, payment.provider)
    return _fail_checkout(payment)


def fail_stale_checkouts(now=None):
    """The beat's sweep of fail_stale_checkout. Returns how many it rejected."""
    stale = Payment.objects.filter(
        status=Payment.Status.PENDING, checkout_url='', created_at__lt=stale_checkout_cutoff(now),
    ).values_list('pk', flat=True)
    failed = 0
    for payment in Payment.objects.filter(pk__in=list(stale)):
        try:
            if fail_stale_checkout(payment).status == Payment.Status.REJECTED:
                failed += 1
        except Exception as exc:  # logged; the next run tries again
            logger.error('payments.stale_checkout_sweep_failed payment=%s error=%s', payment.pk, type(exc).__name__)
    return failed


def _close(provider, payment):
    try:
        provider.cancel_checkout(payment)
    except Exception as exc:  # nothing can pay a settled payment anyway: a late payment is logged
        logger.warning('payments.cancel_checkout_failed payment=%s error=%s', payment.pk, type(exc).__name__)


def open_checkout(provider, payment, return_url):
    """
    Ask the provider for the checkout of a new payment and keep its URL and
    reference, only while the payment is still pending. When it was settled
    meanwhile (another plan was chosen, the plan was kept) the new checkout
    is closed and the payment pending now is returned instead (the
    workspace's upgrade waiting for its payment, or this one as it is).
    """
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
    current = None
    if payment.plan_id is not None:
        current = Payment.objects.filter(
            workspace_id=payment.workspace_id, status=Payment.Status.PENDING, plan__isnull=False,
        ).first()
    return current or Payment.objects.get(pk=payment.pk)


def _replay(payment, tokens, package_id):
    """The payment a repeated key answers, or None when its attempt was lost (its key is free again)."""
    if payment.plan_id is not None or payment.tokens != tokens or payment.package_id != package_id:
        raise IdempotencyKeyReused()
    if payment.status == Payment.Status.PENDING and not payment.checkout_url:
        if checkout_is_stale(payment):
            fail_stale_checkout(payment)
            return None
        raise PurchaseInProgress()
    return payment


def start_token_purchase(workspace, *, user, tokens, amount_usd, package_id, return_url, idempotency_key=None):
    """
    A pending token purchase with the provider's checkout. Returns
    ``(payment, created)``: ``created`` is False when ``idempotency_key``
    already started this same purchase. Raises PaymentsUnavailable (no
    provider: nothing is written), IdempotencyKeyReused, PurchaseInProgress
    or CheckoutFailed.
    """
    provider = require_payment_provider()
    if idempotency_key:
        existing = Payment.objects.filter(workspace=workspace, idempotency_key=idempotency_key).first()
        replayed = _replay(existing, tokens, package_id) if existing is not None else None
        if replayed is not None:
            return replayed, False
    try:
        with transaction.atomic():
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


def settle_payment(payment_id, *, approved, failure_reason=''):
    """
    Approve or reject a pending payment, once. Approving a token purchase
    credits its tokens; approving an upgrade payment puts the workspace on its
    plan with a paid period of one month from now, in the workspace's time
    zone (and drops a scheduled downgrade). Returns ``(payment, changed)``;
    ``changed`` is False when the payment was already settled, and then
    nothing is written (a late approval of a rejected one is logged).
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
        else:
            _credit(payment, now)
            payment.status = Payment.Status.APPROVED
        payment.checkout_url = ''
        payment.settled_at = now
        payment.save()
    logger.info(
        'payments.settled payment=%s kind=%s status=%s tokens=%s amount_usd=%s',
        payment.pk, 'plan' if payment.plan_id else 'tokens', payment.status, payment.tokens, payment.amount_usd,
    )
    return payment, True
