import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import stripe
from asgiref.sync import async_to_sync
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from apps.payments.services import stripe_service
from apps.payments.services.payment_providers import Checkout, PaymentsUnavailable, require_payment_provider
from apps.payments.views_stripe import StripeWebhookView


@pytest.mark.parametrize('product_id', ('', 'prod_example'))
@override_settings(PAYMENTS_CHECKOUT_TTL_SECONDS=86400)
def test_checkout_uses_server_amount_and_preserves_return_query(monkeypatch, product_id):
    payment = SimpleNamespace(
        pk=uuid4(), amount_usd=Decimal('9.99'), tokens=1000, plan_id=None,
        checkout_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    sessions = SimpleNamespace(create_async=AsyncMock(return_value=SimpleNamespace(
        id='cs_test_example', url='https://checkout.stripe.com/example',
    )))

    def stripe_request(operation, *args):
        return async_to_sync(operation)(sessions, *args)

    monkeypatch.setattr(stripe_service, '_stripe_request', stripe_request)
    with override_settings(STRIPE_TOKEN_PRODUCT_ID=product_id):
        checkout = stripe_service.StripeProvider().start_checkout(
            payment, 'https://app.example.com/uso/comprar/resultado?keep=1&keep=2&compra=old#notice',
        )

    assert checkout == Checkout(url='https://checkout.stripe.com/example', reference='cs_test_example')
    (params,), options = sessions.create_async.await_args
    price_data = params['line_items'][0]['price_data']
    product = {'product': product_id} if product_id else {'product_data': {'name': 'OneCreator tokens'}}
    assert price_data == {'unit_amount': 999, 'currency': 'usd', **product}
    assert params['payment_method_types'] == ['card']
    assert params['adaptive_pricing'] == {'enabled': False}
    assert params['client_reference_id'] == str(payment.pk)
    assert params['expires_at'] == int(payment.checkout_started_at.timestamp()) + 86400
    assert options == {'options': {'idempotency_key': f'onecreator-payment-{payment.pk}'}}
    success = urlsplit(params['success_url'])
    assert parse_qs(success.query) == {'keep': ['1', '2'], 'compra': [str(payment.pk)]}
    assert success.fragment == 'notice'
    assert params['cancel_url'] == 'https://app.example.com/uso/comprar?tokens=1000'


def test_expire_checks_a_session_stripe_could_not_close():
    closed = {'status': 'complete', 'payment_status': 'paid'}
    sessions = SimpleNamespace(
        expire_async=AsyncMock(side_effect=stripe.InvalidRequestError('already closed', None)),
        retrieve_async=AsyncMock(return_value=closed),
    )

    assert async_to_sync(stripe_service._expire)(sessions, 'cs_test_example') == closed
    sessions.retrieve_async.assert_awaited_once_with('cs_test_example')


@override_settings(
    STRIPE_SECRET_KEY='sk_test_example', STRIPE_TOKEN_PRODUCT_ID='prod_example',
    PAYMENTS_PROVIDER_TIMEOUT_SECONDS=30, PAYMENTS_CHECKOUT_TTL_SECONDS=86400,
)
def test_sdk_encodes_checkout_request_and_idempotency_header(monkeypatch):
    payment = SimpleNamespace(
        pk=uuid4(), amount_usd=Decimal('1.00'), tokens=100,
        checkout_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    def stripe_api(request):
        assert request.method == 'POST'
        assert request.url.path == '/v1/checkout/sessions'
        assert request.headers['Idempotency-Key'] == f'onecreator-payment-{payment.pk}'
        assert parse_qs(request.content.decode())['line_items[0][price_data][unit_amount]'] == ['100']
        return httpx.Response(200, json={
            'object': 'checkout.session', 'id': 'cs_test_example', 'url': 'https://checkout.stripe.com/example',
        })

    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(stripe_api))
    monkeypatch.setattr(httpx, 'AsyncClient', client)

    checkout = stripe_service.create_checkout_session(payment, 'https://app.example.com/uso/comprar/resultado')

    assert checkout.reference == 'cs_test_example'


@override_settings(
    PAYMENTS_PROVIDER='stripe', STRIPE_SECRET_KEY='test-key', STRIPE_WEBHOOK_SECRET='test-signing',
    PAYMENTS_CHECKOUT_TTL_SECONDS=86400, PAYMENTS_PROVIDER_TIMEOUT_SECONDS=30,
)
def test_stripe_provider_handles_token_purchases_only():
    assert require_payment_provider().name == 'stripe'
    with pytest.raises(PaymentsUnavailable):
        require_payment_provider(for_plan=True)


@pytest.mark.parametrize(('signature', 'live', 'expected'), [
    ('valid', False, 200),
    ('tampered', False, 400),
    ('missing', False, 400),
    ('valid', True, 400),
])
@override_settings(STRIPE_SECRET_KEY='sk_test_example', STRIPE_WEBHOOK_SECRET='example-signing-key')
def test_webhook_checks_the_signature_on_the_raw_body(monkeypatch, signature, live, expected):
    verified = []
    monkeypatch.setattr('apps.payments.views_stripe.process_checkout_event', lambda event: verified.append(event) or True)
    payload = json.dumps({'id': 'evt_test_example', 'object': 'event', 'livemode': live}).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(b'example-signing-key', timestamp.encode() + b'.' + payload, hashlib.sha256).hexdigest()
    headers = {} if signature == 'missing' else {'HTTP_STRIPE_SIGNATURE': f't={timestamp},v1={digest}'}
    if signature == 'tampered':
        payload += b' '
    request = APIRequestFactory().post('/api/v1/payments/webhook/', payload, content_type='application/json', **headers)

    response = StripeWebhookView.as_view()(request)

    assert response.status_code == expected
    assert [event.id for event in verified] == (['evt_test_example'] if expected == 200 else [])
