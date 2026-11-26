"""
The money invariants of services/payments.py, at the database:

- a payment is settled once, also when confirmations arrive at the same time
  (row lock on the workspace and the payment, PostgreSQL only);
- an approved token purchase has exactly one DEPOSIT, and only it has one
  (check constraint + unique link);
- tokens or a plan, never both; at most one upgrade waiting for its payment
  per workspace.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from apps.payments.models import Payment, Transaction, Wallet
from apps.payments.services.payments import settle_payment
from apps.users.models import User, Workspace

from .billing_helpers import owner_with_workspace

pytestmark = pytest.mark.django_db


def token_purchase(workspace, tokens=1000, **fields):
    return Payment.objects.create(
        workspace=workspace, tokens=tokens, amount_usd=Decimal(tokens) / 100, provider='simulated', **fields,
    )


def settle_concurrently(payment_id, attempts=8):
    def attempt():
        try:
            return settle_payment(payment_id, approved=True)[1]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        return list(pool.map(lambda _: attempt(), range(attempts)))


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmations_credit_the_tokens_once(seeded_plans):
    if connection.vendor != 'postgresql':
        pytest.skip('SELECT FOR UPDATE needs PostgreSQL')
    _, workspace = owner_with_workspace()
    payment = token_purchase(workspace, tokens=1500)

    results = settle_concurrently(payment.pk)

    assert results.count(True) == 1
    assert Wallet.objects.get(workspace=workspace).available_balance == Decimal('1500')
    assert Transaction.objects.filter(wallet__workspace=workspace).count() == 1
    payment.refresh_from_db()
    assert (payment.status, payment.balance_after) == ('approved', Decimal('1500'))


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmations_of_an_upgrade_change_the_plan_once(seeded_plans):
    if connection.vendor != 'postgresql':
        pytest.skip('SELECT FOR UPDATE needs PostgreSQL')
    _, workspace = owner_with_workspace()
    payment = Payment.objects.create(
        workspace=workspace, plan=seeded_plans['Pro'], tokens=0, amount_usd=Decimal('49.00'), provider='simulated',
    )

    results = settle_concurrently(payment.pk)

    assert results.count(True) == 1
    workspace = Workspace.objects.get(pk=workspace.pk)
    assert workspace.subscription_plan == seeded_plans['Pro']
    assert not Transaction.objects.filter(wallet__workspace=workspace).exists()


def test_a_second_confirmation_writes_nothing(seeded_plans):
    _, workspace = owner_with_workspace()
    payment = token_purchase(workspace)
    settle_payment(payment.pk, approved=True)

    payment, changed = settle_payment(payment.pk, approved=True)

    assert changed is False
    assert Wallet.objects.get(workspace=workspace).available_balance == Decimal('1000')
    assert Transaction.objects.filter(wallet__workspace=workspace).count() == 1


def test_an_approved_token_purchase_cannot_exist_without_its_deposit(seeded_plans):
    _, workspace = owner_with_workspace()

    with pytest.raises(IntegrityError), transaction.atomic():
        token_purchase(workspace, status='approved')


def test_only_an_approved_token_purchase_has_a_deposit(seeded_plans):
    _, workspace = owner_with_workspace()
    deposit = Transaction.objects.create(
        wallet=workspace.wallet, amount=Decimal('1000'), transaction_type='DEPOSIT', status='COMPLETED',
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        token_purchase(workspace, credit_transaction=deposit)


def test_one_deposit_belongs_to_one_purchase(seeded_plans):
    _, workspace = owner_with_workspace()
    first = token_purchase(workspace)
    settle_payment(first.pk, approved=True)
    first.refresh_from_db()
    second = token_purchase(workspace)
    settle_payment(second.pk, approved=True)

    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.filter(pk=second.pk).update(credit_transaction=first.credit_transaction)


@pytest.mark.parametrize('fields', [
    {'tokens': 1000, 'plan': 'Pro'},
    {'tokens': 0, 'plan': None},
    {'tokens': 1000, 'plan': None, 'amount_usd': Decimal('0')},
])
def test_tokens_or_a_plan_and_a_positive_amount(seeded_plans, fields):
    _, workspace = owner_with_workspace()
    values = {'amount_usd': Decimal('10.00'), **fields}
    values['plan'] = seeded_plans[fields['plan']] if fields['plan'] else None

    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(workspace=workspace, provider='simulated', **values)


def test_one_upgrade_waits_for_its_payment_at_a_time(seeded_plans):
    _, workspace = owner_with_workspace()
    Payment.objects.create(workspace=workspace, plan=seeded_plans['Pro'], amount_usd=Decimal('49.00'), provider='simulated')

    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(workspace=workspace, plan=seeded_plans['Estudio'], amount_usd=Decimal('129.00'), provider='simulated')


def test_deleting_a_workspace_takes_its_payments_and_deposits(seeded_plans):
    owner, workspace = owner_with_workspace()
    payment = token_purchase(workspace)
    settle_payment(payment.pk, approved=True)

    workspace.delete()

    assert not Payment.objects.exists()
    assert not Transaction.objects.exists()
    assert User.objects.filter(pk=owner.pk).exists()


def test_the_deposit_of_a_purchase_is_not_deleted_on_its_own(seeded_plans):
    from django.db.models import RestrictedError

    _, workspace = owner_with_workspace()
    payment = token_purchase(workspace)
    settle_payment(payment.pk, approved=True)
    payment.refresh_from_db()

    with pytest.raises(RestrictedError):
        payment.credit_transaction.delete()
