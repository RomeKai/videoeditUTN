"""
GET /api/v1/subscription/ and POST /api/v1/subscription/change/
(views_subscription.py, services/subscriptions.py): up applies when the
payment is confirmed, down waits for the end of the paid period and never
leaves more seats in use than the plan has, the current plan cancels what is
scheduled, and the beat task applies what is due once.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.payments.models import Payment, SubscriptionPlan
from apps.payments.services.plan_periods import add_month
from apps.payments.services.payments import settle_payment
from apps.payments.services.subscriptions import apply_due_plan_changes
from apps.payments.tasks import apply_due_plan_changes as apply_due_plan_changes_task
from apps.users.models import Workspace, WorkspaceMember

from .billing_helpers import FRONT, NO_PROVIDER, SIMULATED, client_for, join, make_user, owner_with_workspace, set_plan

pytestmark = pytest.mark.django_db

CHANGE = '/api/v1/subscription/change/'
SUBSCRIPTION = '/api/v1/subscription/'


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


# --- GET ---------------------------------------------------------------------------

def test_a_new_workspace_is_on_the_free_plan_with_nothing_to_renew(owner, plans):
    user, workspace = owner

    body = client_for(user).get(SUBSCRIPTION).json()

    assert body['plan']['id'] == str(plans['Gratis'].pk)
    assert body['plan']['name'] == 'Gratis'
    assert body['status'] == 'active'
    assert body['renews_at'] is None
    assert body['pending_plan'] is None
    assert body['pending_payment'] is None


@override_settings(**SIMULATED)
def test_a_plan_payment_waiting_for_its_checkout_is_shown_without_its_url(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    payment = Payment.objects.get(workspace=workspace, status='pending')

    body = client_for(user).get(SUBSCRIPTION).json()

    # Planes says a payment is waiting and offers to keep the current plan; every role reads this,
    # so the checkout URL is not in it. It expires when its checkout does.
    expires_at = body['pending_payment'].pop('expires_at')
    assert body['pending_payment'] == {'id': str(payment.pk), 'plan_id': str(plans['Pro'].pk), 'plan_name': 'Pro'}
    assert parse_datetime(expires_at) == payment.checkout_started_at + timedelta(seconds=settings.PAYMENTS_CHECKOUT_TTL_SECONDS)
    assert body['plan']['name'] == 'Gratis'


@override_settings(**SIMULATED, PAYMENTS_CHECKOUT_TTL_SECONDS=90 * 60)
def test_the_pending_payment_expires_with_the_checkout_lifetime_of_the_setting(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    payment = Payment.objects.get(workspace=workspace, status='pending')

    expires_at = client_for(user).get(SUBSCRIPTION).json()['pending_payment']['expires_at']

    assert parse_datetime(expires_at) == payment.checkout_started_at + timedelta(minutes=90)


@override_settings(**SIMULATED, PAYMENTS_PROVIDER_TIMEOUT_SECONDS=30)
def test_a_plan_payment_whose_checkout_is_still_opening_has_no_expiry_yet(owner, plans):
    user, workspace = owner
    opening = Payment.objects.create(workspace=workspace, plan=plans['Pro'], amount_usd=Decimal('49.00'), provider='simulated')

    body = client_for(user).get(SUBSCRIPTION).json()

    assert body['pending_payment'] == {
        'id': str(opening.pk), 'plan_id': str(plans['Pro'].pk), 'plan_name': 'Pro', 'expires_at': None,
    }


@override_settings(**SIMULATED, PAYMENTS_PROVIDER_TIMEOUT_SECONDS=30)
def test_a_plan_payment_whose_provider_call_was_lost_is_not_shown(owner, plans):
    user, workspace = owner
    lost = Payment.objects.create(workspace=workspace, plan=plans['Pro'], amount_usd=Decimal('49.00'), provider='simulated')
    old = timezone.now() - timedelta(seconds=120)
    Payment.objects.filter(pk=lost.pk).update(created_at=old, checkout_started_at=old)

    # Still pending until the beat sweeps it, but «Seguir con» would find nothing to cancel (400).
    assert client_for(user).get(SUBSCRIPTION).json()['pending_payment'] is None
    assert change(client_for(user), plans['Gratis']).status_code == 400


@override_settings(**SIMULATED)
def test_the_plan_payment_of_another_workspace_is_not_shown(owner, plans):
    user, _ = owner
    other, _ = owner_with_workspace('otra')
    assert change(client_for(other), plans['Pro']).json()['checkout_url']

    assert client_for(user).get(SUBSCRIPTION).json()['pending_payment'] is None


@override_settings(**SIMULATED)
def test_a_pending_token_purchase_is_not_a_pending_plan_payment(owner, plans):
    user, workspace = owner
    purchase = client_for(user).post(
        '/api/v1/token-purchases/', {'package_id': 'pack-1000', 'return_url': f'{FRONT}/uso/comprar/resultado'}, format='json',
    )
    assert purchase.status_code == 201 and purchase.json()['status'] == 'pending'

    response = client_for(user).get(SUBSCRIPTION)

    assert response.status_code == 200
    assert response.json()['pending_payment'] is None


@override_settings(**SIMULATED)
def test_a_viewer_reads_the_pending_payment_without_its_checkout_url(owner, plans):
    user, workspace = owner
    checkout_url = change(client_for(user), plans['Pro']).json()['checkout_url']
    payment = Payment.objects.get(workspace=workspace, status='pending')
    viewer = make_user('lectora')
    join(workspace, viewer, role=WorkspaceMember.Role.VIEWER)

    response = client_for(viewer, workspace).get(SUBSCRIPTION)

    assert response.status_code == 200
    assert set(response.json()['pending_payment']) == {'id', 'plan_id', 'plan_name', 'expires_at'}
    assert response.json()['pending_payment']['id'] == str(payment.pk)
    assert checkout_url and payment.checkout_url
    assert payment.checkout_url not in response.content.decode()
    assert 'checkout_url' not in response.content.decode()


@override_settings(**SIMULATED)
def test_keeping_the_current_plan_cancels_the_payment_it_showed(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])

    response = change(client_for(user), plans['Gratis'])

    assert response.status_code == 200
    assert response.json()['subscription']['pending_payment'] is None
    assert not Payment.objects.filter(workspace=workspace, status='pending').exists()


@override_settings(**SIMULATED)
def test_a_plan_payment_whose_checkout_expired_is_not_shown(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    Payment.objects.filter(workspace=workspace).update(checkout_started_at=timezone.now() - timedelta(days=2))

    assert client_for(user).get(SUBSCRIPTION).json()['pending_payment'] is None


@pytest.mark.parametrize('role', [WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER])
def test_every_role_reads_the_subscription(owner, plans, role):
    _, workspace = owner
    set_plan(workspace, plans['Pro'])
    member = make_user('miembro')
    join(workspace, member, role=role)

    response = client_for(member, workspace).get(SUBSCRIPTION)

    assert response.status_code == 200
    assert response.json()['plan']['name'] == 'Pro'


def test_a_workspace_without_a_plan_shows_the_default_one(owner, plans):
    user, workspace = owner
    Workspace.objects.filter(pk=workspace.pk).update(subscription_plan=None)

    assert client_for(user).get(SUBSCRIPTION).json()['plan']['name'] == 'Gratis'


def test_a_paid_period_that_ended_is_not_shown_as_a_renewal(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() - timedelta(days=1))

    assert client_for(user).get(SUBSCRIPTION).json()['renews_at'] is None


# --- roles ---------------------------------------------------------------------------

@pytest.mark.parametrize('role', [WorkspaceMember.Role.EDITOR, WorkspaceMember.Role.VIEWER])
def test_only_owner_and_admin_change_the_plan(owner, plans, role):
    _, workspace = owner
    set_plan(workspace, plans['Pro'])
    member = make_user('miembro')
    join(workspace, member, role=role)

    response = change(client_for(member, workspace), plans['Creador'])

    assert response.status_code == 403
    assert response.json()['detail'].startswith('Solo el dueño o un admin')
    assert reload(workspace).pending_plan_id is None


@override_settings(**SIMULATED)
def test_an_admin_changes_the_plan(owner, plans):
    _, workspace = owner
    set_plan(workspace, plans['Pro'])
    admin = make_user('admin')
    join(workspace, admin, role=WorkspaceMember.Role.ADMIN)

    response = change(client_for(admin, workspace), plans['Estudio'])

    assert response.status_code == 200
    assert Payment.objects.get(workspace=workspace).created_by == admin


# --- up ------------------------------------------------------------------------------

@override_settings(**SIMULATED)
def test_going_up_to_a_paid_plan_opens_the_checkout_and_changes_nothing_yet(owner, plans):
    user, workspace = owner

    response = change(client_for(user), plans['Creador'], return_url=f'{FRONT}/uso/planes')

    assert response.status_code == 200
    body = response.json()
    payment = Payment.objects.get(workspace=workspace)
    assert body['applied'] == 'now'
    assert body['checkout_url'] == f'{FRONT}/dev/checkout?compra={payment.pk}&volver=http%3A%2F%2Flocalhost%3A3000%2Fuso%2Fplanes'
    assert body['subscription']['plan']['name'] == 'Gratis'
    assert (payment.status, payment.plan, payment.tokens, payment.amount_usd) == ('pending', plans['Creador'], 0, Decimal('19.00'))
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_without_return_url_the_checkout_comes_back_to_planes(owner, plans):
    user, _ = owner

    body = change(client_for(user), plans['Creador']).json()

    assert body['checkout_url'].endswith('volver=http%3A%2F%2Flocalhost%3A3000%2Fuso%2Fplanes')


@override_settings(**NO_PROVIDER)
def test_without_a_payments_provider_a_paid_upgrade_is_refused_and_nothing_changes(owner, plans):
    user, workspace = owner

    response = change(client_for(user), plans['Pro'])

    assert response.status_code == 501
    assert response.json()['code'] == 'payments_unavailable'
    assert not Payment.objects.exists()
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(PAYMENTS_PROVIDER='simulated', PAYMENTS_ALLOW_SIMULATED=False, DEBUG=False)
def test_the_simulated_provider_is_off_without_debug_or_the_demo_flag(owner, plans):
    user, workspace = owner

    response = change(client_for(user), plans['Pro'])

    assert response.status_code == 501
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_the_confirmed_payment_changes_the_plan_and_starts_a_paid_month(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    payment = Payment.objects.get(workspace=workspace)

    before = timezone.now()
    settle_payment(payment.pk, approved=True)

    workspace = reload(workspace)
    assert workspace.subscription_plan == plans['Pro']
    assert add_month(before) <= workspace.plan_renews_at <= add_month(timezone.now())
    body = client_for(user).get(SUBSCRIPTION).json()
    assert body['plan']['name'] == 'Pro'
    assert body['renews_at'] is not None


@override_settings(**SIMULATED)
def test_an_upgrade_payment_changes_the_plan_once(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    payment = Payment.objects.get(workspace=workspace)
    settle_payment(payment.pk, approved=True)
    # The plan moves on afterwards (an admin, a downgrade)...
    set_plan(workspace, plans['Creador'])

    # ...and the same confirmation arriving again changes nothing.
    again, changed = settle_payment(payment.pk, approved=True)

    assert changed is False
    assert again.status == 'approved'
    assert reload(workspace).subscription_plan == plans['Creador']


@override_settings(**SIMULATED)
def test_a_rejected_upgrade_payment_leaves_the_plan(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Pro'])
    payment = Payment.objects.get(workspace=workspace)

    settle_payment(payment.pk, approved=False, failure_reason='El banco rechazó el pago.')

    assert reload(workspace).subscription_plan == plans['Gratis']
    payment.refresh_from_db()
    assert (payment.status, payment.failure_reason, payment.checkout_url) == ('rejected', 'El banco rechazó el pago.', '')
    # Rejected is final: an approval after it changes nothing.
    assert settle_payment(payment.pk, approved=True)[1] is False
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_asking_again_for_the_same_upgrade_reuses_its_checkout(owner, plans):
    user, workspace = owner
    first = change(client_for(user), plans['Pro']).json()['checkout_url']

    second = change(client_for(user), plans['Pro']).json()['checkout_url']

    assert second == first
    assert Payment.objects.filter(workspace=workspace).count() == 1


@override_settings(**SIMULATED)
def test_choosing_another_plan_rejects_the_unpaid_upgrade(owner, plans):
    user, workspace = owner
    change(client_for(user), plans['Estudio'])
    old = Payment.objects.get(workspace=workspace)

    change(client_for(user), plans['Pro'])

    old.refresh_from_db()
    assert old.status == 'rejected'
    assert old.failure_reason == 'Elegiste otro plan antes de pagar este.'
    new = Payment.objects.get(workspace=workspace, status='pending')
    assert new.plan == plans['Pro']
    # The old checkout, paid late, changes nothing.
    settle_payment(old.pk, approved=True)
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_a_confirmed_upgrade_drops_the_scheduled_downgrade(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=10))
    change(client_for(user), plans['Gratis'])
    assert reload(workspace).pending_plan == plans['Gratis']

    change(client_for(user), plans['Estudio'])
    settle_payment(Payment.objects.get(workspace=workspace).pk, approved=True)

    workspace = reload(workspace)
    assert workspace.subscription_plan == plans['Estudio']
    assert workspace.pending_plan is None
    assert workspace.pending_plan_effective_at is None


# --- down ----------------------------------------------------------------------------

def test_going_down_to_the_free_plan_waits_for_the_end_of_the_paid_period(owner, plans):
    user, workspace = owner
    renews = timezone.now() + timedelta(days=12)
    set_plan(workspace, plans['Pro'], renews_at=renews)

    response = change(client_for(user), plans['Gratis'])

    assert response.status_code == 200
    body = response.json()
    assert body['applied'] == 'period_end'
    assert body['checkout_url'] is None
    assert body['subscription']['plan']['name'] == 'Pro'
    assert body['subscription']['pending_plan']['id'] == str(plans['Gratis'].pk)
    assert body['subscription']['pending_plan']['name'] == 'Gratis'
    assert datetime.fromisoformat(body['subscription']['pending_plan']['effective_at']) == renews
    workspace = reload(workspace)
    assert (workspace.subscription_plan, workspace.pending_plan, workspace.pending_plan_effective_at) == (plans['Pro'], plans['Gratis'], renews)
    assert not Payment.objects.exists()


def test_going_down_to_the_free_plan_without_a_paid_period_applies_at_once(owner, plans):
    user, workspace = owner
    # Put on Pro in the admin: nothing was paid, there is no period to wait for.
    set_plan(workspace, plans['Pro'])

    body = change(client_for(user), plans['Gratis']).json()

    assert body['applied'] == 'now'
    assert body['subscription']['plan']['name'] == 'Gratis'
    assert reload(workspace).subscription_plan == plans['Gratis']


@override_settings(**SIMULATED)
def test_going_down_to_a_cheaper_paid_plan_is_paid_and_applies_on_confirmation(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=12))

    body = change(client_for(user), plans['Creador']).json()

    assert (body['applied'], body['subscription']['pending_plan']) == ('now', None)
    assert body['checkout_url']
    assert reload(workspace).subscription_plan == plans['Pro']
    settle_payment(Payment.objects.get(workspace=workspace).pk, approved=True)
    assert reload(workspace).subscription_plan == plans['Creador']


@pytest.mark.parametrize('second', [WorkspaceMember.Status.ACTIVE, WorkspaceMember.Status.PENDING])
def test_going_down_with_more_seats_in_use_than_the_plan_is_a_409(owner, plans, second):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    join(workspace, make_user('uno'))
    # A pending invitation takes a seat like a member.
    join(workspace, make_user('dos'), status=second)

    response = change(client_for(user), plans['Gratis'])

    assert response.status_code == 409
    assert response.json() == {
        'error': 'Ocupás 3 lugares y el plan Gratis tiene 2. Quitá miembros o invitaciones antes de bajar.',
        'code': 'seats_exceeded',
        'seats_used': 3,
        'max_members': 2,
    }
    workspace = reload(workspace)
    assert (workspace.subscription_plan, workspace.pending_plan) == (plans['Pro'], None)


def test_going_down_with_exactly_the_seats_of_the_plan_is_allowed(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    join(workspace, make_user('uno'), status=WorkspaceMember.Status.PENDING)

    response = change(client_for(user), plans['Gratis'])

    assert response.status_code == 200
    assert reload(workspace).pending_plan == plans['Gratis']


# --- cancel --------------------------------------------------------------------------

def test_choosing_the_current_plan_cancels_the_scheduled_change(owner, plans):
    user, workspace = owner
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() + timedelta(days=5))
    change(client_for(user), plans['Gratis'])

    body = change(client_for(user), plans['Pro']).json()

    assert body['applied'] == 'now'
    assert body['subscription']['plan']['name'] == 'Pro'
    assert body['subscription']['pending_plan'] is None
    workspace = reload(workspace)
    assert (workspace.pending_plan, workspace.pending_plan_effective_at) == (None, None)


def test_choosing_the_current_plan_with_nothing_scheduled_is_a_400_with_its_code(owner, plans):
    user, _ = owner

    response = change(client_for(user), plans['Gratis'])

    assert response.status_code == 400
    # The web matches the code, never the text or the field.
    assert response.json() == {'error': 'Ya es tu plan actual.', 'code': 'nothing_to_cancel'}


def test_a_plan_that_is_not_offered_is_a_404(owner, plans):
    user, _ = owner
    hidden = plans['Pro']
    hidden.is_public = False
    hidden.save()

    response = change(client_for(user), hidden)

    assert response.status_code == 404
    assert response.json() == {'detail': 'El plan no existe.'}


def test_a_return_url_of_another_site_is_refused(owner, plans):
    user, _ = owner

    response = change(client_for(user), plans['Pro'], return_url='https://otro-sitio.example/uso/planes')

    assert response.status_code == 400
    assert response.json() == {'return_url': ['La dirección de vuelta tiene que ser una página de la app.']}


# --- the beat task -------------------------------------------------------------------

def schedule(workspace, plan, when):
    Workspace.objects.filter(pk=workspace.pk).update(pending_plan=plan, pending_plan_effective_at=when)


def test_the_task_applies_the_downgrades_that_are_due_once(plans):
    _, due = owner_with_workspace('vence')
    _, later = owner_with_workspace('despues')
    now = timezone.now()
    set_plan(due, plans['Pro'], renews_at=now - timedelta(minutes=1))
    schedule(due, plans['Gratis'], now - timedelta(minutes=1))
    set_plan(later, plans['Pro'], renews_at=now + timedelta(days=3))
    schedule(later, plans['Gratis'], now + timedelta(days=3))

    assert apply_due_plan_changes_task() == 1

    due, later = reload(due), reload(later)
    assert (due.subscription_plan, due.pending_plan, due.pending_plan_effective_at, due.plan_renews_at) == (plans['Gratis'], None, None, None)
    assert (later.subscription_plan, later.pending_plan) == (plans['Pro'], plans['Gratis'])
    # Nothing is left to apply: a second run (or a second beat) changes nothing.
    assert apply_due_plan_changes() == 0
    assert reload(due).subscription_plan == plans['Gratis']


def test_while_a_downgrade_waits_no_invitation_breaks_its_seats(plans):
    user, workspace = owner_with_workspace()
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() - timedelta(minutes=1))
    schedule(workspace, plans['Gratis'], timezone.now() - timedelta(minutes=1))
    join(reload(workspace), make_user('uno'))
    # Gratis has 2 seats: the owner and "uno". Pro would have room, the plan it becomes does not.
    with pytest.raises(ValidationError, match="Tu plan pasa a 'Gratis'"):
        join(reload(workspace), make_user('dos'))

    assert apply_due_plan_changes() == 1

    assert reload(workspace).subscription_plan == plans['Gratis']
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 2


def test_members_from_before_the_downgrade_keep_their_seats_if_the_plan_shrank(plans, caplog):
    user, workspace = owner_with_workspace()
    set_plan(workspace, plans['Pro'], renews_at=timezone.now() - timedelta(minutes=1))
    join(reload(workspace), make_user('uno'))
    schedule(workspace, plans['Gratis'], timezone.now() - timedelta(minutes=1))
    # Staff lowered the seats of the target plan while the downgrade waited.
    SubscriptionPlan.objects.filter(pk=plans['Gratis'].pk).update(max_members=1)

    assert apply_due_plan_changes() == 1

    assert reload(workspace).subscription_plan == plans['Gratis']
    assert WorkspaceMember.objects.filter(workspace=workspace).count() == 2
    assert f'subscription.over_member_limit workspace={workspace.pk} seats_used=2 max_members=1' in caplog.text


def test_a_scheduled_change_always_has_its_date(owner, plans):
    from django.db import IntegrityError, transaction

    _, workspace = owner
    with pytest.raises(IntegrityError), transaction.atomic():
        Workspace.objects.filter(pk=workspace.pk).update(pending_plan=plans['Creador'])


@pytest.mark.parametrize('start, expected', [
    (datetime(2026, 1, 31, 10, tzinfo=dt_timezone.utc), datetime(2026, 2, 28, 10, tzinfo=dt_timezone.utc)),
    (datetime(2028, 1, 31, 10, tzinfo=dt_timezone.utc), datetime(2028, 2, 29, 10, tzinfo=dt_timezone.utc)),
    (datetime(2026, 12, 15, 10, tzinfo=dt_timezone.utc), datetime(2027, 1, 15, 10, tzinfo=dt_timezone.utc)),
])
def test_a_paid_period_lasts_one_calendar_month(start, expected):
    assert add_month(start) == expected
