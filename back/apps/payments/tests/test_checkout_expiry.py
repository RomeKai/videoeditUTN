"""
A checkout nobody pays expires (services/payments.py · expire_checkouts, run
by the beat task): PAYMENTS_CHECKOUT_TTL_SECONDS after its provider call
started, the payment is rejected and its checkout closed, decided again under
the locked rows. A confirmation after that is a late payment, never a second
settlement; an abandoned plan checkout stops capping the seats; and an expired
checkout is never handed out again.
"""
import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.payments.models import Payment
from apps.payments.services import payments as payments_service
from apps.payments.services.payment_providers import SimulatedProvider
from apps.payments.services.payments import settle_payment
from apps.payments.tasks import apply_due_plan_changes as beat_task
from apps.users.models import Workspace, WorkspaceMember

from .billing_helpers import FRONT, SIMULATED, client_for, join, make_user, owner_with_workspace, set_plan

pytestmark = pytest.mark.django_db

CHANGE = '/api/v1/subscription/change/'
PURCHASES = '/api/v1/token-purchases/'
DAY = 24 * 60 * 60
EXPIRED = 'El checkout venció sin que se pagara.'


@pytest.fixture
def owner(seeded_plans):
    return owner_with_workspace()


def open_plan_checkout(user, plan):
    response = client_for(user).post(CHANGE, {'plan_id': str(plan.pk)}, format='json')
    assert response.status_code == 200 and response.json()['checkout_url'], response.json()
    return Payment.objects.get(status='pending', plan=plan)


def opened_ago(payment, hours, created_hours=None):
    now = timezone.now()
    Payment.objects.filter(pk=payment.pk).update(
        checkout_started_at=now - timedelta(hours=hours),
        created_at=now - timedelta(hours=created_hours if created_hours is not None else hours),
    )


def reload(workspace):
    return Workspace.objects.get(pk=workspace.pk)


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
@pytest.mark.parametrize('hours, created_hours, expires', [
    (25, None, True),
    (1, None, False),
    # Measured from when its provider call started, not from when the row was created.
    (1, 30, False),
])
def test_the_beat_task_rejects_a_plan_checkout_only_past_its_lifetime(owner, seeded_plans, caplog, hours, created_hours, expires):
    caplog.set_level('INFO')
    user, workspace = owner
    payment = open_plan_checkout(user, seeded_plans['Pro'])
    opened_ago(payment, hours, created_hours)

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        beat_task()

    payment.refresh_from_db()
    assert reload(workspace).subscription_plan == seeded_plans['Gratis']
    if expires:
        assert (payment.status, payment.failure_reason, payment.checkout_url) == ('rejected', EXPIRED, '')
        # Closed at the provider with what the locked row says.
        assert cancel.call_count == 1
        assert cancel.call_args.args[0].provider_reference == payment.provider_reference != ''
        assert f'payments.checkout_expired payment={payment.pk}' in caplog.text
        assert 'expired_checkouts=1' in caplog.text
    else:
        assert payment.status == 'pending' and payment.checkout_url
        assert cancel.call_count == 0
        assert 'payments.checkout_expired' not in caplog.text


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
def test_an_abandoned_token_checkout_expires_too_and_keeps_its_key(owner):
    user, workspace = owner
    first = client_for(user).post(
        PURCHASES, {'package_id': 'pack-1000', 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json',
        HTTP_IDEMPOTENCY_KEY='envio-1',
    ).json()
    payment = Payment.objects.get(pk=first['id'])
    opened_ago(payment, 25)

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        beat_task()

    payment.refresh_from_db()
    assert (payment.status, payment.idempotency_key) == ('rejected', 'envio-1')
    assert cancel.call_count == 1
    assert workspace.wallet.available_balance == 0


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
def test_a_confirmation_after_the_expiry_is_a_late_payment(owner, seeded_plans, caplog):
    user, workspace = owner
    payment = open_plan_checkout(user, seeded_plans['Pro'])
    opened_ago(payment, 25)
    with mock.patch.object(SimulatedProvider, 'cancel_checkout'):
        beat_task()

    _, changed = settle_payment(payment.pk, approved=True)

    assert changed is False
    payment.refresh_from_db()
    assert payment.status == 'rejected'
    assert reload(workspace).subscription_plan == seeded_plans['Gratis']
    assert f'payment.late_paid payment={payment.pk} workspace={workspace.pk}' in caplog.text


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
def test_after_the_expiry_invitations_use_the_current_plans_seats_again(owner, seeded_plans):
    user, workspace = owner
    set_plan(workspace, seeded_plans['Pro'], renews_at=timezone.now() + timedelta(days=10))
    for name in ('m0', 'm1', 'm2'):
        join(reload(workspace), make_user(name))  # 4 seats; Creador has 5, Pro 10
    payment = open_plan_checkout(user, seeded_plans['Creador'])
    join(reload(workspace), make_user('n0'), status=WorkspaceMember.Status.PENDING)
    newcomer = make_user('n1')
    # The refusal says how to stop the payment from capping the seats.
    with pytest.raises(ValidationError, match=r"Estás pasando al plan 'Creador', que permite 5 miembros\. Para invitar a más gente, cancelá ese pago: elegí tu plan actual en Planes\."):
        join(reload(workspace), newcomer, status=WorkspaceMember.Status.PENDING)

    opened_ago(payment, 25)
    with mock.patch.object(SimulatedProvider, 'cancel_checkout'):
        beat_task()

    join(reload(workspace), newcomer, status=WorkspaceMember.Status.PENDING)
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 6
    assert reload(workspace).subscription_plan == seeded_plans['Pro']


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
def test_the_same_plan_past_its_checkout_lifetime_gets_a_new_checkout(owner, seeded_plans):
    """Before the beat runs: an expired checkout is never handed out again (paying it would be a late payment)."""
    user, workspace = owner
    old = open_plan_checkout(user, seeded_plans['Pro'])
    opened_ago(old, 25)

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        response = client_for(user).post(CHANGE, {'plan_id': str(seeded_plans['Pro'].pk)}, format='json')

    old.refresh_from_db()
    assert (old.status, old.failure_reason) == ('rejected', EXPIRED)
    assert cancel.call_count == 1 and cancel.call_args.args[0].pk == old.pk
    new = Payment.objects.get(workspace=workspace, status='pending')
    assert new.pk != old.pk
    assert response.json()['checkout_url'] == new.checkout_url


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
def test_a_replay_past_the_checkout_lifetime_answers_the_purchase_rejected(owner):
    user, _ = owner

    def buy():
        return client_for(user).post(
            PURCHASES, {'package_id': 'pack-1000', 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json',
            HTTP_IDEMPOTENCY_KEY='envio-1',
        )

    first = buy().json()
    opened_ago(Payment.objects.get(pk=first['id']), 25)

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        replay = buy()

    assert replay.status_code == 201
    assert (replay.json()['id'], replay.json()['status'], replay.json()['checkout_url']) == (first['id'], 'rejected', None)
    assert cancel.call_count == 1
    assert Payment.objects.count() == 1


# --- a confirmation racing the expiry ------------------------------------------------

def someone_waits_for_a_lock(timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'")
            if cursor.fetchone()[0]:
                return True
        time.sleep(0.05)
    return False


def in_thread(target, results, name):
    def run():
        try:
            results[name] = target()
        except Exception as exc:  # recorded for the assertions
            results[name] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


@pytest.mark.django_db(transaction=True)
@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=DAY)
@pytest.mark.parametrize('first', ['expiry', 'confirmation'])
def test_a_confirmation_racing_the_expiry_settles_the_payment_once(seeded_plans, caplog, first):
    if connection.vendor != 'postgresql':
        pytest.skip('needs PostgreSQL row locks')
    caplog.set_level('INFO')
    user, workspace = owner_with_workspace()
    payment = open_plan_checkout(user, seeded_plans['Pro'])
    opened_ago(payment, 25)
    holding = threading.Event()
    release = threading.Event()

    def hold(original):
        # Whoever goes first stops here with the workspace and payment rows locked.
        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            holding.set()
            release.wait(timeout=10)
            return result
        return wrapper

    if first == 'expiry':
        # The re-check of expire_checkout, under the locks.
        patch = mock.patch.object(payments_service, 'checkout_is_expired', hold(payments_service.checkout_is_expired))
    else:
        # The new plan's period, written by settle_payment under the locks.
        patch = mock.patch.object(payments_service, 'start_paid_period', hold(payments_service.start_paid_period))
    results = {}
    with patch, mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        expiry_first = first == 'expiry'
        leader = in_thread(beat_task if expiry_first else lambda: settle_payment(payment.pk, approved=True), results, first)
        assert holding.wait(timeout=10)
        follower = in_thread(
            (lambda: settle_payment(payment.pk, approved=True)) if expiry_first else beat_task,
            results, 'confirmation' if expiry_first else 'expiry',
        )
        # The other one waits for the workspace's row.
        assert someone_waits_for_a_lock()
        release.set()
        leader.join(timeout=30)
        follower.join(timeout=30)

    assert not any(isinstance(result, Exception) for result in results.values()), results
    payment.refresh_from_db()
    settled_lines = caplog.text.count(f'payments.settled payment={payment.pk}')
    late = f'payment.late_paid payment={payment.pk}' in caplog.text
    plan = reload(workspace).subscription_plan
    assert settled_lines == 1
    if expiry_first:
        # Rejected first: the confirmation changes nothing and is logged for a refund.
        assert (payment.status, payment.failure_reason) == ('rejected', EXPIRED)
        assert late and plan == seeded_plans['Gratis']
        assert cancel.call_count == 1
        assert results['confirmation'][1] is False
    else:
        # Paid first: the expiry finds it settled under the lock and leaves it.
        assert payment.status == 'approved'
        assert not late and plan == seeded_plans['Pro']
        assert cancel.call_count == 0
        assert 'payments.checkout_expired' not in caplog.text
        assert results['confirmation'][1] is True
