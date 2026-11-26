"""
GET /api/v1/token-packages/, POST /api/v1/token-purchases/,
GET /api/v1/token-purchases/<id>/ and the simulated provider's
POST /api/v1/token-purchases/<id>/simulate/ (views_purchases.py,
services/payments.py). The tokens are credited once, with one DEPOSIT.
"""
import uuid
from decimal import Decimal

import pytest
from django.test import override_settings

from apps.payments.models import Payment, Transaction, Wallet
from apps.users.models import WorkspaceMember

from .billing_helpers import FRONT, NO_PROVIDER, SIMULATED, client_for, join, make_user, owner_with_workspace, set_plan

pytestmark = pytest.mark.django_db

PURCHASES = '/api/v1/token-purchases/'
RETURN = f'{FRONT}/uso/comprar/resultado'


@pytest.fixture
def owner(seeded_plans):
    return owner_with_workspace()


def buy(client, **body):
    return client.post(PURCHASES, {'return_url': RETURN, **body}, format='json')


def simulate(client, payment_id, approved=True):
    return client.post(f'{PURCHASES}{payment_id}/simulate/', {'approved': approved}, format='json')


def balance(workspace):
    return Wallet.objects.get(workspace=workspace).available_balance


# --- packages ---------------------------------------------------------------------

def test_the_packages_their_prices_and_the_reference_conversion(owner):
    user, _ = owner

    body = client_for(user).get('/api/v1/token-packages/').json()

    assert body == {
        'packages': [
            {'id': 'pack-1000', 'tokens': 1000, 'price_usd': '10.00', 'discount_rate': '0.00'},
            {'id': 'pack-5000', 'tokens': 5000, 'price_usd': '45.00', 'discount_rate': '0.10'},
            {'id': 'pack-15000', 'tokens': 15000, 'price_usd': '120.00', 'discount_rate': '0.20'},
        ],
        'custom_price_per_token_usd': '0.01',
        'custom_min_tokens': 100,
        'local_currency': 'ARS',
        'usd_to_local_rate': '1450',
    }


@override_settings(TOKEN_PRICE_USD='0.02')
def test_the_prices_follow_the_reference_price_of_a_token(owner):
    user, _ = owner

    body = client_for(user).get('/api/v1/token-packages/').json()

    assert [item['price_usd'] for item in body['packages']] == ['20.00', '90.00', '240.00']
    assert body['custom_price_per_token_usd'] == '0.02'


def test_every_role_sees_the_packages(owner, seeded_plans):
    _, workspace = owner
    set_plan(workspace, seeded_plans['Pro'])
    viewer = make_user('lector')
    join(workspace, viewer, role=WorkspaceMember.Role.VIEWER)

    assert client_for(viewer, workspace).get('/api/v1/token-packages/').status_code == 200


# --- starting a purchase --------------------------------------------------------------

@override_settings(**SIMULATED)
def test_buying_a_package_answers_a_pending_purchase_with_its_checkout(owner):
    user, workspace = owner

    response = buy(client_for(user), package_id='pack-5000')

    assert response.status_code == 201
    payment = Payment.objects.get(workspace=workspace)
    assert response.json() == {
        'id': str(payment.pk),
        'status': 'pending',
        'tokens': 5000,
        'amount_usd': '45.00',
        'plan_id': None,
        'checkout_url': f'{FRONT}/dev/checkout?compra={payment.pk}&volver=http%3A%2F%2Flocalhost%3A3000%2Fuso%2Fcomprar%2Fresultado',
        'failure_reason': None,
        'created_at': response.json()['created_at'],
        'balance_after': None,
    }
    assert (payment.package_id, payment.provider, payment.created_by) == ('pack-5000', 'simulated', user)
    # Nothing is credited before the payment is confirmed.
    assert balance(workspace) == 0
    assert not Transaction.objects.filter(wallet__workspace=workspace).exists()


@override_settings(**SIMULATED)
def test_a_free_amount_pays_the_base_price(owner):
    user, _ = owner

    body = buy(client_for(user), custom_tokens=2500).json()

    assert (body['tokens'], body['amount_usd']) == (2500, '25.00')
    assert Payment.objects.get().package_id == ''


@override_settings(**SIMULATED)
@pytest.mark.parametrize('body, errors', [
    ({'package_id': 'pack-7'}, {'package_id': ['El paquete no existe.']}),
    ({'custom_tokens': 99}, {'custom_tokens': ['El mínimo es 100 tokens.']}),
    ({'custom_tokens': 1_000_001}, {'custom_tokens': ['El máximo es 1.000.000 tokens por compra.']}),
    ({'custom_tokens': 'mucho'}, {'custom_tokens': ['Escribí una cantidad de tokens, sin decimales.']}),
    ({}, {'non_field_errors': ['Elegí un paquete o escribí una cantidad de tokens.']}),
    ({'package_id': 'pack-1000', 'custom_tokens': 500}, {'non_field_errors': ['Elegí un paquete o una cantidad de tokens, no los dos.']}),
    ({'package_id': 'pack-1000', 'return_url': 'https://otro-sitio.example/vuelta'}, {'return_url': ['La dirección de vuelta tiene que ser una página de la app.']}),
])
def test_a_bad_purchase_is_a_400_and_starts_nothing(owner, body, errors):
    user, _ = owner

    response = buy(client_for(user), **body)

    assert response.status_code == 400
    assert response.json() == errors
    assert not Payment.objects.exists()


@override_settings(**SIMULATED)
@pytest.mark.parametrize('role', [WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER])
def test_only_owner_and_admin_buy_and_follow_a_purchase(owner, seeded_plans, role):
    user, workspace = owner
    set_plan(workspace, seeded_plans['Pro'])
    payment_id = buy(client_for(user), package_id='pack-1000').json()['id']
    member = make_user('miembro')
    join(workspace, member, role=role)
    client = client_for(member, workspace)

    assert buy(client, package_id='pack-1000').status_code == 403
    assert client.get(f'{PURCHASES}{payment_id}/').status_code == 403
    assert simulate(client, payment_id).status_code == 403
    assert Payment.objects.count() == 1
    assert Payment.objects.get().status == 'pending'


@override_settings(**NO_PROVIDER)
def test_without_a_payments_provider_nothing_is_started(owner):
    user, _ = owner

    response = buy(client_for(user), package_id='pack-1000')

    assert response.status_code == 501
    assert response.json() == {
        'error': 'Los pagos todavía no están habilitados.',
        'code': 'payments_unavailable',
    }
    assert not Payment.objects.exists()


@override_settings(**SIMULATED)
def test_the_same_idempotency_key_answers_the_same_purchase(owner):
    user, workspace = owner
    client = client_for(user)

    first = client.post(PURCHASES, {'package_id': 'pack-1000', 'return_url': RETURN}, format='json', HTTP_IDEMPOTENCY_KEY='envio-1')
    again = client.post(PURCHASES, {'package_id': 'pack-1000', 'return_url': RETURN}, format='json', HTTP_IDEMPOTENCY_KEY='envio-1')
    other = client.post(PURCHASES, {'package_id': 'pack-1000', 'return_url': RETURN}, format='json', HTTP_IDEMPOTENCY_KEY='envio-2')

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert other.json()['id'] != first.json()['id']
    assert Payment.objects.filter(workspace=workspace).count() == 2


@override_settings(**SIMULATED)
def test_a_key_reused_for_another_purchase_is_a_409(owner):
    user, _ = owner
    client = client_for(user)
    client.post(PURCHASES, {'package_id': 'pack-1000', 'return_url': RETURN}, format='json', HTTP_IDEMPOTENCY_KEY='envio-1')

    response = client.post(PURCHASES, {'custom_tokens': 700, 'return_url': RETURN}, format='json', HTTP_IDEMPOTENCY_KEY='envio-1')

    assert response.status_code == 409
    assert response.json()['code'] == 'idempotency_key_reused'
    assert Payment.objects.count() == 1


# --- following and settling it ------------------------------------------------------

@override_settings(**SIMULATED)
def test_approving_credits_the_tokens_once_with_one_deposit(owner):
    user, workspace = owner
    Wallet.objects.filter(workspace=workspace).update(available_balance=Decimal('120.5'))
    client = client_for(user)
    payment_id = buy(client, package_id='pack-1000').json()['id']

    body = simulate(client, payment_id).json()
    again = simulate(client, payment_id).json()
    rejected_late = simulate(client, payment_id, approved=False).json()

    assert body['status'] == 'approved'
    assert Decimal(body['balance_after']) == Decimal('1120.5')
    assert body['checkout_url'] is None
    assert again == body == rejected_late
    assert balance(workspace) == Decimal('1120.5')
    deposit = Transaction.objects.get(wallet__workspace=workspace)
    assert (deposit.transaction_type, deposit.status, deposit.category, deposit.amount) == ('DEPOSIT', 'COMPLETED', 'purchase', Decimal('1000'))
    assert deposit.project is None
    assert (deposit.description, deposit.created_by) == ('Compra de 1.000 tokens', user)
    assert Payment.objects.get(pk=payment_id).credit_transaction == deposit
    assert client.get(f'{PURCHASES}{payment_id}/').json() == body


@override_settings(**SIMULATED)
def test_a_rejected_payment_credits_nothing(owner):
    user, workspace = owner
    client = client_for(user)
    payment_id = buy(client, custom_tokens=300).json()['id']

    body = simulate(client, payment_id, approved=False).json()

    assert body['status'] == 'rejected'
    assert body['failure_reason'] == 'El banco rechazó el pago. Probá con otra tarjeta.'
    assert (body['checkout_url'], body['balance_after']) == (None, None)
    assert balance(workspace) == 0
    assert not Transaction.objects.filter(wallet__workspace=workspace).exists()
    # Rejected is final.
    assert simulate(client, payment_id).json()['status'] == 'rejected'
    assert balance(workspace) == 0


@override_settings(**SIMULATED)
def test_a_payment_of_another_workspace_is_a_404(owner):
    user, _ = owner
    payment_id = buy(client_for(user), package_id='pack-1000').json()['id']
    stranger, _ = owner_with_workspace('otra')

    assert client_for(stranger).get(f'{PURCHASES}{payment_id}/').json() == {'detail': 'No encontramos ese pago.'}
    assert simulate(client_for(stranger), payment_id).status_code == 404
    assert client_for(user).get(f'{PURCHASES}{uuid.uuid4()}/').status_code == 404
    assert Payment.objects.get().status == 'pending'


@override_settings(**SIMULATED)
def test_following_an_upgrade_payment_shows_its_plan_and_no_tokens(owner, seeded_plans):
    user, _ = owner
    client = client_for(user)
    client.post('/api/v1/subscription/change/', {'plan_id': str(seeded_plans['Pro'].pk)}, format='json')
    payment = Payment.objects.get()

    body = client.get(f'{PURCHASES}{payment.pk}/').json()

    assert (body['plan_id'], body['tokens'], body['amount_usd'], body['status']) == (str(seeded_plans['Pro'].pk), 0, '49.00', 'pending')
    assert simulate(client, payment.pk).json()['status'] == 'approved'
    assert client.get('/api/v1/subscription/').json()['plan']['name'] == 'Pro'


# --- the simulator never runs where it is not allowed -----------------------------

@pytest.mark.parametrize('overrides', [
    NO_PROVIDER,
    {'PAYMENTS_PROVIDER': 'simulated', 'PAYMENTS_ALLOW_SIMULATED': False, 'DEBUG': False},
])
def test_without_the_simulated_provider_simulate_does_not_exist(owner, overrides):
    user, workspace = owner
    with override_settings(**SIMULATED):
        payment_id = buy(client_for(user), package_id='pack-1000').json()['id']

    with override_settings(**overrides):
        response = simulate(client_for(user), payment_id)

    assert response.status_code == 404
    assert Payment.objects.get().status == 'pending'
    assert balance(workspace) == 0


@override_settings(PAYMENTS_PROVIDER='simulated', PAYMENTS_ALLOW_SIMULATED=False, DEBUG=True)
def test_debug_is_enough_for_the_simulator(owner):
    user, workspace = owner
    payment_id = buy(client_for(user), package_id='pack-1000').json()['id']

    assert simulate(client_for(user), payment_id).json()['status'] == 'approved'
    assert balance(workspace) == 1000


@override_settings(**SIMULATED)
def test_the_simulator_never_settles_a_payment_of_another_provider(owner):
    user, workspace = owner
    payment = Payment.objects.create(workspace=workspace, created_by=user, tokens=1000, amount_usd=Decimal('10.00'), provider='stripe')

    response = simulate(client_for(user), payment.pk)

    assert response.status_code == 404
    payment.refresh_from_db()
    assert payment.status == 'pending'
    assert balance(workspace) == 0
