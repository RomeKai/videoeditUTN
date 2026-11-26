"""
The rules of a plan change that money depends on (services/subscriptions.py,
plan_periods.py, apps/users/seats.py):

- price, not the pricing page's order, decides what needs a payment;
- a scheduled downgrade keeps its seat promise until it applies;
- a paid period that ends without a renewal ends the paid plan;
- keeping the current plan cancels an upgrade waiting for its payment;
- the admin changing the plan drops what was scheduled;
- the paid period is counted in the workspace's time zone, with its anchor day.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib import admin
from django.core.checks import run_checks
from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.payments.models import Payment, SubscriptionPlan
from apps.payments.services.payment_providers import SimulatedProvider
from apps.payments.services.payments import settle_payment
from apps.payments.services.plan_periods import add_month
from apps.payments.services.subscriptions import apply_due_plan_changes
from apps.users.models import Workspace, WorkspaceMember

from .billing_helpers import SIMULATED, client_for, join, make_user, owner_with_workspace, set_plan

pytestmark = pytest.mark.django_db

CHANGE = '/api/v1/subscription/change/'


@pytest.fixture
def plans(seeded_plans):
    return seeded_plans


@pytest.fixture
def owner(plans):
    return owner_with_workspace()


def change(client, plan, **extra):
    return client.post(CHANGE, {'plan_id': str(plan.pk), **extra}, format='json')


def reload(workspace):
    return Workspace.objects.get(pk=workspace.pk)


def public_plan(name, price, sort_order, max_members=10):
    return SubscriptionPlan.objects.create(
        name=name, price_usd_monthly=Decimal(price), sort_order=sort_order, max_members=max_members,
        is_public=True, is_active=True,
    )


def schedule(workspace, plan, when):
    Workspace.objects.filter(pk=workspace.pk).update(pending_plan=plan, pending_plan_effective_at=when)


# --- 1. price decides ---------------------------------------------------------------

@override_settings(**SIMULATED)
def test_a_pricier_plan_earlier_in_the_page_still_needs_a_payment(owner, plans):
    user, workspace = owner
    # Listed before Gratis, but it costs money: it is not a free "downgrade".
    early = public_plan('Temprano', '99.00', sort_order=0)

    body = change(client_for(user), early).json()

    assert body['checkout_url']
    assert reload(workspace).subscription_plan == plans['Gratis']
    assert Payment.objects.get(workspace=workspace).amount_usd == Decimal('99.00')


@override_settings(**SIMULATED)
@pytest.mark.parametrize('price', ['5.00', '49.00'])
def test_any_other_paid_plan_is_a_purchase_that_applies_on_confirmation(owner, plans, price):
    user, workspace = owner
    # Cheaper, or the same price (a lateral move): paid all the same, and the rest of the period is lost.
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=9))
    other = public_plan('Otro', price, sort_order=99)

    body = change(client_for(user), other).json()

    assert body['checkout_url'] and body['applied'] == 'now'
    workspace = reload(workspace)
    assert (workspace.subscription_plan, workspace.pending_plan) == (plans['Pro'], None)
    payment = Payment.objects.get(workspace=workspace)
    assert payment.amount_usd == Decimal(price)

    before = timezone.now()
    settle_payment(payment.pk, approved=True)

    workspace = reload(workspace)
    assert workspace.subscription_plan == other
    assert add_month(before) <= workspace.plan_renews_at <= add_month(timezone.now())


@override_settings(**SIMULATED)
def test_paying_once_never_keeps_a_cheaper_paid_plan_for_good(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    settle_payment(Payment.objects.get(workspace=workspace).pk, approved=True)

    # Going to Creador is paid too: it is not scheduled for free at the end of the Pro period.
    body = change(client_for(user), plans['Creador']).json()

    assert body['checkout_url']
    assert reload(workspace).pending_plan is None
    assert Payment.objects.filter(workspace=workspace, status='pending', plan=plans['Creador']).exists()


@override_settings(**SIMULATED)
def test_a_lapsed_period_does_not_let_a_paid_plan_apply_unpaid(owner, plans):
    user, workspace = owner
    # The period ended and the beat has not dropped it yet.
    set_plan(workspace, plans['Estudio'], renews_at=timezone.now() - timedelta(minutes=5))

    body = change(client_for(user), plans['Pro']).json()

    assert body['checkout_url']
    assert reload(workspace).subscription_plan == plans['Estudio']


def test_only_the_free_plan_is_scheduled_for_the_end_of_the_period(owner, plans):
    user, workspace = owner
    renews = timezone.now() + timedelta(days=9)
    set_plan(workspace, plans['Pro'], renews_at=renews)

    body = change(client_for(user), plans['Gratis']).json()

    assert (body['applied'], body['checkout_url']) == ('period_end', None)
    workspace = reload(workspace)
    assert (workspace.pending_plan, workspace.pending_plan_effective_at) == (plans['Gratis'], renews)
    assert not Payment.objects.exists()


def test_a_check_warns_when_the_order_of_public_plans_does_not_follow_their_price(plans):
    assert 'payments.W002' not in [message.id for message in run_checks(databases=['default'])]

    public_plan('Temprano', '99.00', sort_order=0)

    assert 'payments.W002' in [message.id for message in run_checks(databases=['default'])]


# --- 3. the seat promise of a scheduled downgrade -----------------------------------

def test_while_a_downgrade_waits_nobody_joins_past_its_seats(owner, plans):
    _, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    schedule(workspace, plans['Gratis'], timezone.now() + timedelta(days=5))
    workspace = reload(workspace)
    join(workspace, make_user('uno'))

    with pytest.raises(ValidationError, match="Tu plan pasa a 'Gratis'.*2 miembros"):
        join(workspace, make_user('dos'), status=WorkspaceMember.Status.PENDING)
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 2

    # Read from the database, not from the instance: one loaded before the downgrade was scheduled says nothing.
    stale = Workspace.objects.get(pk=workspace.pk)
    Workspace.objects.filter(pk=workspace.pk).update(pending_plan=None, pending_plan_effective_at=None)
    schedule(workspace, plans['Gratis'], timezone.now() + timedelta(days=5))
    stale.pending_plan = None
    stale.pending_plan_effective_at = None
    with pytest.raises(ValidationError, match="Tu plan pasa a 'Gratis'"):
        join(stale, make_user('cuatro'))

    # Canceling the downgrade gives the seats of the plan back.
    client_for(workspace.owner).post(CHANGE, {'plan_id': str(plans['Pro'].pk)}, format='json')
    join(reload(workspace), make_user('tres'))
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 3


# --- 4. the end of a paid period ----------------------------------------------------

def test_a_paid_period_that_ends_unrenewed_drops_to_the_default_plan_once(plans):
    _, ended = owner_with_workspace('vencido')
    _, running = owner_with_workspace('vigente')
    _, assigned = owner_with_workspace('asignado')
    now = timezone.now()
    set_plan(ended, plans['Pro'], renews_at=now - timedelta(minutes=1))
    set_plan(running, plans['Pro'], renews_at=now + timedelta(days=3))
    # Put on a plan in the admin: no paid period, nothing ends.
    set_plan(assigned, plans['Estudio'])

    assert apply_due_plan_changes() == 1

    ended, running, assigned = reload(ended), reload(running), reload(assigned)
    assert (ended.subscription_plan, ended.plan_renews_at, ended.pending_plan) == (plans['Gratis'], None, None)
    assert running.subscription_plan == plans['Pro']
    assert assigned.subscription_plan == plans['Estudio']
    assert apply_due_plan_changes() == 0


def test_a_paid_period_that_ends_with_a_scheduled_downgrade_applies_the_downgrade(plans):
    _, workspace = owner_with_workspace()
    ended = timezone.now() - timedelta(minutes=1)
    set_plan(workspace, plans['Pro'], renews_at=ended)
    schedule(workspace, plans['Gratis'], ended)

    assert apply_due_plan_changes() == 1

    assert reload(workspace).subscription_plan == plans['Gratis']


def test_without_a_default_plan_an_ended_period_keeps_the_plan(plans, caplog):
    _, workspace = owner_with_workspace()
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() - timedelta(minutes=1))

    with override_settings(DEFAULT_PLAN_NAME='No existe'):
        assert apply_due_plan_changes() == 0

    assert reload(workspace).subscription_plan == plans['Pro']
    assert 'subscription.period_end_without_default' in caplog.text


def test_members_from_before_keep_their_seats_when_the_plan_drops(plans, caplog):
    _, workspace = owner_with_workspace()
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() - timedelta(minutes=1))
    workspace = reload(workspace)
    for name in ('uno', 'dos', 'tres'):
        join(workspace, make_user(name))

    assert apply_due_plan_changes() == 1

    assert reload(workspace).subscription_plan == plans['Gratis']
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 4
    assert 'subscription.over_member_limit' in caplog.text


# --- 8. keeping the current plan while an upgrade waits for its payment ------------

@override_settings(**SIMULATED)
def test_choosing_the_current_plan_cancels_the_upgrade_waiting_for_its_payment(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    waiting = Payment.objects.get(workspace=workspace)

    with mock.patch.object(SimulatedProvider, 'cancel_checkout') as cancel:
        response = change(client_for(user), plans['Gratis'])

    assert response.status_code == 200
    body = response.json()
    assert (body['applied'], body['checkout_url'], body['subscription']['plan']['name']) == ('now', None, 'Gratis')
    waiting.refresh_from_db()
    assert waiting.status == 'rejected'
    assert cancel.call_count == 1
    # Paid late, it changes nothing.
    settle_payment(waiting.pk, approved=True)
    assert reload(workspace).subscription_plan == plans['Gratis']


# --- 9. the admin -------------------------------------------------------------------

def test_changing_the_plan_in_the_admin_drops_the_scheduled_change(owner, plans):
    _, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    schedule(workspace, plans['Creador'], timezone.now() + timedelta(days=5))
    workspace = reload(workspace)
    workspace.subscription_plan = plans['Estudio']
    model_admin = admin.site._registry[Workspace]
    request = RequestFactory().post('/admin/')
    request.user = make_user('staff')

    model_admin.save_model(request, workspace, mock.Mock(changed_data=['subscription_plan']), change=True)

    workspace = reload(workspace)
    assert (workspace.subscription_plan, workspace.pending_plan, workspace.pending_plan_effective_at) == (plans['Estudio'], None, None)


def save_in_admin(workspace, changed):
    model_admin = admin.site._registry[Workspace]
    request = RequestFactory().post('/admin/')
    request.user = make_user('staff')
    model_admin.save_model(request, workspace, mock.Mock(changed_data=changed), change=True)
    return reload(workspace)


def test_a_plan_set_in_the_admin_is_a_comp_that_no_old_period_ends(owner, plans):
    _, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    Workspace.objects.filter(pk=workspace.pk).update(plan_anchor_day=21)
    workspace = reload(workspace)
    workspace.subscription_plan = plans['Estudio']

    workspace = save_in_admin(workspace, ['subscription_plan'])

    assert (workspace.subscription_plan, workspace.plan_renews_at, workspace.plan_anchor_day) == (plans['Estudio'], None, None)
    # When the old period would have ended, nothing drops it to the default plan.
    assert apply_due_plan_changes(now=timezone.now() + timedelta(days=6)) == 0
    assert reload(workspace).subscription_plan == plans['Estudio']


def test_a_renewal_date_set_in_the_same_admin_save_is_kept(owner, plans):
    _, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    Workspace.objects.filter(pk=workspace.pk).update(plan_anchor_day=21)
    workspace = reload(workspace)
    chosen = timezone.now() + timedelta(days=30)
    workspace.subscription_plan = plans['Estudio']
    workspace.plan_renews_at = chosen

    workspace = save_in_admin(workspace, ['subscription_plan', 'plan_renews_at'])

    assert (workspace.subscription_plan, workspace.plan_renews_at, workspace.plan_anchor_day) == (plans['Estudio'], chosen, 21)


# --- 12. the paid period, in the workspace's time zone ------------------------------

@override_settings(WORKSPACE_TIME_ZONE='America/Argentina/Buenos_Aires')
def test_a_month_is_counted_in_the_time_zone_of_the_workspace():
    # 02:00 UTC on 31 Jan is still 30 Jan (23:00) in Buenos Aires: the period ends on its 28 Feb, 23:00.
    start = datetime(2026, 1, 31, 2, tzinfo=dt_timezone.utc)

    assert add_month(start) == datetime(2026, 3, 1, 2, tzinfo=dt_timezone.utc)


@override_settings(WORKSPACE_TIME_ZONE='America/Argentina/Buenos_Aires')
def test_chained_periods_come_back_to_their_anchor_day():
    start = datetime(2026, 1, 31, 15, tzinfo=dt_timezone.utc)  # 12:00 on 31 Jan in Buenos Aires
    february = add_month(start, anchor_day=31)

    assert february == datetime(2026, 2, 28, 15, tzinfo=dt_timezone.utc)
    assert add_month(february, anchor_day=31) == datetime(2026, 3, 31, 15, tzinfo=dt_timezone.utc)


@override_settings(WORKSPACE_TIME_ZONE='America/Argentina/Buenos_Aires', **SIMULATED)
def test_a_confirmed_upgrade_keeps_the_anchor_day_of_its_period(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    payment = Payment.objects.get(workspace=workspace)
    confirmed = datetime(2026, 1, 31, 2, tzinfo=dt_timezone.utc)  # 30 Jan in Buenos Aires

    with mock.patch('apps.payments.services.payments.timezone.now', return_value=confirmed):
        settle_payment(payment.pk, approved=True)

    workspace = reload(workspace)
    assert workspace.plan_anchor_day == 30
    assert workspace.plan_renews_at == datetime(2026, 3, 1, 2, tzinfo=dt_timezone.utc)


# --- round 3 · 3. seats while a plan payment waits ------------------------------------

@override_settings(**SIMULATED)
def test_while_a_cheaper_plan_payment_waits_nobody_joins_past_its_seats(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=10))
    for name in ('m0', 'm1', 'm2'):
        join(reload(workspace), make_user(name))  # 4 seats; Creador has 5
    assert change(client_for(user), plans['Creador']).json()['checkout_url']

    join(reload(workspace), make_user('n0'), status=WorkspaceMember.Status.PENDING)
    with pytest.raises(ValidationError, match="Estás pasando al plan 'Creador'.*5 miembros"):
        join(reload(workspace), make_user('n1'), status=WorkspaceMember.Status.PENDING)

    settle_payment(Payment.objects.get(workspace=workspace, status='pending').pk, approved=True)
    workspace = reload(workspace)
    assert workspace.subscription_plan == plans['Creador']
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 5


@override_settings(**SIMULATED)
def test_a_confirmed_plan_with_fewer_seats_than_in_use_is_logged(owner, plans, caplog):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=10))
    for name in ('m0', 'm1', 'm2'):
        join(reload(workspace), make_user(name))
    change(client_for(user), plans['Creador'])
    # Staff lowered Creador's seats while the payment waited.
    SubscriptionPlan.objects.filter(pk=plans['Creador'].pk).update(max_members=2)

    settle_payment(Payment.objects.get(workspace=workspace, status='pending').pk, approved=True)

    assert reload(workspace).subscription_plan == plans['Creador']
    assert f'subscription.over_member_limit workspace={workspace.pk} seats_used=4 max_members=2' in caplog.text
