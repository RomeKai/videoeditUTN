"""
payments 0006-0008: the four seeded plans end complete and public after a rollback
and a new migrate, an edit made in the admin stays, Estudio moves to 3 h only if
nobody changed it, and only one plan stays recommended.
"""
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.payments.services.plans import PUBLIC_PLANS_CACHE_KEY

BEFORE_PRICING = [('payments', '0003_transaction_created_at')]
AFTER_SEED = [('payments', '0006_subscriptionplan_is_public_and_limits')]
LATEST = [('payments', '0008_subscriptionplan_single_recommended')]

EXPECTED = {
    # name: (price, sort_order, max_video_duration_seconds)
    'Gratis': (Decimal('0.00'), 1, 600),
    'Creador': (Decimal('19.00'), 2, 3600),
    'Pro': (Decimal('49.00'), 3, 10800),
    'Estudio': (Decimal('129.00'), 4, 10800),
}


class SeededPlansMigrationTests(TransactionTestCase):

    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command('migrate', verbosity=0)
        cache.clear()

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps.get_model('payments', 'SubscriptionPlan')

    def test_rolling_back_and_migrating_again_leaves_the_four_plans_complete(self):
        Plan = self.migrate(BEFORE_PRICING)
        # The rows stay when 0004 is rolled back; an old plan under another name too.
        for name, (_, _, seconds) in EXPECTED.items():
            Plan.objects.update_or_create(
                name=name, defaults={'max_video_duration_seconds': 14400 if name == 'Estudio' else seconds},
            )
        Plan.objects.update_or_create(name='Starter', defaults={'max_video_duration_seconds': 60})

        Plan = self.migrate(LATEST)

        for name, (price, order, seconds) in EXPECTED.items():
            plan = Plan.objects.get(name=name)
            self.assertEqual(plan.price_usd_monthly, price, name)
            self.assertEqual(plan.sort_order, order, name)
            self.assertEqual(plan.max_video_duration_seconds, seconds, name)
            self.assertNotEqual(plan.description, '', name)
            self.assertTrue(plan.is_public, name)
        self.assertEqual(list(Plan.objects.filter(is_recommended=True).values_list('name', flat=True)), ['Pro'])
        self.assertFalse(Plan.objects.get(name='Starter').is_public)

    def test_values_edited_in_the_admin_are_kept(self):
        Plan = self.migrate(AFTER_SEED)
        for name in EXPECTED:  # 0005 seeded them; a flushed test database may not have them
            Plan.objects.get_or_create(name=name, defaults={'max_video_duration_seconds': 600})
        Plan.objects.filter(is_recommended=True).update(is_recommended=False)
        Plan.objects.filter(name='Creador').update(
            price_usd_monthly=Decimal('25.00'), description='Editado', sort_order=7, is_recommended=True,
        )
        Plan.objects.filter(name='Estudio').update(max_video_duration_seconds=7200)

        Plan = self.migrate(LATEST)

        creador = Plan.objects.get(name='Creador')
        self.assertEqual(
            (creador.price_usd_monthly, creador.description, creador.sort_order),
            (Decimal('25.00'), 'Editado', 7),
        )
        self.assertEqual(list(Plan.objects.filter(is_recommended=True).values_list('name', flat=True)), ['Creador'])
        self.assertEqual(Plan.objects.get(name='Estudio').max_video_duration_seconds, 7200)

    def test_two_recommended_plans_end_as_one_before_the_constraint(self):
        Plan = self.migrate(AFTER_SEED)
        for name in EXPECTED:
            Plan.objects.get_or_create(name=name, defaults={'max_video_duration_seconds': 600})
        Plan.objects.filter(name__in=['Pro', 'Estudio']).update(is_recommended=True)

        Plan = self.migrate(LATEST)

        self.assertEqual(list(Plan.objects.filter(is_recommended=True).values_list('name', flat=True)), ['Pro'])

    def test_the_cached_public_list_is_dropped(self):
        self.migrate(AFTER_SEED)
        cache.set(PUBLIC_PLANS_CACHE_KEY, [{'name': 'Starter'}], 60)

        self.migrate(LATEST)

        self.assertIsNone(cache.get(PUBLIC_PLANS_CACHE_KEY))
