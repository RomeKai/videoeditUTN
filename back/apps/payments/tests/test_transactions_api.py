"""
The wallet's movements (front contract billing.transactions and
billing.exportTransactions), for Dueño and Admin only:

- Each row says what it did to the balance (``kind``): a reservation that
  was returned is ``released`` ("Devuelto") and keeps its project.
- Filters by kind, by month in the workspace's zone and by project; ``totals``
  counts every kind under the same month and project, ignoring kind and page.
- The CSV has the same rows, with the sign of what each did to the balance.
- Three queries for a page whatever its size; another workspace's rows never show.
"""
import csv
import io
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.core.permissions import ADMINS, DENIED
from apps.payments.models import Transaction
from apps.payments.tests.test_wallet_api import (
    COMPLETED, DEPOSIT, FAILED, PENDING, RENDER, REFUND, RESERVED, USAGE, WalletHarness, local, movement,
)
from apps.videos.tests.test_active_workspace import ADMIN, EDITOR, VIEWER, header

TRANSACTIONS = reverse('transaction-list')
EXPORT = reverse('transaction-export')


class MovementsHarness(WalletHarness):
    def setUp(self):
        super().setUp()
        self.carla = self.alice
        self.carla.first_name, self.carla.last_name = 'Carla', 'Gómez'
        self.carla.save(update_fields=['first_name', 'last_name'])
        self.interview = self.project(title='Entrevista con Ana')
        self.podcast = self.project(title='Podcast 12')
        october = local(2026, 10, 10)
        self.purchase = movement(self.home, '5000', DEPOSIT, at=october, description='Compra de 5000 tokens', user=self.carla)
        self.charged = movement(
            self.home, '12.5', at=local(2026, 10, 11), project=self.interview, user=self.carla,
            description='Procesamiento: Entrevista con Ana',
        )
        self.released = movement(
            self.home, '7.25', status=FAILED, at=local(2026, 10, 12), project=self.podcast, category=RENDER,
            description='Render Final (Paper Edit): Podcast 12',
        )
        self.reserved = movement(
            self.home, '3.1234567891', status=RESERVED, at=local(2026, 11, 2), project=self.interview, category=RENDER,
            description='Render de clip: Gancho',
        )
        self.pending = movement(self.home, '1000', DEPOSIT, PENDING, at=local(2026, 11, 3), description='Compra de 1000 tokens')
        self.refund = movement(self.home, '2', REFUND, at=local(2026, 11, 4), project=self.podcast, description='Devolución')
        # Never a movement: a purchase that failed did nothing to the balance.
        movement(self.home, '99', DEPOSIT, FAILED, at=local(2026, 11, 5))
        # Another workspace's.
        movement(self.team, '444', at=local(2026, 11, 1))


class TransactionListTests(MovementsHarness, APITestCase):
    def test_every_movement_newest_first_with_its_kind_and_project(self):
        response = self.get(TRANSACTIONS)

        self.assertEqual(response.status_code, 200, response.content[:300])
        data = response.json()
        self.assertEqual((data['count'], data['next'], data['previous']), (6, None, None))
        self.assertEqual(
            [(row['id'], row['kind']) for row in data['results']],
            [
                (str(self.refund.pk), 'refund'), (str(self.pending.pk), 'pending'), (str(self.reserved.pk), 'reserved'),
                (str(self.released.pk), 'released'), (str(self.charged.pk), 'charged'), (str(self.purchase.pk), 'purchase'),
            ],
        )
        released = data['results'][3]
        self.assertEqual(released, {
            'id': str(self.released.pk),
            'wallet': str(self.released.wallet_id),
            'created_by': None,
            'amount': '7.2500000000',
            'transaction_type': 'SPEND',
            'status': 'FAILED',
            'description': 'Render Final (Paper Edit): Podcast 12',
            'created_at': released['created_at'],
            'kind': 'released',
            'created_by_detail': None,
            'project': {'id': str(self.podcast.pk), 'title': 'Podcast 12'},
            'category': 'render',
        })
        self.assertEqual(released['created_at'], '2026-10-12T15:00:00Z')
        charged = data['results'][4]
        # The name, never the email.
        self.assertEqual(charged['created_by_detail'], {'id': str(self.carla.pk), 'name': 'Carla Gómez'})
        self.assertEqual(charged['created_by'], str(self.carla.pk))
        self.assertEqual(data['results'][2]['amount'], '3.1234567891')
        self.assertIsNone(data['results'][5]['project'])
        self.assertEqual(data['results'][5]['category'], 'purchase')

    def test_totals_per_kind_ignore_the_kind_filter_and_the_page(self):
        response = self.get(TRANSACTIONS, kind='charged', page_size=1)

        data = response.json()
        self.assertEqual([row['id'] for row in data['results']], [str(self.charged.pk)])
        self.assertEqual(data['totals'], {
            'purchase': {'count': 1, 'amount': '5000.0000000000'},
            'charged': {'count': 1, 'amount': '12.5000000000'},
            'released': {'count': 1, 'amount': '7.2500000000'},
            'reserved': {'count': 1, 'amount': '3.1234567891'},
            'pending': {'count': 1, 'amount': '1000.0000000000'},
            'refund': {'count': 1, 'amount': '2.0000000000'},
        })

    def test_month_is_the_one_of_the_workspace_zone(self):
        # 01:30 UTC of November 1st is October 31st, 22:30, in Buenos Aires.
        edge = movement(self.home, '1', at=datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo('UTC')))

        october = self.get(TRANSACTIONS, month='2026-10').json()
        november = self.get(TRANSACTIONS, month='2026-11').json()

        self.assertEqual(
            [row['id'] for row in october['results']],
            [str(edge.pk), str(self.released.pk), str(self.charged.pk), str(self.purchase.pk)],
        )
        self.assertEqual(set(october['totals']), {'charged', 'released', 'purchase'})
        self.assertEqual(october['totals']['charged'], {'count': 2, 'amount': '13.5000000000'})
        self.assertEqual(
            [row['id'] for row in november['results']], [str(self.refund.pk), str(self.pending.pk), str(self.reserved.pk)],
        )

    def test_project_filter_with_its_totals(self):
        data = self.get(TRANSACTIONS, project=str(self.podcast.pk)).json()

        self.assertEqual([row['id'] for row in data['results']], [str(self.refund.pk), str(self.released.pk)])
        self.assertEqual(data['totals'], {
            'released': {'count': 1, 'amount': '7.2500000000'}, 'refund': {'count': 1, 'amount': '2.0000000000'},
        })

    def test_kind_filter(self):
        for kind, expected in (('released', self.released), ('purchase', self.purchase), ('pending', self.pending)):
            with self.subTest(kind=kind):
                data = self.get(TRANSACTIONS, kind=kind).json()
                self.assertEqual([row['id'] for row in data['results']], [str(expected.pk)])

    def test_pages(self):
        first = self.get(TRANSACTIONS, page_size=4).json()
        second = self.get(TRANSACTIONS, page_size=4, page=2).json()

        self.assertEqual((first['count'], len(first['results']), second['previous'] is not None), (6, 4, True))
        self.assertIn('page=2', first['next'])
        self.assertEqual(len(second['results']), 2)
        self.assertIsNone(second['next'])
        self.assertEqual(self.get(TRANSACTIONS, page=9).status_code, 404)
        self.assertEqual(len(self.get(TRANSACTIONS, page_size=500).json()['results']), 6)  # capped at 100

    def test_a_deleted_project_leaves_its_movements_without_link(self):
        self.podcast.delete()

        data = self.get(TRANSACTIONS, kind='released').json()

        self.assertIsNone(data['results'][0]['project'])
        self.assertEqual(data['results'][0]['description'], 'Render Final (Paper Edit): Podcast 12')

    def test_wrong_filters_are_a_400(self):
        for query in ({'kind': 'devuelto'}, {'month': '2026-13'}, {'project': 'abc'}):
            with self.subTest(query=query):
                self.assertEqual(self.get(TRANSACTIONS, **query).status_code, 400)

    def test_only_owner_and_admin_even_for_one_project(self):
        movement(self.team, '1', project=self.project(self.team, 'Del equipo'))
        for role, code in ((VIEWER, 403), (EDITOR, 403), (ADMIN, 200)):
            team_project = self.team.projects.get()
            with self.subTest(role=role):
                self.as_member(role)
                for query in ({}, {'project': str(team_project.pk)}):
                    response = self.get(TRANSACTIONS, self.team, **query)
                    self.assertEqual(response.status_code, code)
                    if code == 403:
                        self.assertEqual(response.json(), {'detail': DENIED[ADMINS]})

    def test_another_workspace_is_never_shown(self):
        self.assertEqual(self.get(TRANSACTIONS, self.team).status_code, 404)
        # Its project as a filter of mine finds nothing.
        team_project = self.project(self.team, 'Ajeno')
        movement(self.team, '5', project=team_project)
        data = self.get(TRANSACTIONS, project=str(team_project.pk)).json()
        self.assertEqual((data['count'], data['totals']), (0, {}))

    def test_three_queries_for_the_page_whatever_its_size(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.get(TRANSACTIONS, page_size=100).status_code, 200)
            return len(queries)

        few = count()
        for number in range(30):
            project = self.project(title=f'Proyecto {number}')
            movement(self.home, '1', at=local(2026, 9, 1 + number % 28), project=project, user=self.carla)

        self.assertEqual(count(), few)


class TransactionExportTests(MovementsHarness, APITestCase):
    def rows(self, workspace=None, **query):
        response = self.get(EXPORT, workspace, **query)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        return list(csv.reader(io.StringIO(response.content.decode())))

    def test_the_sign_is_what_it_did_to_the_balance(self):
        rows = self.rows()

        self.assertEqual(rows[0], ['fecha', 'concepto', 'proyecto', 'tipo', 'monto'])
        self.assertEqual(rows[1:], [
            ['2026-11-04T12:00:00-03:00', 'Devolución', 'Podcast 12', 'Devolución', '2.00'],
            ['2026-11-03T12:00:00-03:00', 'Compra de 1000 tokens', '', 'Pendiente', '1000.00'],
            ['2026-11-02T12:00:00-03:00', 'Render de clip: Gancho', 'Entrevista con Ana', 'Reservado', '-3.1234567891'],
            ['2026-10-12T12:00:00-03:00', 'Render Final (Paper Edit): Podcast 12', 'Podcast 12', 'Devuelto', '7.25'],
            ['2026-10-11T12:00:00-03:00', 'Procesamiento: Entrevista con Ana', 'Entrevista con Ana', 'Cobrado', '-12.50'],
            ['2026-10-10T12:00:00-03:00', 'Compra de 5000 tokens', '', 'Compra', '5000.00'],
        ])

    def test_the_same_filters_as_the_list(self):
        self.assertEqual([row[3] for row in self.rows(month='2026-10')[1:]], ['Devuelto', 'Cobrado', 'Compra'])
        self.assertEqual([row[3] for row in self.rows(kind='released')[1:]], ['Devuelto'])
        self.assertEqual([row[3] for row in self.rows(project=str(self.interview.pk))[1:]], ['Reservado', 'Cobrado'])
        self.assertEqual(self.get(EXPORT, month='octubre').status_code, 400)

    def test_a_title_a_spreadsheet_would_run_is_written_as_text(self):
        project = self.project(title='=HYPERLINK("http://x")')
        movement(self.home, '1', at=local(2026, 12, 1), project=project, description='=1+1')

        row = self.rows(month='2026-12')[1]

        self.assertEqual((row[1], row[2]), ("'=1+1", '\'=HYPERLINK("http://x")'))

    def test_only_owner_and_admin(self):
        for role, code in ((VIEWER, 403), (EDITOR, 403), (ADMIN, 200)):
            with self.subTest(role=role):
                self.as_member(role)
                self.assertEqual(self.get(EXPORT, self.team).status_code, code)
        self.assertEqual(self.rows(self.team)[1:], [['2026-11-01T12:00:00-03:00', 'Movimiento', '', 'Cobrado', '-444.00']])

    def test_queries_do_not_grow_with_the_rows(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.rows()
            return len(queries)

        few = count()
        for number in range(20):
            movement(self.home, '1', project=self.project(title=f'P{number}'), user=self.carla)

        self.assertEqual(count(), few)


class HeadRequestTests(MovementsHarness, APITestCase):
    """A HEAD is a GET without body: the same 403, and the export is never built."""

    def test_viewer_and_editor_get_the_403_of_get_on_head(self):
        for role in (VIEWER, EDITOR):
            self.as_member(role)
            for url in (TRANSACTIONS, EXPORT, USAGE):
                with self.subTest(role=role, url=url), patch('apps.payments.views.movements.csv_text') as csv_text:
                    response = self.client.head(url, **header(self.team))
                    self.assertEqual(response.status_code, 403)
                    csv_text.assert_not_called()

    def test_admin_gets_the_200_of_get(self):
        self.as_member(ADMIN)
        for url in (TRANSACTIONS, EXPORT, USAGE):
            with self.subTest(url=url):
                self.assertEqual(self.client.head(url, **header(self.team)).status_code, 200)
