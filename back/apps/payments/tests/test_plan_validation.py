"""
What the admin can save in a plan: one 0 in a limit breaks the whole pricing page
(the frontend parses the list with z.array(Plan)), and only one plan is recommended.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.payments.admin import SubscriptionPlanAdmin
from apps.payments.models import SubscriptionPlan

pytestmark = pytest.mark.django_db


def plan(**fields):
    return SubscriptionPlan(name=fields.pop('name', 'Prueba'), **fields)


@pytest.mark.parametrize('field', ['max_members', 'max_video_duration_seconds'])
def test_a_limit_of_zero_is_rejected(field):
    with pytest.raises(ValidationError) as error:
        plan(**{field: 0}).full_clean()

    assert field in error.value.message_dict


def test_a_limit_of_one_is_valid():
    plan(max_members=1, max_video_duration_seconds=1).full_clean()


def test_the_admin_cannot_mark_a_second_plan_as_recommended(seeded_plans):
    second = seeded_plans['Creador']
    second.is_recommended = True

    with pytest.raises(ValidationError) as error:
        second.full_clean()

    assert 'Only one plan can be recommended' in str(error.value)


def test_the_database_refuses_a_second_recommended_plan(seeded_plans):
    with pytest.raises(IntegrityError), transaction.atomic():
        SubscriptionPlan.objects.filter(name='Estudio').update(is_recommended=True)


def test_moving_the_mark_to_another_plan_works(seeded_plans):
    SubscriptionPlan.objects.filter(name='Pro').update(is_recommended=False)
    creador = seeded_plans['Creador']
    creador.is_recommended = True
    creador.full_clean()
    creador.save()

    assert list(SubscriptionPlan.objects.filter(is_recommended=True).values_list('name', flat=True)) == ['Creador']


def test_the_admin_list_shows_price_order_recommended_and_public():
    for field in ('price_usd_monthly', 'sort_order', 'is_recommended', 'is_public'):
        assert field in SubscriptionPlanAdmin.list_display


def test_the_admin_list_page_renders(client, seeded_plans):
    admin = get_user_model().objects.create_superuser(username='admin', email='admin@example.com', password='x')
    client.force_login(admin)

    response = client.get('/admin/payments/subscriptionplan/')

    assert response.status_code == 200
    assert b'Estudio' in response.content
