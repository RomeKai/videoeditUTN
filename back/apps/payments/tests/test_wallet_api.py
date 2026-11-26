"""
The wallet of the active workspace (front contract billing.wallet, billing.usage,
billing.pricing and billing.estimate):

- GET /wallet/: balances as exact decimal text, what was charged this month
  and each open reservation with its project, newest first. Every role.
- GET /wallet/usage/: what was charged on each day of a month, in the
  workspace's zone, by the day it was charged. Dueño and Admin.
- GET /pricing/: PricingEngine's numbers and the upload limits. Public.
- POST /pricing/estimate/: what create would charge, with the plan's limits
  as 403 + code. Every role.

Another workspace's data never shows; a workspace the user is not in is 404.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.core.permissions import ADMINS, DENIED
from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.payments.services import wallet_service
from apps.payments.services.estimate import LINK_RESOLUTION_ERROR
from apps.payments.services.pricing_engine import PricingEngine
from apps.users.models import WorkspaceMember
from apps.videos.models import VideoProject
from apps.videos.tests.test_active_workspace import ADMIN, EDITOR, VIEWER, fund, header, home_of, join, make_user, team

BA = ZoneInfo('America/Argentina/Buenos_Aires')
SPEND, DEPOSIT, REFUND = Transaction.Type.SPEND, Transaction.Type.DEPOSIT, Transaction.Type.REFUND
RESERVED, COMPLETED, FAILED, PENDING = (
    Transaction.Status.RESERVED, Transaction.Status.COMPLETED, Transaction.Status.FAILED, Transaction.Status.PENDING,
)
PROJECT, RENDER, SEO = Transaction.Category.PROJECT, Transaction.Category.RENDER, Transaction.Category.SEO

WALLET = reverse('wallet')
USAGE = reverse('wallet-usage')
PRICING = reverse('pricing')
ESTIMATE = reverse('pricing-estimate')


def local(year, month, day, hour=12, minute=0):
    """An instant given in the workspace's zone (UTC-3)."""
    return datetime(year, month, day, hour, minute, tzinfo=BA)


def movement(workspace, amount, kind=SPEND, status=COMPLETED, *, at=None, settled=None, project=None,
             category=None, description='Movimiento', user=None, reserved=None):
    """A Transaction as wallet_service leaves it, with the times given."""
    wallet = Wallet.objects.get(workspace=workspace)
    txn = Transaction.objects.create(
        wallet=wallet, amount=Decimal(amount), transaction_type=kind, status=status, description=description,
        created_by=user, project=project,
        category=category or (Transaction.Category.PURCHASE if kind == DEPOSIT else PROJECT),
        reserved_amount=Decimal(reserved) if reserved is not None else None,
    )
    fields = {}
    if at is not None:
        fields['created_at'] = at
    if settled is not None:
        fields['settled_at'] = settled
    if fields:
        Transaction.objects.filter(pk=txn.pk).update(**fields)
    txn.refresh_from_db()
    return txn


def plan(name='Prueba', **fields):
    defaults = {
        'max_video_duration_seconds': 3600, 'max_resolution': '1080p', 'has_watermark': False,
        'has_seo_optimization': True, 'base_discount_rate': Decimal('0.05'), 'max_members': 5,
    }
    return SubscriptionPlan.objects.create(name=name, **{**defaults, **fields})


class WalletHarness:
    """Alice owns her home workspace; Boss owns a team workspace with its own wallet."""

    def setUp(self):
        super().setUp()
        self.alice = make_user('alice')
        self.home = home_of(self.alice)
        fund(self.home, '500.00')
        self.boss = make_user('boss')
        self.team = team(self.boss)
        fund(self.team, '900.00')
        self.client.force_authenticate(self.alice)

    def project(self, workspace=None, title='Entrevista con Ana'):
        return VideoProject.objects.create(
            workspace=workspace or self.home, uploaded_by=self.alice, title=title, source_file='test/file.mp4',
        )

    def get(self, url, workspace=None, **query):
        return self.client.get(url, query, **header(workspace or self.home))

    def as_member(self, role):
        """Alice as ``role`` of the team workspace (only that one)."""
        WorkspaceMember.objects.filter(workspace=self.team, user=self.alice).delete()
        join(self.team, self.alice, role)
        return self.team


class WalletTests(WalletHarness, APITestCase):
    def test_the_balances_and_each_reservation_with_its_project(self):
        project = self.project()
        wallet = Wallet.objects.get(workspace=self.home)
        older = wallet_service.reserve_funds(
            wallet.id, Decimal('12.5'), description='Procesamiento: Entrevista con Ana', project=project, category=PROJECT,
        )
        newer = wallet_service.reserve_funds(wallet.id, Decimal('0.3333333333'), description='Render suelto')
        Transaction.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(minutes=5))
        # Neither a charge nor a refund is reserved.
        movement(self.home, '7', status=COMPLETED)
        movement(self.home, '3', status=FAILED)

        response = self.get(WALLET)

        self.assertEqual(response.status_code, 200, response.content[:300])
        data = response.json()
        self.assertEqual(data['id'], str(wallet.id))
        self.assertEqual(data['workspace'], str(self.home.pk))
        # Exact, as text: what is left after the reservations, to the tenth decimal.
        self.assertEqual(data['available_balance'], '487.1666666667')
        self.assertEqual(data['reserved_balance'], '12.8333333333')
        self.assertEqual(
            [(item['transaction_id'], item['project_id'], item['project_title'], item['amount']) for item in data['reservations']],
            [(str(newer.pk), None, None, '0.3333333333'), (str(older.pk), str(project.pk), 'Entrevista con Ana', '12.5000000000')],
        )
        self.assertTrue(data['updated_at'])

    def test_spent_this_month_is_what_was_charged_this_month_in_the_workspace_zone(self):
        now = timezone.now()
        month_start = now.astimezone(BA).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        movement(self.home, '10', at=month_start + timedelta(minutes=1))
        # Reserved last month, charged this one: it counts on the day it was charged.
        movement(self.home, '2.25', at=month_start - timedelta(days=2), settled=month_start + timedelta(minutes=2))
        # A partial charge counts what was charged, not what was reserved.
        movement(self.home, '4', reserved='9', at=month_start + timedelta(minutes=3), settled=month_start + timedelta(minutes=4))
        # Not spent: charged before the month, reserved, returned, bought, another workspace's.
        movement(self.home, '100', at=month_start - timedelta(minutes=1), settled=month_start - timedelta(seconds=1))
        movement(self.home, '20', status=RESERVED, at=month_start + timedelta(minutes=5))
        movement(self.home, '30', status=FAILED, at=month_start + timedelta(minutes=5))
        movement(self.home, '5000', kind=DEPOSIT, at=month_start + timedelta(minutes=5))
        movement(self.team, '40', at=month_start + timedelta(minutes=5))

        data = self.get(WALLET).json()

        self.assertEqual(data['spent_this_month'], '16.2500000000')

    def test_the_low_balance_threshold_is_20_minutes_at_720p_with_the_plan_discount(self):
        self.assertEqual(self.get(WALLET).json()['low_balance_threshold'], '600.00')  # no plan

        self.home.subscription_plan = plan(base_discount_rate=Decimal('0.10'))
        self.home.save(update_fields=['subscription_plan'])

        self.assertEqual(self.get(WALLET).json()['low_balance_threshold'], '540.00')

    def test_every_role_sees_the_balance_of_its_workspace_only(self):
        movement(self.home, '1', status=RESERVED)
        for role in (VIEWER, EDITOR, ADMIN):
            with self.subTest(role=role):
                self.as_member(role)
                data = self.get(WALLET, self.team).json()
                self.assertEqual((data['workspace'], data['available_balance']), (str(self.team.pk), '900.0000000000'))
                self.assertEqual(data['reservations'], [])

    def test_a_workspace_the_user_is_not_in_is_a_404(self):
        response = self.get(WALLET, self.team)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('900', response.content.decode())

    def test_a_user_without_any_workspace_gets_a_404(self):
        lonely = make_user('lonely')
        home_of(lonely).delete()
        self.client.force_authenticate(lonely)

        self.assertEqual(self.client.get(WALLET).status_code, 404)

    def test_an_old_workspace_without_wallet_gets_an_empty_one(self):
        Wallet.objects.filter(workspace=self.home).delete()

        data = self.get(WALLET).json()

        self.assertEqual(
            (data['available_balance'], data['reserved_balance'], data['reservations']),
            ('0.0000000000', '0.0000000000', []),
        )
        self.assertTrue(Wallet.objects.filter(workspace=self.home).exists())

    def test_the_reservations_take_the_same_queries_however_many_there_are(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.get(WALLET).status_code, 200)
            return len(queries)

        movement(self.home, '1', status=RESERVED, project=self.project(title='Uno'))
        few = count()
        for number in range(10):
            movement(self.home, '1', status=RESERVED, project=self.project(title=f'P{number}'))

        self.assertEqual(count(), few)


class UsageTests(WalletHarness, APITestCase):
    def test_each_day_of_the_month_split_in_projects_renders_and_seo(self):
        movement(self.home, '10.5', category=PROJECT, at=local(2026, 10, 3))
        movement(self.home, '0.25', category=PROJECT, at=local(2026, 10, 3, 20))
        movement(self.home, '4', category=RENDER, at=local(2026, 10, 3))
        movement(self.home, '5', category=SEO, at=local(2026, 10, 31, 23, 59))
        movement(self.home, '1', category=Transaction.Category.OTHER, at=local(2026, 10, 15))

        response = self.get(USAGE, month='2026-10')

        self.assertEqual(response.status_code, 200, response.content[:300])
        days = response.json()
        self.assertEqual(len(days), 31)
        self.assertEqual([day['date'] for day in days[:2]], ['2026-10-01', '2026-10-02'])
        by_date = {day['date']: day for day in days}
        self.assertEqual(by_date['2026-10-03'], {
            'date': '2026-10-03', 'projects': '10.7500000000', 'renders': '4.0000000000', 'seo': '0.0000000000',
        })
        self.assertEqual(by_date['2026-10-31']['seo'], '5.0000000000')
        # A category with no column of its own counts as a project.
        self.assertEqual(by_date['2026-10-15']['projects'], '1.0000000000')
        self.assertEqual(by_date['2026-10-02']['projects'], '0.0000000000')

    def test_the_day_is_the_one_of_the_workspace_zone_and_of_the_charge(self):
        # 01:00 UTC of November 1st is still October 31st in Buenos Aires.
        movement(self.home, '1', at=datetime(2026, 11, 1, 1, 0, tzinfo=ZoneInfo('UTC')))
        # Reserved in October, charged on November 2nd.
        movement(self.home, '2', at=local(2026, 10, 30), settled=local(2026, 11, 2))

        october = {day['date']: day['projects'] for day in self.get(USAGE, month='2026-10').json()}
        november = {day['date']: day['projects'] for day in self.get(USAGE, month='2026-11').json()}

        self.assertEqual(october['2026-10-31'], '1.0000000000')
        self.assertEqual(october['2026-10-30'], '0.0000000000')
        self.assertEqual(november['2026-11-02'], '2.0000000000')
        self.assertEqual(november['2026-11-01'], '0.0000000000')
        self.assertEqual(len(november), 30)

    def test_only_what_was_charged_counts(self):
        at = local(2026, 10, 3)
        movement(self.home, '4', reserved='10', at=at, settled=at)  # partial: 4 charged, 6 returned
        movement(self.home, '20', status=RESERVED, at=at)
        movement(self.home, '30', status=FAILED, at=at)
        movement(self.home, '5000', kind=DEPOSIT, at=at)
        movement(self.home, '3', kind=REFUND, at=at)
        movement(self.team, '40', at=at)

        day = {day['date']: day for day in self.get(USAGE, month='2026-10').json()}['2026-10-03']

        self.assertEqual((day['projects'], day['renders'], day['seo']), ('4.0000000000', '0.0000000000', '0.0000000000'))

    def test_without_month_it_is_the_current_one(self):
        days = self.get(USAGE).json()

        self.assertEqual(days[0]['date'][:7], timezone.now().astimezone(BA).strftime('%Y-%m'))

    def test_a_month_that_is_not_yyyy_mm_is_a_400(self):
        for month in ('2026-13', '2026-1', '26-10', 'octubre', '2026-10-01', '9999-12'):
            with self.subTest(month=month):
                response = self.get(USAGE, month=month)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'month': ['Mandá el mes como AAAA-MM.']})

    def test_only_owner_and_admin(self):
        for role, code in ((VIEWER, 403), (EDITOR, 403), (ADMIN, 200)):
            with self.subTest(role=role):
                self.as_member(role)
                response = self.get(USAGE, self.team, month='2026-10')
                self.assertEqual(response.status_code, code)
                if code == 403:
                    self.assertEqual(response.json(), {'detail': DENIED[ADMINS]})

    def test_one_query_for_the_month_however_many_movements(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.get(USAGE, month='2026-10').status_code, 200)
            return len(queries)

        movement(self.home, '1', at=local(2026, 10, 1))
        few = count()
        for day in range(2, 29):
            movement(self.home, '1', category=RENDER, at=local(2026, 10, day))

        self.assertEqual(count(), few)


class PricingTests(APITestCase):
    def test_public_and_with_the_numbers_of_the_engine(self):
        # A stale token does not break it: no authentication is read.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer expired')

        response = client.get(PRICING)

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json(), {
            'base_cost_per_second_720p': '0.5',
            'resolution_multipliers': {'720p': '1.0', '1080p': '1.5', '4K': '3.0'},
            'feature_costs': {'seo_optimization': '5.0', 'ai_thumbnail': '15.0'},
            'link_flat_cost': '10.0',
            'min_cost': '0.01',
            'token_price_usd': '0.01',
            'single_edit_max_duration_seconds': 600,
            'upload_max_duration_seconds': 10800,
            'upload_max_size_bytes': 500 * 1024 * 1024,
            'upload_extensions': ['.mp4', '.mov', '.avi'],
            'selection_criteria_max_chars': 300,
            'prompt_max_chars': 1000,
        })


class EstimateTests(WalletHarness, APITestCase):
    def setUp(self):
        super().setUp()
        self.home.subscription_plan = plan()
        self.home.save(update_fields=['subscription_plan'])

    def estimate(self, workspace=None, **body):
        return self.client.post(ESTIMATE, body, format='json', **header(workspace or self.home))

    def test_a_file_costs_what_create_would_charge(self):
        response = self.estimate(source='file', duration_seconds=181.9, render_resolution='1080p', use_seo=True)

        self.assertEqual(response.status_code, 200, response.content[:300])
        data = response.json()
        # 181 whole seconds: (181 × 0.5 × 1.5 + 5) × 0.95 = 133.70625 -> 133.71
        expected = PricingEngine.calculate_render_cost(181, '1080p', {'seo_optimization': True}, self.home.subscription_plan)
        self.assertEqual(expected, Decimal('133.71'))
        self.assertEqual(data, {
            'base_cost': '135.75',
            'add_ons_cost': '5.0',
            'discount_rate': '0.05',
            'discount_amount': '7.04',
            'total': '133.71',
            'available_balance': '500.0000000000',
            'balance_after': '366.2900000000',
            'sufficient': True,
            'has_watermark': False,
            'eta_seconds': None,
        })

    def test_a_link_has_the_flat_price_and_only_the_seo_is_discounted(self):
        data = self.estimate(source='link', use_seo=True).json()

        self.assertEqual(
            (data['base_cost'], data['add_ons_cost'], data['total'], data['discount_amount'], data['eta_seconds']),
            ('10.0', '5.0', '14.75', '0.25', None),
        )

    def test_a_link_is_always_720p(self):
        response = self.estimate(source='link', render_resolution='1080p')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'render_resolution': [LINK_RESOLUTION_ERROR]})

    def test_a_file_needs_its_duration(self):
        response = self.estimate(source='file')

        self.assertEqual(response.status_code, 400)
        self.assertIn('duration_seconds', response.json())

    def test_not_enough_balance_says_so(self):
        fund(self.home, '10.00')

        data = self.estimate(source='file', duration_seconds=60).json()

        self.assertEqual((data['total'], data['sufficient'], data['balance_after']), ('28.50', False, '-18.5000000000'))

    def test_the_plan_limits_are_a_403_with_their_code(self):
        cases = (
            ({'source': 'file', 'duration_seconds': 3601}, 'duration', 3600),
            ({'source': 'file', 'duration_seconds': 10, 'render_resolution': '4K'}, 'resolution', '1080p'),
        )
        for body, code, limit in cases:
            with self.subTest(code=code):
                response = self.estimate(**body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual((response.json()['code'], response.json()['limit']), (code, limit))
                self.assertTrue(response.json()['error'].startswith('Tu plan Prueba'))
        SubscriptionPlan.objects.filter(pk=self.home.subscription_plan_id).update(has_seo_optimization=False)
        for source in ('file', 'link'):
            with self.subTest(source=source):
                response = self.estimate(source=source, duration_seconds=10, use_seo=True)
                self.assertEqual((response.status_code, response.json()['code']), (403, 'seo'))

    def test_without_plan_there_is_no_limit_nor_discount(self):
        self.home.subscription_plan = None
        self.home.save(update_fields=['subscription_plan'])

        data = self.estimate(source='file', duration_seconds=7200, render_resolution='4K').json()

        self.assertEqual((data['total'], data['discount_rate'], data['has_watermark']), ('10800.00', '0', True))

    def test_the_eta_comes_from_the_time_the_latest_projects_took_per_second(self):
        stages = {'audio_extracted': 10, 'transcribed': 30, 'moderated': 5, 'clips_selected': 15, 'proxy': 999}
        for seconds in (60, 120, 600):
            # 60 s of work for a 60 s video, 120 for 120, 600 for 600: one second per second.
            scaled = {key: value * seconds / 60 for key, value in stages.items()}
            VideoProject.objects.create(
                workspace=self.team, uploaded_by=self.boss, title='Otro', source_file='x.mp4',
                metadata={'stage_durations': scaled, 'priced_duration_s': seconds},
            )
        # Without the review stages, or without its seconds, a project tells nothing.
        VideoProject.objects.create(
            workspace=self.team, uploaded_by=self.boss, title='Sin', source_file='x.mp4',
            metadata={'stage_durations': {'audio_extracted': 1}, 'priced_duration_s': 1},
        )
        VideoProject.objects.create(
            workspace=self.team, uploaded_by=self.boss, title='Link', source_file='x.mp4',
            metadata={'stage_durations': stages},
        )

        self.assertEqual(self.estimate(source='file', duration_seconds=300).json()['eta_seconds'], 300)
        self.assertIsNone(self.estimate(source='link').json()['eta_seconds'])

    def test_every_role_can_estimate_and_it_uses_that_workspace(self):
        self.as_member(VIEWER)

        data = self.estimate(self.team, source='file', duration_seconds=10).json()

        self.assertEqual((data['total'], data['available_balance']), ('5.00', '900.0000000000'))

    def test_a_workspace_the_user_is_not_in_is_a_404(self):
        self.assertEqual(self.estimate(self.team, source='file', duration_seconds=10).status_code, 404)

    def test_the_body_is_validated(self):
        for body in ({}, {'source': 'video'}, {'source': 'file', 'duration_seconds': -1},
                     {'source': 'file', 'duration_seconds': 10, 'render_resolution': '8K'}):
            with self.subTest(body=body):
                self.assertEqual(self.estimate(**body).status_code, 400)
