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
- A request that repeats its ``Idempotency-Key`` gets the payment the first
  one started, never a second checkout.

Logs carry ids, amounts and error classes only.
"""
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.users.models import Workspace

from ..models import Payment, Transaction, Wallet
from .payment_providers import require_payment_provider
from .plan_periods import PLAN_FIELDS, add_month, switch_plan

logger = logging.getLogger(__name__)

DEFAULT_REJECTION = 'El proveedor de pagos rechazó el pago.'
CHECKOUT_FAILED = 'No pudimos abrir el checkout del proveedor de pagos.'


class CheckoutFailed(Exception):
    """The provider did not open the checkout. The payment is left rejected: nothing was charged."""


class IdempotencyKeyReused(Exception):
    """The Idempotency-Key already started another payment (other tokens or a plan)."""


def format_tokens(tokens):
    """1000 → "1.000", as the app writes amounts."""
    return f'{tokens:,}'.replace(',', '.')


def open_checkout(provider, payment, return_url):
    """Ask the provider for the checkout of a new payment and keep its URL and reference."""
    try:
        checkout = provider.start_checkout(payment, return_url)
    except Exception as exc:  # any provider error: the payment must not stay pending
        logger.warning(
            'payments.checkout_failed payment=%s provider=%s error=%s', payment.pk, provider.name, type(exc).__name__,
        )
        settle_payment(payment.pk, approved=False, failure_reason=CHECKOUT_FAILED)
        raise CheckoutFailed() from exc
    Payment.objects.filter(pk=payment.pk).update(checkout_url=checkout.url, provider_reference=checkout.reference)
    payment.checkout_url = checkout.url
    payment.provider_reference = checkout.reference
    return payment


def _replay(payment, tokens, package_id):
    if payment.plan_id is not None or payment.tokens != tokens or payment.package_id != package_id:
        raise IdempotencyKeyReused()
    return payment


def start_token_purchase(workspace, *, user, tokens, amount_usd, package_id, return_url, idempotency_key=None):
    """
    A pending token purchase with the provider's checkout. Returns
    ``(payment, created)``: ``created`` is False when ``idempotency_key``
    already started this same purchase. Raises PaymentsUnavailable (no
    provider: nothing is written), IdempotencyKeyReused or CheckoutFailed.
    """
    provider = require_payment_provider()
    if idempotency_key:
        existing = Payment.objects.filter(workspace=workspace, idempotency_key=idempotency_key).first()
        if existing is not None:
            return _replay(existing, tokens, package_id), False
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
        if existing is None:
            raise
        return _replay(existing, tokens, package_id), False
    open_checkout(provider, payment, return_url)
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
    plan with a paid period of one month from now (and drops a scheduled
    downgrade). Returns ``(payment, changed)``; ``changed`` is False when the
    payment was already settled, and then nothing is written.
    """
    workspace_id = Payment.objects.values_list('workspace_id', flat=True).get(pk=payment_id)
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=workspace_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        if payment.status != Payment.Status.PENDING:
            logger.info('payments.settle_ignored payment=%s status=%s', payment.pk, payment.status)
            return payment, False
        now = timezone.now()
        if not approved:
            payment.status = Payment.Status.REJECTED
            payment.failure_reason = (failure_reason or DEFAULT_REJECTION)[:255]
        elif payment.plan_id is not None:
            switch_plan(workspace, payment.plan, paid_until=add_month(now))
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
