"""
A new workspace starts on the plan of settings.DEFAULT_PLAN_NAME (Gratis): without a
plan PricingEngine checks nothing and the frontend allows nothing. Whatever happens
with that plan, the user always gets a workspace.
"""
import logging
from unittest import mock

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.payments.models import SubscriptionPlan
from apps.payments.services.plans import get_default_plan_id
from apps.users.models import User, Workspace

SERVICE_LOGGER = 'apps.payments.services.plans'


def new_workspace(username='nuevo'):
    user = User.objects.create_user(username=username, email=f'{username}@example.com', password='x')
    return Workspace.objects.get(owner=user)


def warnings(caplog):
    return [record.getMessage() for record in caplog.records]


@pytest.mark.django_db
def test_a_new_workspace_starts_on_the_free_plan(seeded_plans):
    workspace = new_workspace()

    assert workspace.subscription_plan == seeded_plans['Gratis']


@pytest.mark.django_db
def test_the_default_plan_is_the_one_the_setting_names(seeded_plans, settings):
    settings.DEFAULT_PLAN_NAME = 'Creador'

    assert new_workspace().subscription_plan == seeded_plans['Creador']


@pytest.mark.django_db
def test_without_the_plan_the_sign_up_goes_on_and_it_is_logged(seeded_plans, settings, caplog):
    settings.DEFAULT_PLAN_NAME = 'Todavía no existe'

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        workspace = new_workspace()

    assert workspace.subscription_plan is None
    assert warnings(caplog) == ['plans.default_plan_missing setting=DEFAULT_PLAN_NAME']


@pytest.mark.django_db
def test_an_inactive_default_plan_is_not_assigned(seeded_plans, caplog):
    SubscriptionPlan.objects.filter(name='Gratis').update(is_active=False)

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        workspace = new_workspace()

    assert workspace.subscription_plan is None
    assert warnings(caplog) == [f"plans.default_plan_inactive plan_id={seeded_plans['Gratis'].pk}"]


@pytest.mark.django_db
def test_a_failed_lookup_still_gives_the_user_a_workspace(seeded_plans, caplog):
    def broken_lookup(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute('SELECT no_such_column FROM payments_subscriptionplan')

    with mock.patch.object(SubscriptionPlan.objects, 'filter', side_effect=broken_lookup), \
            caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        workspace = new_workspace()

    # The failed query rolled back its savepoint only: the sign-up's queries go on.
    assert workspace.subscription_plan is None
    assert workspace.members.count() == 1
    assert warnings(caplog) == ['plans.default_plan_lookup_failed error=ProgrammingError']


@pytest.mark.django_db
def test_an_empty_setting_means_no_default_plan(seeded_plans, settings, caplog):
    settings.DEFAULT_PLAN_NAME = ''

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        assert get_default_plan_id() is None

    assert caplog.records == []


class SignUpBeforeTheMigrationsTests(TransactionTestCase):
    """New code can run before `migrate` (a deploy): the lookup reads no column a migration adds."""

    def tearDown(self):
        call_command('migrate', verbosity=0)

    def test_a_sign_up_before_0006_gets_a_workspace_on_the_free_plan(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('payments', '0005_seed_subscription_plans')])  # no is_public yet
        Plan = executor.loader.project_state([('payments', '0005_seed_subscription_plans')]).apps.get_model(
            'payments', 'SubscriptionPlan'
        )
        free, _ = Plan.objects.get_or_create(name='Gratis', defaults={'max_video_duration_seconds': 600})

        user = User.objects.create_user(username='temprana', email='temprana@example.com', password='x')

        workspace = Workspace.objects.get(owner=user)
        self.assertEqual(workspace.subscription_plan_id, free.pk)
