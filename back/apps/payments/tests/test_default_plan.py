"""
A new workspace starts on the plan of settings.DEFAULT_PLAN_NAME (Gratis): without a
plan PricingEngine checks nothing and the frontend allows nothing.
"""
import logging

import pytest

from apps.payments.services.plans import get_default_plan
from apps.users.models import User, Workspace

pytestmark = pytest.mark.django_db


def new_workspace(username='nuevo'):
    user = User.objects.create_user(username=username, email=f'{username}@example.com', password='x')
    return Workspace.objects.get(owner=user)


def test_a_new_workspace_starts_on_the_free_plan(seeded_plans):
    workspace = new_workspace()

    assert workspace.subscription_plan == seeded_plans['Gratis']


def test_the_default_plan_is_the_one_the_setting_names(seeded_plans, settings):
    settings.DEFAULT_PLAN_NAME = 'Creador'

    assert new_workspace().subscription_plan == seeded_plans['Creador']


def test_without_the_plan_the_sign_up_goes_on_and_it_is_logged(seeded_plans, settings, caplog):
    settings.DEFAULT_PLAN_NAME = 'Todavía no existe'

    with caplog.at_level(logging.WARNING, logger='apps.payments.services.plans'):
        workspace = new_workspace()

    assert workspace.subscription_plan is None
    assert [record.getMessage() for record in caplog.records] == [
        'plans.default_plan_missing setting=DEFAULT_PLAN_NAME'
    ]


def test_an_empty_setting_means_no_default_plan(seeded_plans, settings, caplog):
    settings.DEFAULT_PLAN_NAME = ''

    with caplog.at_level(logging.WARNING, logger='apps.payments.services.plans'):
        assert get_default_plan() is None

    assert caplog.records == []
