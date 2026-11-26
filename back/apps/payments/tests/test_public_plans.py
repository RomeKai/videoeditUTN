"""
PAY-03 · GET /api/v1/plans/: planes activos y públicos, en orden, con cache de 1 h,
más el seed de los cuatro planes y el plan por defecto de un workspace nuevo.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.core.testing import clear_local_caches
from apps.payments.models import SubscriptionPlan, Wallet
from apps.users.models import User, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

URL = '/api/v1/plans/'

CONTRACT_FIELDS = {
    'id', 'name', 'max_video_duration_seconds', 'max_resolution', 'has_watermark',
    'allow_scheduling', 'allow_crossposting', 'has_seo_optimization',
    'has_thumbnail_engine', 'base_discount_rate', 'max_members', 'is_active',
    'price_usd_monthly', 'description', 'sort_order', 'is_recommended',
}


@pytest.fixture(autouse=True)
def clean_cache():
    # Only a LocMem cache: on Redis, clear() is a FLUSHDB (apps/core/testing.py).
    clear_local_caches()
    yield
    clear_local_caches()


@pytest.fixture
def client():
    return APIClient()


def names(response):
    return [plan['name'] for plan in response.json()]


def test_lists_the_four_seeded_plans_in_order_without_authentication(client):
    response = client.get(URL)

    assert response.status_code == 200
    assert names(response) == ['Gratis', 'Creador', 'Pro', 'Estudio']
    assert [plan['sort_order'] for plan in response.json()] == [1, 2, 3, 4]


def test_each_plan_carries_the_fields_of_the_contract(client):
    plan = client.get(URL).json()[0]

    assert set(plan) == CONTRACT_FIELDS
    assert plan['name'] == 'Gratis'
    assert plan['has_watermark'] is True
    assert Decimal(plan['price_usd_monthly']) == 0
    assert plan['max_resolution'] == '720p'


def test_only_one_seeded_plan_is_recommended(client):
    recommended = [plan['name'] for plan in client.get(URL).json() if plan['is_recommended']]

    assert recommended == ['Pro']


def test_inactive_plans_are_not_listed(client):
    SubscriptionPlan.objects.filter(name='Estudio').update(is_active=False)

    assert names(client.get(URL)) == ['Gratis', 'Creador', 'Pro']


def test_an_invalid_token_does_not_break_the_public_list(client):
    client.credentials(HTTP_AUTHORIZATION='Bearer expirado.o.roto')

    assert client.get(URL).status_code == 200


def test_only_get_is_allowed(client):
    assert client.post(URL, {}, format='json').status_code == 405


def test_the_second_request_is_served_from_cache(client, django_assert_num_queries):
    client.get(URL)

    with django_assert_num_queries(0):
        assert client.get(URL).status_code == 200


def test_editing_a_plan_refreshes_the_cached_list(client):
    client.get(URL)

    plan = SubscriptionPlan.objects.get(name='Pro')
    plan.description = 'Nueva descripción'
    plan.save()

    pro = next(item for item in client.get(URL).json() if item['name'] == 'Pro')
    assert pro['description'] == 'Nueva descripción'


def test_deactivating_a_plan_refreshes_the_cached_list(client):
    client.get(URL)

    plan = SubscriptionPlan.objects.get(name='Creador')
    plan.is_active = False
    plan.save()

    assert 'Creador' not in names(client.get(URL))


def test_deleting_a_plan_refreshes_the_cached_list(client):
    client.get(URL)

    SubscriptionPlan.objects.get(name='Estudio').delete()

    assert 'Estudio' not in names(client.get(URL))


def test_the_plans_endpoint_is_in_the_openapi_schema(client):
    schema = client.get('/api/schema/?format=json').json()

    assert '/api/v1/plans/' in schema['paths']


def test_seeding_the_plans_does_not_give_a_new_workspace_a_plan():
    """Sin plan no se aplica ningún límite; asignar 'Gratis' por defecto es otra decisión."""
    user = User.objects.create_user(username='nuevo', email='nuevo@example.com', password='x')

    assert Workspace.objects.get(owner=user).subscription_plan is None


def test_a_new_workspace_keeps_its_admin_member_and_wallet():
    user = User.objects.create_user(username='otro', email='otro@example.com', password='x')

    workspace = Workspace.objects.get(owner=user)
    member = WorkspaceMember.objects.get(workspace=workspace, user=user)
    assert member.role == WorkspaceMember.Role.ADMIN
    assert member.status == WorkspaceMember.Status.ACTIVE
    assert Wallet.objects.filter(workspace=workspace).exists()
