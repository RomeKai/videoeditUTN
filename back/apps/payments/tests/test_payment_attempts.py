"""
A payment attempt from start to checkout (services/payments.py, subscriptions.py,
views_purchases.py): a checkout is only handed out for a payment still pending,
an attempt in progress is never replaced, a failed one frees its key, the
simulator answers only who may use it, a late payment is logged for a refund,
and the workspace row is locked without blocking the rows that point to it.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.payments.models import Payment
from apps.payments.services.payment_providers import Checkout, SimulatedProvider
from apps.payments.services.payments import open_checkout, settle_payment
from apps.payments.services.subscriptions import change_plan
from apps.users.models import User, Workspace

from .billing_helpers import FRONT, SIMULATED, client_for, owner_with_workspace

pytestmark = pytest.mark.django_db

PURCHASES = '/api/v1/token-purchases/'
CHANGE = '/api/v1/subscription/change/'
RETURN = f'{FRONT}/uso/comprar/resultado'


@pytest.fixture
def owner(seeded_plans):
    return owner_with_workspace()


def buy(client, key=None, **body):
    extra = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
    return client.post(PURCHASES, {'return_url': RETURN, **body}, format='json', **extra)


# --- 2. a checkout only for a payment still pending ---------------------------------

@override_settings(**SIMULATED)
def test_a_payment_settled_before_its_checkout_opens_gets_no_checkout(owner):
    user, workspace = owner
    payment = Payment.objects.create(workspace=workspace, tokens=1000, amount_usd=Decimal('10.00'), provider='simulated')
    settle_payment(payment.pk, approved=False, failure_reason='Reemplazado.')

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        result = open_checkout(SimulatedProvider(), payment, RETURN)

    payment.refresh_from_db()
    assert (payment.status, payment.checkout_url) == ('rejected', '')
    assert result.status == 'rejected'
    assert cancel.call_count == 1


@override_settings(**SIMULATED)
def test_the_same_upgrade_while_its_checkout_is_opening_is_a_409_and_rejects_nothing(owner, seeded_plans):
    user, workspace = owner
    opening = Payment.objects.create(
        workspace=workspace, plan=seeded_plans['Pro'], amount_usd=Decimal('49.00'), provider='simulated',
    )

    response = client_for(user).post(CHANGE, {'plan_id': str(seeded_plans['Pro'].pk)}, format='json')

    assert response.status_code == 409
    assert response.json()['code'] == 'checkout_in_progress'
    opening.refresh_from_db()
    assert opening.status == 'pending'
    assert Payment.objects.filter(workspace=workspace).count() == 1


@pytest.mark.django_db(transaction=True)
@override_settings(**SIMULATED)
def test_a_double_submit_of_an_upgrade_never_hands_out_the_checkout_of_a_rejected_payment(seeded_plans):
    if connection.vendor != 'postgresql':
        pytest.skip('needs PostgreSQL row locks')
    user, workspace = owner_with_workspace()
    plan_id = seeded_plans['Pro'].pk
    first_opening = threading.Event()
    release = threading.Event()
    original = SimulatedProvider.start_checkout
    calls = []

    def slow_checkout(self, payment, return_url):
        calls.append(payment.pk)
        if len(calls) == 1:
            # The first submit stays inside the provider while the second one arrives.
            first_opening.set()
            release.wait(timeout=10)
        return original(self, payment, return_url)

    def submit():
        try:
            return change_plan(workspace.pk, plan_id, user=user, return_url=f'{FRONT}/uso/planes')
        except Exception as exc:  # the 409 of the second submit
            return exc
        finally:
            connection.close()

    with mock.patch.object(SimulatedProvider, 'start_checkout', slow_checkout):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit)
            assert first_opening.wait(timeout=10)
            second = pool.submit(submit).result(timeout=20)
            release.set()
            first = first.result(timeout=20)

    assert type(second).__name__ == 'CheckoutInProgress'
    assert first.checkout_url
    payment = Payment.objects.get(workspace=workspace)
    assert payment.status == 'pending'
    assert payment.checkout_url == first.checkout_url


# --- 6. a payment that arrives after its rejection ----------------------------------

def test_a_payment_confirmed_after_it_was_rejected_is_logged_for_a_refund(owner, caplog):
    _, workspace = owner
    payment = Payment.objects.create(workspace=workspace, tokens=1000, amount_usd=Decimal('10.00'), provider='simulated')
    settle_payment(payment.pk, approved=False, failure_reason='Reemplazado.')

    _, changed = settle_payment(payment.pk, approved=True)

    assert changed is False
    assert f'payment.late_paid payment={payment.pk} workspace={workspace.pk}' in caplog.text
    assert 'amount_usd=10.00' in caplog.text


def test_a_repeated_approval_is_not_a_late_payment(owner, caplog):
    _, workspace = owner
    payment = Payment.objects.create(workspace=workspace, tokens=1000, amount_usd=Decimal('10.00'), provider='simulated')
    settle_payment(payment.pk, approved=True)

    settle_payment(payment.pk, approved=True)

    assert 'payment.late_paid' not in caplog.text


# --- 7. who may use the simulator without DEBUG ---------------------------------------

@pytest.mark.parametrize('who, allowed', [('anyone', False), ('staff', True), ('listed', True)])
def test_without_debug_only_staff_or_listed_emails_simulate(owner, who, allowed):
    user, workspace = owner
    if who == 'staff':
        User.objects.filter(pk=user.pk).update(is_staff=True)
        user.refresh_from_db()
    listed = ['DUENA@example.com'] if who == 'listed' else []
    with override_settings(PAYMENTS_PROVIDER='simulated', PAYMENTS_ALLOW_SIMULATED=True, DEBUG=False, PAYMENTS_SIMULATE_ALLOWED_EMAILS=listed):
        payment_id = buy(client_for(user), package_id='pack-1000').json()['id']
        response = client_for(user).post(f'{PURCHASES}{payment_id}/simulate/', {'approved': True}, format='json')

    assert response.status_code == (200 if allowed else 404)
    assert Payment.objects.get().status == ('approved' if allowed else 'pending')


@override_settings(PAYMENTS_PROVIDER='simulated', PAYMENTS_ALLOW_SIMULATED=False, DEBUG=True, PAYMENTS_SIMULATE_ALLOWED_EMAILS=[])
def test_with_debug_anyone_simulates(owner):
    user, _ = owner
    payment_id = buy(client_for(user), package_id='pack-1000').json()['id']

    assert client_for(user).post(f'{PURCHASES}{payment_id}/simulate/', {'approved': True}, format='json').status_code == 200


# --- 10. idempotency ----------------------------------------------------------------

@override_settings(**SIMULATED)
def test_a_replay_while_the_first_purchase_opens_its_checkout_is_a_409(owner):
    user, workspace = owner
    Payment.objects.create(
        workspace=workspace, tokens=1000, amount_usd=Decimal('10.00'), package_id='pack-1000',
        provider='simulated', idempotency_key='envio-1',
    )

    response = buy(client_for(user), key='envio-1', package_id='pack-1000')

    assert response.status_code == 409
    assert response.json()['code'] == 'purchase_in_progress'
    assert Payment.objects.count() == 1


@override_settings(**SIMULATED)
def test_after_a_failed_checkout_the_same_key_starts_a_new_attempt(owner):
    user, workspace = owner
    with mock.patch.object(SimulatedProvider, 'start_checkout', side_effect=RuntimeError('provider down')):
        failed = buy(client_for(user), key='envio-1', package_id='pack-1000')

    retried = buy(client_for(user), key='envio-1', package_id='pack-1000')
    again = buy(client_for(user), key='envio-1', package_id='pack-1000')

    assert failed.status_code == 502
    assert retried.status_code == 201
    assert retried.json()['status'] == 'pending' and retried.json()['checkout_url']
    assert again.json()['id'] == retried.json()['id']
    assert sorted(Payment.objects.filter(workspace=workspace).values_list('status', flat=True)) == ['pending', 'rejected']


# --- 11. long URLs ------------------------------------------------------------------

@override_settings(**SIMULATED)
def test_a_return_url_over_600_characters_is_a_400(owner, seeded_plans):
    user, _ = owner
    long_url = f"{RETURN}?x={'a' * 600}"

    assert buy(client_for(user), package_id='pack-1000', return_url=long_url).status_code == 400
    assert client_for(user).post(CHANGE, {'plan_id': str(seeded_plans['Pro'].pk), 'return_url': long_url}, format='json').status_code == 400
    assert not Payment.objects.exists()


@override_settings(**SIMULATED)
def test_a_checkout_url_too_long_to_store_rejects_the_payment_instead_of_a_500(owner):
    user, _ = owner
    huge = Checkout(url=f"{FRONT}/dev/checkout?x={'a' * 2100}", reference='sim_huge')

    with mock.patch.object(SimulatedProvider, 'start_checkout', return_value=huge):
        response = buy(client_for(user), package_id='pack-1000')

    assert response.status_code == 502
    assert response.json()['code'] == 'checkout_failed'
    assert Payment.objects.get().status == 'rejected'


# --- 13. the workspace lock ----------------------------------------------------------

@override_settings(**SIMULATED)
def test_the_workspace_is_locked_for_no_key_update(owner, seeded_plans):
    user, workspace = owner
    payment = Payment.objects.create(workspace=workspace, tokens=1000, amount_usd=Decimal('10.00'), provider='simulated')

    with CaptureQueriesContext(connection) as settled:
        settle_payment(payment.pk, approved=True)
    with CaptureQueriesContext(connection) as changed:
        change_plan(workspace.pk, seeded_plans['Pro'].pk, user=user, return_url=f'{FRONT}/uso/planes')

    for queries in (settled, changed):
        locks = [query['sql'] for query in queries.captured_queries if 'FOR ' in query['sql'] and '"users_workspace"' in query['sql'].split('WHERE')[0]]
        assert locks and all('FOR NO KEY UPDATE' in sql for sql in locks), locks
    assert Workspace.objects.filter(pk=workspace.pk).exists()
