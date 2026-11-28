"""Stripe Checkout for one-time token purchases."""
import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import stripe
from asgiref.sync import async_to_sync
from django.conf import settings

from .payment_providers import STRIPE, Checkout, PaymentProvider

logger = logging.getLogger(__name__)

MIN_SESSION_SECONDS = 30 * 60
MAX_SESSION_SECONDS = 24 * 60 * 60
EXPIRY_CLOCK_MARGIN_SECONDS = 5
PURCHASE_KIND = 'onecreator_token_purchase'
COMPLETED = 'checkout.session.completed'
EXPIRED = 'checkout.session.expired'


def stripe_configured():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET)


def valid_checkout_ttl():
    minimum = MIN_SESSION_SECONDS + settings.PAYMENTS_PROVIDER_TIMEOUT_SECONDS + EXPIRY_CLOCK_MARGIN_SECONDS
    return minimum <= settings.PAYMENTS_CHECKOUT_TTL_SECONDS <= MAX_SESSION_SECONDS


def _cents(payment):
    return int(payment.amount_usd * 100)


def _success_url(url, payment_id):
    query = [(key, value) for key, value in parse_qsl(url.query, keep_blank_values=True) if key != 'compra']
    query.append(('compra', str(payment_id)))
    return urlunsplit(url._replace(query=urlencode(query)))


@async_to_sync
async def _stripe_request(operation, *args):
    # One deadline across connecting, reading and checking an already closed
    # session. The SDK must not silently retry behind it.
    timeout = settings.PAYMENTS_PROVIDER_TIMEOUT_SECONDS
    transport = stripe.HTTPXClient(timeout=timeout)
    try:
        client = stripe.StripeClient(
            settings.STRIPE_SECRET_KEY, http_client=transport, max_network_retries=0,
        )
        async with asyncio.timeout(timeout):
            return await operation(client.v1.checkout.sessions, *args)
    finally:
        await transport.close_async()


async def _create(sessions, params, payment_id):
    return await sessions.create_async(params, options={'idempotency_key': f'onecreator-payment-{payment_id}'})


async def _expire(sessions, reference):
    try:
        return await sessions.expire_async(reference)
    except stripe.InvalidRequestError:
        # Stripe cannot expire a session that paid or already expired.
        return await sessions.retrieve_async(reference)


def create_checkout_session(payment, return_url):
    price_data = {'currency': 'usd', 'unit_amount': _cents(payment)}
    if settings.STRIPE_TOKEN_PRODUCT_ID:
        price_data['product'] = settings.STRIPE_TOKEN_PRODUCT_ID
    else:
        price_data['product_data'] = {'name': 'OneCreator tokens'}
    url = urlsplit(return_url)
    session = _stripe_request(_create, {
        'mode': 'payment',
        'payment_method_types': ['card'],
        'adaptive_pricing': {'enabled': False},
        'line_items': [{'price_data': price_data, 'quantity': 1}],
        'client_reference_id': str(payment.pk),
        'metadata': {'purchase_kind': PURCHASE_KIND},
        'success_url': _success_url(url, payment.pk),
        'cancel_url': urlunsplit((url.scheme, url.netloc, '/uso/comprar', urlencode({'tokens': payment.tokens}), '')),
        'expires_at': int(payment.checkout_started_at.timestamp()) + settings.PAYMENTS_CHECKOUT_TTL_SECONDS,
    }, payment.pk)
    if not session.id or not session.url:
        raise ValueError('Stripe returned an incomplete Checkout Session.')
    return Checkout(url=session.url, reference=session.id)


class StripeProvider(PaymentProvider):
    name = STRIPE
    supports_plan_payments = False

    def start_checkout(self, payment, return_url):
        if payment.plan_id is not None:
            raise ValueError('Stripe Checkout here is for token purchases.')
        return create_checkout_session(payment, return_url)

    def cancel_checkout(self, payment):
        if payment.provider_reference:
            try:
                _stripe_request(_expire, payment.provider_reference)
            except (stripe.StripeError, TimeoutError) as exc:
                logger.warning('payments.stripe_cancel_failed payment=%s error=%s', payment.pk, type(exc).__name__)


def _session_matches(payment, session):
    return (
        payment.plan_id is None
        and payment.provider_reference
        and payment.provider_reference == session.get('id')
        and str(payment.pk) == session.get('client_reference_id')
        and session.get('object') == 'checkout.session'
        and session.get('mode') == 'payment'
        and session.get('currency') == 'usd'
        and session.get('amount_total') == _cents(payment)
    )


def _apply_session(payment, session, event_type):
    from .payments import CHECKOUT_EXPIRED, settle_payment

    if not _session_matches(payment, session):
        logger.warning('payments.stripe_session_mismatch payment=%s', payment.pk)
        return payment, False
    if event_type == COMPLETED and session.get('status') == 'complete' and session.get('payment_status') == 'paid':
        return settle_payment(payment.pk, approved=True)
    if event_type == EXPIRED and session.get('status') == 'expired' and session.get('payment_status') == 'unpaid':
        return settle_payment(payment.pk, approved=False, failure_reason=CHECKOUT_EXPIRED, notify=False)
    return payment, False


def reconcile_expired_checkout(payment):
    """Ask Stripe before rejecting a locally expired session whose webhook may be in flight."""
    from .payments import CheckoutFailed

    try:
        session = _stripe_request(_expire, payment.provider_reference)
        event_type = EXPIRED if session.get('status') == 'expired' else COMPLETED
        result, changed = _apply_session(payment, session, event_type)
    except (stripe.StripeError, TimeoutError) as exc:
        raise CheckoutFailed() from exc
    if result.status == payment.Status.PENDING:
        raise CheckoutFailed()
    return result, changed and result.status == payment.Status.REJECTED


def process_checkout_event(event):
    """Apply a verified event. Return False when Stripe should retry delivery."""
    from apps.users.models import Workspace

    from ..models import Payment

    event_type = event.get('type')
    if event_type not in (COMPLETED, EXPIRED):
        return True
    session = (event.get('data') or {}).get('object')
    if not session or (session.get('metadata') or {}).get('purchase_kind') != PURCHASE_KIND:
        return True
    try:
        payment_id = UUID(session['client_reference_id'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return True
    payment = Payment.objects.filter(pk=payment_id, provider=STRIPE).first()
    if payment is not None:
        if payment.status == Payment.Status.PENDING and not payment.provider_reference:
            return False  # open_checkout is still storing the session.
        if payment.provider_reference:
            try:
                _apply_session(payment, session, event_type)
                return True
            except (Payment.DoesNotExist, Workspace.DoesNotExist):
                pass
    if event_type == COMPLETED and session.get('payment_status') == 'paid':
        # A signed event can arrive after the workspace and payment were
        # removed. Its event id lets staff reconcile it in Stripe.
        logger.warning(
            'payments.stripe_unmatched_paid payment=%s session=%s event=%s',
            payment_id, session.get('id'), event.get('id'),
        )
    return True
