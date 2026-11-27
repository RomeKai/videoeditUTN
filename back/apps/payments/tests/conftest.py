import importlib

import pytest

# The seed of the plans lives in its migrations (0007, 0009 for the seats of Gratis,
# 0013 for those of Creador and 0014 for those of Pro); the tests call the same functions instead of trusting the rows the migrations left: a TransactionTestCase flushes
# them, and with --reuse-db they are gone for good.
seed_migration = importlib.import_module('apps.payments.migrations.0007_complete_seeded_plans')
free_plan_migration = importlib.import_module('apps.payments.migrations.0009_free_plan_two_members')
creator_plan_migration = importlib.import_module('apps.payments.migrations.0013_creator_plan_five_members')
pro_plan_migration = importlib.import_module('apps.payments.migrations.0014_pro_plan_ten_members')
social_accounts_migration = importlib.import_module('apps.payments.migrations.0019_conectar_redes_en_planes_pagos')
SEEDED_NAMES = [plan['name'] for plan in seed_migration.PLANS]


@pytest.fixture
def seeded_plans(db):
    """The four plans of the pricing page, by name."""
    from apps.payments.models import SubscriptionPlan

    seed_migration.seed_plans(SubscriptionPlan)
    free_plan_migration.widen_free_plan(SubscriptionPlan)
    creator_plan_migration.widen_creator_plan(SubscriptionPlan)
    pro_plan_migration.widen_pro_plan(SubscriptionPlan)
    social_accounts_migration.allow_social_accounts(SubscriptionPlan)
    return {plan.name: plan for plan in SubscriptionPlan.objects.filter(name__in=SEEDED_NAMES)}
