import importlib

import pytest

# The seed of the plans lives in its migrations (0007, 0009 for the seats of Gratis
# and 0013 for those of Creador); the tests call the same functions instead of trusting the rows the migrations left: a TransactionTestCase flushes
# them, and with --reuse-db they are gone for good.
seed_migration = importlib.import_module('apps.payments.migrations.0007_complete_seeded_plans')
free_plan_migration = importlib.import_module('apps.payments.migrations.0009_free_plan_two_members')
creator_plan_migration = importlib.import_module('apps.payments.migrations.0013_creator_plan_five_members')
SEEDED_NAMES = [plan['name'] for plan in seed_migration.PLANS]


@pytest.fixture
def seeded_plans(db):
    """The four plans of the pricing page, by name."""
    from apps.payments.models import SubscriptionPlan

    seed_migration.seed_plans(SubscriptionPlan)
    free_plan_migration.widen_free_plan(SubscriptionPlan)
    creator_plan_migration.widen_creator_plan(SubscriptionPlan)
    return {plan.name: plan for plan in SubscriptionPlan.objects.filter(name__in=SEEDED_NAMES)}
