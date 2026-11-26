"""
The cache of the public plans is only an optimization. With the cache server down
(Redis unreachable), the list is read from the database, a plan change commits
(also through the admin) and migrate applies every migration.
"""
import logging
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan
from apps.payments.tests.unreachable_cache import UNREACHABLE_CACHES

SERVICE_LOGGER = 'apps.payments.services.plans'


@pytest.fixture
def cache_down(settings):
    settings.CACHES = UNREACHABLE_CACHES


def cache_errors(caplog):
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith('plans.cache_unavailable')]


@pytest.mark.django_db
def test_the_public_list_is_read_from_the_database(seeded_plans, cache_down, caplog):
    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        response = APIClient().get('/api/v1/plans/')

    assert response.status_code == 200
    assert [plan['name'] for plan in response.json()] == ['Gratis', 'Creador', 'Pro', 'Estudio']
    assert cache_errors(caplog) == [
        'plans.cache_unavailable operation=get error=ConnectionError',
        'plans.cache_unavailable operation=set error=ConnectionError',
    ]


@pytest.mark.django_db(transaction=True)
def test_a_plan_change_commits(seeded_plans, cache_down, caplog):
    """The drop runs after the commit: an error there must not reach the caller."""
    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        with transaction.atomic():
            plan = SubscriptionPlan.objects.get(name='Pro')
            plan.description = 'Nueva descripción'
            plan.save()

    assert SubscriptionPlan.objects.get(name='Pro').description == 'Nueva descripción'
    assert cache_errors(caplog) == ['plans.cache_unavailable operation=delete error=ConnectionError']


@pytest.mark.django_db(transaction=True)
def test_the_admin_saves_a_plan(seeded_plans, cache_down, client):
    admin = get_user_model().objects.create_superuser(username='admin', email='admin@example.com', password='x')
    client.force_login(admin)
    plan = seeded_plans['Creador']
    form = {
        field.name: getattr(plan, field.name)
        for field in SubscriptionPlan._meta.fields
        if field.editable and field.name != 'id' and not isinstance(getattr(plan, field.name), bool)
    }
    form.update({field.name: 'on' for field in SubscriptionPlan._meta.fields if getattr(plan, field.name) is True})
    form['price_usd_monthly'] = '21.00'

    response = client.post(f'/admin/payments/subscriptionplan/{plan.pk}/change/', form)

    assert response.status_code == 302, response.content[:500]
    assert SubscriptionPlan.objects.get(pk=plan.pk).price_usd_monthly == Decimal('21.00')


@override_settings(CACHES=UNREACHABLE_CACHES)
class MigrateWithTheCacheDownTests(TransactionTestCase):

    def tearDown(self):
        call_command('migrate', verbosity=0)

    def test_every_payments_migration_is_applied(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('payments', '0006_subscriptionplan_is_public_and_limits')])

        call_command('migrate', 'payments', verbosity=0)

        applied = set(MigrationRecorder(connection).applied_migrations())
        latest = MigrationExecutor(connection).loader.graph.leaf_nodes('payments')
        self.assertTrue(set(latest) <= applied, latest)
