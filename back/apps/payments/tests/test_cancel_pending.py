"""
«Seguir con …» in Planes (POST /api/v1/subscription/cancel-pending/,
services/subscriptions.py · cancel_pending_change) only ever cancels: it names
what the screen showed (the current plan, the plan payment, the scheduled
downgrade) and never changes the plan. When the workspace moved on meanwhile
(the payment approved in another tab, another payment started) it answers 409
with a ``code`` and changes nothing: no new payment, no scheduled downgrade.
A cancel racing the payment's approval ends in one state, whichever locks the
workspace row first.
"""
import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.payments.models import Payment
from apps.payments.services import payments as payments_service
from apps.payments.services import subscriptions as subscriptions_service
from apps.payments.services.payment_providers import SimulatedProvider
from apps.payments.services.payments import settle_payment
from apps.payments.services.subscriptions import KEPT, PlanChanged, cancel_pending_change
from apps.users.models import Workspace, WorkspaceMember

from .billing_helpers import FRONT, SIMULATED, client_for, join, make_user, owner_with_workspace, set_plan

pytestmark = pytest.mark.django_db

CHANGE = '/api/v1/subscription/change/'
CANCEL = '/api/v1/subscription/cancel-pending/'
SUBSCRIPTION = '/api/v1/subscription/'


@pytest.fixture
def plans(seeded_plans):
    return seeded_plans


@pytest.fixture
def owner(plans):
    return owner_with_workspace()


def reload(workspace):
    return Workspace.objects.get(pk=workspace.pk)


def start_plan_payment(user, plan):
    response = client_for(user).post(CHANGE, {'plan_id': str(plan.pk), 'return_url': f'{FRONT}/uso/planes'}, format='json')
    assert response.status_code == 200 and response.json()['checkout_url'], response.json()
    return Payment.objects.get(status='pending', plan=plan)


def schedule_downgrade(user, workspace, plans, current='Pro'):
    """A paid period on ``current`` and the downgrade to Gratis waiting for its end."""
    set_plan(workspace, plans[current], renews_at=timezone.now() + timedelta(days=5))
    response = client_for(user).post(CHANGE, {'plan_id': str(plans['Gratis'].pk)}, format='json')
    assert response.json()['applied'] == 'period_end', response.json()


def what_the_screen_shows(user):
    """«Seguir con …» sends what GET /subscription/ showed: the current plan and what it offered to cancel."""
    body = client_for(user).get(SUBSCRIPTION).json()
    sent = {'current_plan_id': body['plan']['id']}
    if body['pending_payment']:
        sent['payment_id'] = body['pending_payment']['id']
    if body['pending_plan']:
        sent['pending_plan_id'] = body['pending_plan']['id']
    return sent


def keep_current(user, sent):
    return client_for(user).post(CANCEL, sent, format='json')


def state(workspace):
    """Everything a cancel could change, to compare before and after."""
    workspace = reload(workspace)
    payments = sorted(Payment.objects.filter(workspace=workspace).values_list('pk', 'status', 'plan_id', 'failure_reason'))
    return {
        'plan': workspace.subscription_plan_id,
        'renews_at': workspace.plan_renews_at,
        'pending_plan': workspace.pending_plan_id,
        'pending_plan_effective_at': workspace.pending_plan_effective_at,
        'payments': payments,
    }


# --- a stale screen changes nothing -------------------------------------------------

@override_settings(**SIMULATED)
@pytest.mark.parametrize('shown_plan, paid_plan', [
    # The old «Seguir con Gratis» would have scheduled a downgrade to Gratis.
    ('Gratis', 'Pro'),
    # The old «Seguir con Creador» would have opened a new checkout for Creador.
    ('Creador', 'Pro'),
])
def test_after_the_payment_was_approved_elsewhere_keeping_the_old_plan_is_a_409_and_changes_nothing(owner, plans, shown_plan, paid_plan):
    user, workspace = owner
    if shown_plan != 'Gratis':
        set_plan(workspace, plans[shown_plan], renews_at=timezone.now() + timedelta(days=5))
    payment = start_plan_payment(user, plans[paid_plan])
    sent = what_the_screen_shows(user)
    assert sent == {'current_plan_id': str(plans[shown_plan].pk), 'payment_id': str(payment.pk)}
    # Paid in another tab (or the provider's webhook) while Planes still shows the payment.
    settle_payment(payment.pk, approved=True)
    before = state(workspace)

    with mock.patch.object(SimulatedProvider, 'start_checkout') as start, mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        response = keep_current(user, sent)

    assert response.status_code == 409
    assert response.json() == {
        'error': f'Tu plan cambió mientras tanto: ahora estás en el plan {paid_plan}. No cancelamos nada.',
        'code': 'plan_changed',
    }
    assert state(workspace) == before
    workspace = reload(workspace)
    assert workspace.subscription_plan == plans[paid_plan]
    assert (workspace.pending_plan, workspace.pending_plan_effective_at) == (None, None)
    assert list(Payment.objects.filter(workspace=workspace).values_list('status', flat=True)) == ['approved']
    assert (start.call_count, cancel.call_count) == (0, 0)


@override_settings(**SIMULATED)
def test_a_downgrade_the_screen_showed_from_another_plan_is_not_canceled(owner, plans):
    user, workspace = owner
    schedule_downgrade(user, workspace, plans, current='Pro')
    sent = what_the_screen_shows(user)
    # Elsewhere: Estudio is paid (it drops the downgrade) and Gratis is scheduled again, now from Estudio.
    estudio = start_plan_payment(user, plans['Estudio'])
    settle_payment(estudio.pk, approved=True)
    client_for(user).post(CHANGE, {'plan_id': str(plans['Gratis'].pk)}, format='json')
    assert reload(workspace).pending_plan == plans['Gratis']
    before = state(workspace)

    response = keep_current(user, sent)

    assert (response.status_code, response.json()['code']) == (409, 'plan_changed')
    assert state(workspace) == before


@override_settings(**SIMULATED)
def test_a_payment_the_screen_did_not_show_is_not_canceled(owner, plans):
    user, workspace = owner
    shown = start_plan_payment(user, plans['Pro'])
    sent = what_the_screen_shows(user)
    # Another tab picks Estudio: the Pro payment is replaced by a new one.
    other = start_plan_payment(user, plans['Estudio'])
    before = state(workspace)

    response = keep_current(user, sent)

    assert response.status_code == 409
    assert response.json()['code'] == 'pending_changed'
    assert state(workspace) == before
    other.refresh_from_db()
    shown.refresh_from_db()
    assert (other.status, shown.status) == ('pending', 'rejected')


@override_settings(**SIMULATED)
def test_a_payment_started_after_the_screen_showed_only_the_downgrade_is_not_canceled(owner, plans):
    user, workspace = owner
    schedule_downgrade(user, workspace, plans)
    sent = what_the_screen_shows(user)
    assert set(sent) == {'current_plan_id', 'pending_plan_id'}
    started = start_plan_payment(user, plans['Estudio'])
    before = state(workspace)

    response = keep_current(user, sent)

    assert (response.status_code, response.json()['code']) == (409, 'pending_changed')
    assert state(workspace) == before
    started.refresh_from_db()
    assert started.status == 'pending'


@override_settings(**SIMULATED)
def test_a_downgrade_scheduled_after_the_screen_showed_only_the_payment_is_not_canceled(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    shown = start_plan_payment(user, plans['Estudio'])
    sent = what_the_screen_shows(user)
    assert set(sent) == {'current_plan_id', 'payment_id'}
    # Another tab picks Gratis: it rejects the payment the screen shows and schedules the downgrade.
    response = client_for(user).post(CHANGE, {'plan_id': str(plans['Gratis'].pk)}, format='json')
    assert response.json()['applied'] == 'period_end', response.json()
    shown.refresh_from_db()
    assert shown.status == 'rejected'
    before = state(workspace)

    response = keep_current(user, sent)

    # Not «nothing to cancel» (the web would just refresh): a downgrade the screen never showed is waiting.
    assert (response.status_code, response.json()['code']) == (409, 'pending_changed')
    assert state(workspace) == before
    assert reload(workspace).pending_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_when_what_the_screen_showed_was_already_rejected_there_is_nothing_to_cancel(owner, plans):
    user, workspace = owner
    payment = start_plan_payment(user, plans['Pro'])
    sent = what_the_screen_shows(user)
    settle_payment(payment.pk, approved=False, failure_reason='El banco rechazó el pago.')
    before = state(workspace)

    response = keep_current(user, sent)

    assert response.status_code == 409
    assert response.json() == {'error': 'Ya no hay nada pendiente para cancelar.', 'code': 'nothing_to_cancel'}
    assert state(workspace) == before


def test_a_payment_of_another_workspace_is_nothing_to_cancel_and_stays_pending(owner, plans):
    user, workspace = owner
    _, other = owner_with_workspace('otra')
    theirs = Payment.objects.create(workspace=other, plan=plans['Pro'], amount_usd=plans['Pro'].price_usd_monthly, provider='simulated')

    response = keep_current(user, {'current_plan_id': str(plans['Gratis'].pk), 'payment_id': str(theirs.pk)})

    assert (response.status_code, response.json()['code']) == (409, 'nothing_to_cancel')
    theirs.refresh_from_db()
    assert theirs.status == 'pending'


# --- a normal cancel still works ----------------------------------------------------

@override_settings(**SIMULATED)
def test_keeping_the_plan_cancels_the_payment_the_screen_showed(owner, plans):
    user, workspace = owner
    payment = start_plan_payment(user, plans['Pro'])

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        response = keep_current(user, what_the_screen_shows(user))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'subscription'}
    assert body['subscription']['plan']['name'] == 'Gratis'
    assert (body['subscription']['pending_payment'], body['subscription']['pending_plan']) == (None, None)
    payment.refresh_from_db()
    assert (payment.status, payment.failure_reason) == ('rejected', KEPT)
    assert cancel.call_count == 1
    assert reload(workspace).subscription_plan == plans['Gratis']
    assert Payment.objects.filter(workspace=workspace).count() == 1
    # Paid late, it changes nothing.
    settle_payment(payment.pk, approved=True)
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_keeping_the_plan_cancels_the_scheduled_downgrade(owner, plans):
    user, workspace = owner
    schedule_downgrade(user, workspace, plans)
    renews_at = reload(workspace).plan_renews_at

    response = keep_current(user, what_the_screen_shows(user))

    assert response.status_code == 200
    assert response.json()['subscription']['pending_plan'] is None
    workspace = reload(workspace)
    assert (workspace.subscription_plan, workspace.plan_renews_at) == (plans['Pro'], renews_at)
    assert (workspace.pending_plan, workspace.pending_plan_effective_at) == (None, None)
    assert not Payment.objects.filter(workspace=workspace).exists()


@override_settings(**SIMULATED)
def test_keeping_the_plan_cancels_the_downgrade_and_the_payment_together(owner, plans):
    user, workspace = owner
    schedule_downgrade(user, workspace, plans)
    payment = start_plan_payment(user, plans['Estudio'])
    sent = what_the_screen_shows(user)
    assert set(sent) == {'current_plan_id', 'payment_id', 'pending_plan_id'}

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        response = keep_current(user, sent)

    assert response.status_code == 200
    subscription = response.json()['subscription']
    assert (subscription['plan']['name'], subscription['pending_plan'], subscription['pending_payment']) == ('Pro', None, None)
    payment.refresh_from_db()
    assert payment.status == 'rejected'
    assert cancel.call_count == 1
    assert reload(workspace).pending_plan is None


@override_settings(**SIMULATED)
def test_when_the_payment_it_showed_was_rejected_elsewhere_the_downgrade_it_showed_is_still_canceled(owner, plans):
    user, workspace = owner
    schedule_downgrade(user, workspace, plans)
    payment = start_plan_payment(user, plans['Estudio'])
    sent = what_the_screen_shows(user)
    settle_payment(payment.pk, approved=False, failure_reason='El banco rechazó el pago.')

    response = keep_current(user, sent)

    assert response.status_code == 200
    workspace = reload(workspace)
    assert (workspace.subscription_plan, workspace.pending_plan) == (plans['Pro'], None)
    payment.refresh_from_db()
    assert payment.failure_reason == 'El banco rechazó el pago.'


@override_settings(**SIMULATED)
def test_a_payment_whose_checkout_expired_unseen_does_not_block_the_cancel(owner, plans):
    user, workspace = owner
    schedule_downgrade(user, workspace, plans)
    sent = what_the_screen_shows(user)
    # Started elsewhere and abandoned: past its lifetime GET /subscription/ no longer shows it.
    expired = start_plan_payment(user, plans['Estudio'])
    Payment.objects.filter(pk=expired.pk).update(checkout_started_at=timezone.now() - timedelta(days=2))
    assert client_for(user).get(SUBSCRIPTION).json()['pending_payment'] is None

    response = keep_current(user, sent)

    assert response.status_code == 200
    assert reload(workspace).pending_plan is None


# --- the request ---------------------------------------------------------------------

def test_it_needs_something_to_cancel(owner, plans):
    user, _ = owner

    response = keep_current(user, {'current_plan_id': str(plans['Gratis'].pk)})

    assert response.status_code == 400
    assert response.json() == {'non_field_errors': ['Decí qué cancelar: el pago pendiente o el cambio programado.']}


def test_only_owner_and_admin_cancel(owner, plans):
    user, workspace = owner
    payment = Payment.objects.create(workspace=workspace, plan=plans['Pro'], amount_usd=plans['Pro'].price_usd_monthly, provider='simulated')
    editor = make_user('editora')
    join(workspace, editor, role=WorkspaceMember.Role.EDITOR)

    response = client_for(editor, workspace).post(CANCEL, {'current_plan_id': str(plans['Gratis'].pk), 'payment_id': str(payment.pk)}, format='json')

    assert response.status_code == 403
    payment.refresh_from_db()
    assert payment.status == 'pending'


# --- a cancel racing the payment's approval ------------------------------------------

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
@override_settings(**SIMULATED)
@pytest.mark.parametrize('first', ['cancel', 'approval'])
def test_a_cancel_racing_the_approval_ends_in_one_state(seeded_plans, caplog, first):
    if connection.vendor != 'postgresql':
        pytest.skip('needs PostgreSQL row locks')
    caplog.set_level('INFO')
    plans = seeded_plans
    user, workspace = owner_with_workspace()
    payment = start_plan_payment(user, plans['Pro'])
    sent = what_the_screen_shows(user)
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

    def cancel():
        return cancel_pending_change(
            workspace.pk, current_plan_id=plans['Gratis'].pk, payment_id=payment.pk, pending_plan_id=None,
        )

    def approve():
        return settle_payment(payment.pk, approved=True)

    if first == 'cancel':
        # The payment row, read and locked by the cancel after the workspace's.
        patch = mock.patch.object(subscriptions_service, '_pending_upgrade', hold(subscriptions_service._pending_upgrade))
    else:
        # The new plan's period, written by settle_payment under the locks.
        patch = mock.patch.object(payments_service, 'start_paid_period', hold(payments_service.start_paid_period))
    assert sent['payment_id'] == str(payment.pk)
    results = {}
    with patch, mock.patch.object(SimulatedProvider, 'cancel_checkout') as closed:
        leader = in_thread(cancel if first == 'cancel' else approve, results, first)
        assert holding.wait(timeout=10)
        follower_name = 'approval' if first == 'cancel' else 'cancel'
        follower = in_thread(approve if first == 'cancel' else cancel, results, follower_name)
        # The other one waits for the workspace's row.
        assert someone_waits_for_a_lock()
        release.set()
        leader.join(timeout=30)
        follower.join(timeout=30)

    payment.refresh_from_db()
    workspace = reload(workspace)
    assert caplog.text.count(f'payments.settled payment={payment.pk}') == 1
    # Never a second payment, never a scheduled change.
    assert Payment.objects.filter(workspace=workspace).count() == 1
    assert (workspace.pending_plan, workspace.pending_plan_effective_at) == (None, None)
    if first == 'cancel':
        # Canceled first: the approval changes nothing and is logged for a refund.
        assert results['cancel'].pk == workspace.pk
        assert (payment.status, payment.failure_reason) == ('rejected', KEPT)
        assert workspace.subscription_plan == plans['Gratis']
        assert results['approval'][1] is False
        assert f'payment.late_paid payment={payment.pk}' in caplog.text
        assert closed.call_count == 1
    else:
        # Paid first: the cancel finds another plan under the lock and leaves everything as it is.
        assert isinstance(results['cancel'], PlanChanged)
        assert results['approval'][1] is True
        assert payment.status == 'approved'
        assert workspace.subscription_plan == plans['Pro']
        assert workspace.plan_renews_at is not None
        assert 'payment.late_paid' not in caplog.text
        assert closed.call_count == 0
