"""
PAY-03 · GET /api/v1/plans/: planes activos y públicos, en orden, con cache que se
borra al hacer commit, más el seed de los cuatro planes.
"""
from decimal import Decimal

import pytest
from django.conf import settings
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.core.testing import clear_local_caches
from apps.payments.models import SubscriptionPlan, Wallet
from apps.payments.services.plans import (
    PUBLIC_PLANS_CACHE_KEY,
    PUBLIC_PLANS_CACHE_TTL,
    get_public_plans,
)
from apps.users.models import User, Workspace, WorkspaceMember

pytestmark = pytest.mark.django_db

URL = '/api/v1/plans/'

CONTRACT_FIELDS = {
    'id', 'name', 'max_video_duration_seconds', 'max_resolution', 'has_watermark',
    'allow_scheduling', 'allow_crossposting', 'has_seo_optimization',
    'has_thumbnail_engine', 'allow_social_accounts', 'base_discount_rate', 'max_members', 'is_active',
    'price_usd_monthly', 'description', 'sort_order', 'is_recommended',
}


@pytest.fixture(autouse=True)
def plans(seeded_plans):
    return seeded_plans


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


def test_estudio_allows_three_hours_the_ceiling_of_any_upload(client):
    estudio = next(plan for plan in client.get(URL).json() if plan['name'] == 'Estudio')

    assert estudio['max_video_duration_seconds'] == 10800
    assert estudio['max_video_duration_seconds'] == settings.AI_UPLOAD_MAX_DURATION_SECONDS


def test_each_plan_lists_its_seats(client):
    # Gratis 2 (0009), Creador 5 (0013), Pro 10 (0014), Estudio 15 (0005): the seed migrations all ran.
    seats = {plan['name']: plan['max_members'] for plan in client.get(URL).json()}

    assert seats == {'Gratis': 2, 'Creador': 5, 'Pro': 10, 'Estudio': 15}


def test_no_public_plan_promises_more_than_an_upload_can_last(client):
    longest = max(plan['max_video_duration_seconds'] for plan in client.get(URL).json())

    assert longest <= settings.AI_UPLOAD_MAX_DURATION_SECONDS


def test_inactive_plans_are_not_listed(client):
    SubscriptionPlan.objects.filter(name='Estudio').update(is_active=False)

    assert names(client.get(URL)) == ['Gratis', 'Creador', 'Pro']


def test_a_plan_that_is_not_public_is_not_listed(client):
    """An old plan ("Starter", "Free") kept the bare values: it would show first, at 0.00."""
    SubscriptionPlan.objects.create(name='Starter', max_video_duration_seconds=60)

    assert names(client.get(URL)) == ['Gratis', 'Creador', 'Pro', 'Estudio']


def test_a_plan_made_in_the_admin_is_not_public_until_someone_says_so(client):
    plan = SubscriptionPlan.objects.create(name='Agencia', sort_order=5)
    assert plan.is_public is False

    plan.is_public = True
    plan.save()

    assert names(client.get(URL))[-1] == 'Agencia'


def test_an_invalid_token_does_not_break_the_public_list(client):
    client.credentials(HTTP_AUTHORIZATION='Bearer expirado.o.roto')

    assert client.get(URL).status_code == 200


def test_only_get_is_allowed(client):
    assert client.post(URL, {}, format='json').status_code == 405


def test_the_second_request_is_served_from_cache(client, django_assert_num_queries):
    client.get(URL)

    with django_assert_num_queries(0):
        assert client.get(URL).status_code == 200


def test_the_cache_lasts_five_minutes_at_most():
    """Safety net for a change that skips the signals."""
    assert PUBLIC_PLANS_CACHE_TTL <= 5 * 60


def test_a_list_cached_by_the_previous_code_is_not_served(client):
    """The key is versioned: after a deploy, the old payload (with every active plan) is ignored."""
    cache.set('payments:public-plans', [{'name': 'Starter'}], 60 * 60)

    assert names(client.get(URL)) == ['Gratis', 'Creador', 'Pro', 'Estudio']


def test_editing_a_plan_refreshes_the_cached_list(client, django_capture_on_commit_callbacks):
    client.get(URL)

    with django_capture_on_commit_callbacks(execute=True):
        plan = SubscriptionPlan.objects.get(name='Pro')
        plan.description = 'Nueva descripción'
        plan.save()

    pro = next(item for item in client.get(URL).json() if item['name'] == 'Pro')
    assert pro['description'] == 'Nueva descripción'


def test_the_list_is_dropped_when_the_change_commits_not_before(client, django_capture_on_commit_callbacks):
    """
    The admin saves inside a transaction. A request that reads the plans before the
    commit still sees the old rows and caches them: the drop has to come after.
    """
    stale = get_public_plans()

    with django_capture_on_commit_callbacks(execute=True):
        plan = SubscriptionPlan.objects.get(name='Pro')
        plan.description = 'Nueva descripción'
        plan.save()
        # Another request, before the commit: it reads the old rows and caches them.
        cache.set(PUBLIC_PLANS_CACHE_KEY, stale, PUBLIC_PLANS_CACHE_TTL)

    pro = next(item for item in client.get(URL).json() if item['name'] == 'Pro')
    assert pro['description'] == 'Nueva descripción'


def test_saving_a_plan_drops_the_list_only_on_commit(django_capture_on_commit_callbacks):
    cached = get_public_plans()

    with django_capture_on_commit_callbacks() as callbacks:
        SubscriptionPlan.objects.get(name='Pro').save()
        assert cache.get(PUBLIC_PLANS_CACHE_KEY) == cached  # nothing dropped before the commit

    assert len(callbacks) == 1


def test_deactivating_a_plan_refreshes_the_cached_list(client, django_capture_on_commit_callbacks):
    client.get(URL)

    with django_capture_on_commit_callbacks(execute=True):
        plan = SubscriptionPlan.objects.get(name='Creador')
        plan.is_active = False
        plan.save()

    assert 'Creador' not in names(client.get(URL))


def test_deleting_a_plan_refreshes_the_cached_list(client, django_capture_on_commit_callbacks):
    client.get(URL)

    with django_capture_on_commit_callbacks(execute=True):
        SubscriptionPlan.objects.get(name='Estudio').delete()

    assert 'Estudio' not in names(client.get(URL))


def test_the_plans_endpoint_is_in_the_openapi_schema(client):
    schema = client.get('/api/schema/?format=json').json()

    assert '/api/v1/plans/' in schema['paths']


def test_plan_and_project_resolutions_share_one_enum_in_the_schema(client):
    schemas = client.get('/api/schema/?format=json').json()['components']['schemas']

    assert schemas['ResolutionEnum']['enum'] == ['720p', '1080p', '4K']
    assert 'MaxResolutionEnum' not in schemas
    assert 'RenderResolutionEnum' not in schemas


def test_a_new_workspace_keeps_its_admin_member_and_wallet():
    user = User.objects.create_user(username='otro', email='otro@example.com', password='x')

    workspace = Workspace.objects.get(owner=user)
    member = WorkspaceMember.objects.get(workspace=workspace, user=user)
    assert member.role == WorkspaceMember.Role.ADMIN
    assert member.status == WorkspaceMember.Status.ACTIVE
    assert Wallet.objects.filter(workspace=workspace).exists()


def test_only_the_paid_plans_connect_networks(client):
    """Connecting takes a paid profile at the publishing provider (payments 0019): Gratis does not."""
    allowed = {plan['name']: plan['allow_social_accounts'] for plan in client.get(URL).json()}
    assert allowed == {'Gratis': False, 'Creador': True, 'Pro': True, 'Estudio': True}


def test_a_list_cached_by_the_release_before_the_flag_is_not_served(client):
    """
    Review of #187: during a rolling deploy an old container caches the list without
    `allow_social_accounts`; the new code must not serve it (the web requires the field).
    """
    old = [{key: value for key, value in plan.items() if key != 'allow_social_accounts'} for plan in get_public_plans()]
    cache.delete(PUBLIC_PLANS_CACHE_KEY)
    cache.set('payments:public-plans:v2', old, PUBLIC_PLANS_CACHE_TTL)

    assert all('allow_social_accounts' in plan for plan in client.get(URL).json())
