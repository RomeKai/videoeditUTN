"""
payments 0006-0013: the four seeded plans end complete and public after a rollback
and a new migrate, an edit made in the admin stays, Estudio moves to 3 h, Gratis
to 2 members and Creador to 5 only if nobody changed them, only one plan stays recommended, and a
limit below 1 is raised to 1 before the check constraints.
"""
from decimal import Decimal

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.core.testing import clear_local_caches
from apps.payments.services.plans import PUBLIC_PLANS_CACHE_KEY

BEFORE_PRICING = [('payments', '0003_transaction_created_at')]
AFTER_SEED = [('payments', '0006_subscriptionplan_is_public_and_limits')]
BEFORE_FREE_SEATS = [('payments', '0008_subscriptionplan_single_recommended')]
BEFORE_LIMIT_CHECKS = [('payments', '0009_free_plan_two_members')]
BEFORE_CREATOR_SEATS = [('payments', '0012_transaction_settled_at_reserved_amount')]
LATEST = [('payments', '0013_creator_plan_five_members')]

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
        clear_local_caches()

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
        self.assertEqual(Plan.objects.get(name='Gratis').max_members, 2)

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

    def test_the_free_plan_moves_to_two_members_only_if_it_still_had_one(self):
        Plan = self.migrate(BEFORE_FREE_SEATS)
        for name in EXPECTED:
            Plan.objects.get_or_create(name=name, defaults={'max_video_duration_seconds': 600})
        Plan.objects.filter(name='Gratis').update(max_members=1)
        Plan.objects.filter(name='Creador').update(max_members=3)  # an edit: 0009 does not touch Creador, 0013 keeps it

        Plan = self.migrate(LATEST)
        self.assertEqual(Plan.objects.get(name='Gratis').max_members, 2)
        self.assertEqual(Plan.objects.get(name='Creador').max_members, 3)

        # The reverse keeps 2, and a value set in the admin stays when 0009 runs again.
        Plan = self.migrate(BEFORE_FREE_SEATS)
        self.assertEqual(Plan.objects.get(name='Gratis').max_members, 2)
        Plan.objects.filter(name='Gratis').update(max_members=4)
        Plan = self.migrate(LATEST)
        self.assertEqual(Plan.objects.get(name='Gratis').max_members, 4)

    def test_the_creator_plan_moves_to_five_members_only_if_it_still_had_two(self):
        Plan = self.migrate(BEFORE_CREATOR_SEATS)
        for name in EXPECTED:
            Plan.objects.get_or_create(name=name, defaults={'max_video_duration_seconds': 600})
        Plan.objects.filter(name='Creador').update(max_members=2)
        Plan.objects.filter(name='Pro').update(max_members=2)  # same value, another plan: 0013 leaves it

        Plan = self.migrate(LATEST)
        self.assertEqual(Plan.objects.get(name='Creador').max_members, 5)
        self.assertEqual(Plan.objects.get(name='Pro').max_members, 2)

        # The reverse keeps 5, and a value set in the admin stays when 0013 runs again.
        Plan = self.migrate(BEFORE_CREATOR_SEATS)
        self.assertEqual(Plan.objects.get(name='Creador').max_members, 5)
        Plan.objects.filter(name='Creador').update(max_members=3)
        Plan = self.migrate(LATEST)
        self.assertEqual(Plan.objects.get(name='Creador').max_members, 3)

    def test_a_limit_below_one_is_raised_to_one_before_the_check_constraints(self):
        Plan = self.migrate(BEFORE_LIMIT_CHECKS)
        Plan.objects.update_or_create(name='Free', defaults={'max_members': 0, 'max_video_duration_seconds': -5})

        Plan = self.migrate(LATEST)

        old = Plan.objects.get(name='Free')
        self.assertEqual((old.max_members, old.max_video_duration_seconds), (1, 1))
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, 'payments_subscriptionplan')
        self.assertTrue({'payments_plan_max_members_min_1', 'payments_plan_max_duration_min_1'} <= set(constraints))

    def test_the_cached_public_list_is_dropped(self):
        for start in (AFTER_SEED, BEFORE_FREE_SEATS, BEFORE_CREATOR_SEATS):  # by 0007, 0009 and 0013
            with self.subTest(start=start[0][1]):
                Plan = self.migrate(start)
                if start is BEFORE_CREATOR_SEATS:  # 0013 only drops it when it changes a plan
                    Plan.objects.update_or_create(name='Creador', defaults={'max_members': 2, 'max_video_duration_seconds': 3600})
                cache.set(PUBLIC_PLANS_CACHE_KEY, [{'name': 'Starter'}], 60)

                self.migrate(LATEST)

                self.assertIsNone(cache.get(PUBLIC_PLANS_CACHE_KEY))
