import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payments.models import Payment, Transaction, Wallet
from apps.payments.services import stripe_service
from apps.payments.services.payments import CHECKOUT_EXPIRED, expire_checkout
from apps.users.models import WorkspaceMember

from .billing_helpers import FRONT, client_for, join, make_user, owner_with_workspace

CHECKOUT = '/api/v1/payments/checkout/'
WEBHOOK = '/api/v1/payments/webhook/'
PURCHASES = '/api/v1/token-purchases/'
SESSION = 'cs_test_onecreator'
CHECKOUT_URL = 'https://checkout.stripe.com/example'
SIGNING_KEY = 'test-signing-secret'
STRIPE_SETTINGS = {
    'PAYMENTS_PROVIDER': 'stripe',
    'STRIPE_SECRET_KEY': 'sk_test_example',
    'STRIPE_WEBHOOK_SECRET': SIGNING_KEY,
}
pytestmark = pytest.mark.django_db


@pytest.fixture
def purchase(seeded_plans, settings, monkeypatch):
    for name, value in STRIPE_SETTINGS.items():
        setattr(settings, name, value)
    user, workspace = owner_with_workspace()
    client = client_for(user, workspace)
    calls = []

    def stripe_request(operation, *args):
        assert operation is stripe_service._create
        calls.append(args)
        return SimpleNamespace(id=SESSION, url=CHECKOUT_URL)

    monkeypatch.setattr(stripe_service, '_stripe_request', stripe_request)
    return client, workspace, calls


def buy(client, path=CHECKOUT):
    return client.post(
        path, {'return_url': f'{FRONT}/uso/comprar/resultado', 'package_id': 'pack-1000'},
        format='json', HTTP_IDEMPOTENCY_KEY='checkout-once',
    )


def session(payment, **fields):
    return {
        'id': SESSION, 'object': 'checkout.session', 'client_reference_id': str(payment.pk),
        'metadata': {'purchase_kind': stripe_service.PURCHASE_KIND}, 'mode': 'payment',
        'currency': 'usd', 'amount_total': 1000, 'payment_status': 'paid', 'status': 'complete',
        **fields,
    }


def deliver(obj, *, key=SIGNING_KEY, event_type=stripe_service.COMPLETED, live=False):
    payload = json.dumps({
        'id': 'evt_test_onecreator', 'object': 'event', 'livemode': live,
        'type': event_type, 'data': {'object': obj},
    }).encode()
    timestamp = int(time.time())
    signature = hmac.new(key.encode(), str(timestamp).encode() + b'.' + payload, hashlib.sha256).hexdigest()
    return APIClient(enforce_csrf_checks=True).post(
        WEBHOOK, payload, content_type='application/json', HTTP_STRIPE_SIGNATURE=f't={timestamp},v1={signature}',
    )


def test_checkout_requires_billing_permission_and_reuses_client_idempotency(purchase):
    client, workspace, calls = purchase
    editor = make_user('editor')
    join(workspace, editor, role=WorkspaceMember.Role.EDITOR)
    assert buy(APIClient()).status_code == 401
    assert buy(client_for(editor, workspace)).status_code == 403

    first = buy(client)
    repeat = buy(client, PURCHASES)
    assert first.status_code == repeat.status_code == 201
    assert first.json() == repeat.json()
    assert first.json()['checkout_url'] == CHECKOUT_URL
    payment = Payment.objects.get(workspace=workspace)
    assert (payment.provider, payment.provider_reference) == ('stripe', SESSION)
    assert len(calls) == 1
    assert Wallet.objects.get(workspace=workspace).available_balance == 0


def test_signed_event_delivered_twice_credits_one_deposit(purchase, settings):
    client, workspace, _ = purchase
    assert buy(client).status_code == 201
    payment = Payment.objects.get(workspace=workspace)
    settings.MAINTENANCE_MODE = True

    assert deliver(session(payment)).status_code == 200
    assert deliver(session(payment)).status_code == 200
    payment.refresh_from_db()

    assert payment.status == Payment.Status.APPROVED
    assert Wallet.objects.get(workspace=workspace).available_balance == 1000
    deposit = Transaction.objects.get(wallet__workspace=workspace, transaction_type=Transaction.Type.DEPOSIT)
    assert deposit.pk == payment.credit_transaction_id
    assert deposit.amount == Decimal('1000')


def test_unsigned_or_mismatched_events_do_not_credit(purchase):
    client, workspace, _ = purchase
    assert buy(client).status_code == 201
    payment = Payment.objects.get(workspace=workspace)

    assert APIClient().post(WEBHOOK, b'{}', content_type='application/json').status_code == 400
    assert deliver(session(payment), key='wrong-key').status_code == 400
    assert deliver(session(payment), live=True).status_code == 400
    for mismatch in (
        {'payment_status': 'unpaid'}, {'currency': 'ars'}, {'amount_total': 999},
        {'id': 'cs_test_other'}, {'mode': 'subscription'},
    ):
        assert deliver(session(payment, **mismatch)).status_code == 200
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PENDING
    assert not Transaction.objects.filter(wallet__workspace=workspace, transaction_type=Transaction.Type.DEPOSIT).exists()


def test_expired_webhook_closes_an_unpaid_checkout(purchase):
    client, workspace, _ = purchase
    assert buy(client).status_code == 201
    payment = Payment.objects.get(workspace=workspace)

    expired = session(payment, status='expired', payment_status='unpaid')
    assert deliver(expired, event_type=stripe_service.EXPIRED).status_code == 200
    payment.refresh_from_db()
    assert (payment.status, payment.failure_reason) == (Payment.Status.REJECTED, CHECKOUT_EXPIRED)
    assert Wallet.objects.get(workspace=workspace).available_balance == 0


def test_expiry_sweep_credits_payment_stripe_already_completed(purchase, monkeypatch):
    client, workspace, _ = purchase
    assert buy(client).status_code == 201
    payment = Payment.objects.get(workspace=workspace)
    monkeypatch.setattr(stripe_service, '_stripe_request', lambda operation, reference: session(payment))

    result, expired = expire_checkout(payment, now=timezone.now() + timedelta(days=2))

    assert (result.status, expired) == (Payment.Status.APPROVED, False)
    assert deliver(session(payment)).status_code == 200
    assert Wallet.objects.get(workspace=workspace).available_balance == 1000
    assert Transaction.objects.filter(wallet__workspace=workspace, transaction_type=Transaction.Type.DEPOSIT).count() == 1


def test_webhook_retries_while_the_session_is_being_stored(purchase):
    client, workspace, _ = purchase
    assert buy(client).status_code == 201
    payment = Payment.objects.get(workspace=workspace)
    Payment.objects.filter(pk=payment.pk).update(provider_reference='')

    assert deliver(session(payment)).status_code == 503
    assert Wallet.objects.get(workspace=workspace).available_balance == 0

    Payment.objects.filter(pk=payment.pk).update(provider_reference=SESSION)
    assert deliver(session(payment)).status_code == 200
    assert Wallet.objects.get(workspace=workspace).available_balance == 1000


def test_late_payment_for_a_deleted_workspace_leaves_a_reconciliation_reference(purchase, caplog):
    client, workspace, _ = purchase
    assert buy(client).status_code == 201
    payment = Payment.objects.get(workspace=workspace)
    expired = session(payment, status='expired', payment_status='unpaid')
    assert deliver(expired, event_type=stripe_service.EXPIRED).status_code == 200
    workspace.delete()

    assert deliver(session(payment)).status_code == 200
    assert not Payment.objects.filter(pk=payment.pk).exists()
    assert f'payments.stripe_unmatched_paid payment={payment.pk} session={SESSION}' in caplog.text
