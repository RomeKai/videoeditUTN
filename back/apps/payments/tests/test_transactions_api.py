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
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.core.permissions import ADMINS, DENIED
from apps.payments.models import Transaction, Wallet
from apps.payments.services import wallet_service
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
            'reserved_amount': None,
            'transaction_type': 'SPEND',
            'status': 'FAILED',
            'description': 'Render Final (Paper Edit): Podcast 12',
            'created_at': released['created_at'],
            'settled_at': None,
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
            'released': {'count': 1, 'amount': '7.2500000000', 'partial_count': 0},
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
            'released': {'count': 1, 'amount': '7.2500000000', 'partial_count': 0}, 'refund': {'count': 1, 'amount': '2.0000000000'},
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

    def test_the_movements_are_read_by_wallet_id_with_the_index_order(self):
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.get(TRANSACTIONS).status_code, 200)

        reads = [q['sql'] for q in queries.captured_queries if 'FROM "payments_transaction"' in q['sql']]
        self.assertEqual(len(reads), 3)  # totals, count, page
        for sql in reads:
            # By the wallet's id (the index of 0015 starts with it), never through a join to the wallet.
            self.assertNotIn('JOIN "payments_wallet"', sql)
            self.assertIn('"payments_transaction"."wallet_id" =', sql)
        # Ordered by the expression of the index (Django orders by its position in the SELECT).
        self.assertIn('COALESCE("payments_transaction"."settled_at", "payments_transaction"."created_at") AS "moved_at"', reads[-1])
        self.assertRegex(reads[-1], r'ORDER BY \d+ DESC, "payments_transaction"\."id" DESC')

    def test_the_same_queries_for_the_page_whatever_its_size(self):
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

    def test_one_row_per_change_of_the_balance_with_its_sign(self):
        rows = self.rows()

        self.assertEqual(rows[0], ['fecha', 'concepto', 'proyecto', 'tipo', 'monto', 'estado', 'transaccion'])
        self.assertEqual([row[:6] for row in rows[1:]], [
            ['2026-11-04T12:00:00-03:00', 'Devolución', 'Podcast 12', 'Devolución', '2.00', 'Devolución'],
            # The pending purchase has not moved the balance: no row.
            ['2026-11-02T12:00:00-03:00', 'Render de clip: Gancho', 'Entrevista con Ana', 'Reserva', '-3.1234567891', 'Reservado'],
            # A reservation returned whole: what it took and what came back.
            ['2026-10-12T12:00:00-03:00', 'Render Final (Paper Edit): Podcast 12', 'Podcast 12', 'Devuelto', '7.25', 'Devuelto'],
            ['2026-10-12T12:00:00-03:00', 'Render Final (Paper Edit): Podcast 12', 'Podcast 12', 'Reserva', '-7.25', 'Devuelto'],
            ['2026-10-11T12:00:00-03:00', 'Procesamiento: Entrevista con Ana', 'Entrevista con Ana', 'Reserva', '-12.50', 'Cobrado'],
            ['2026-10-10T12:00:00-03:00', 'Compra de 5000 tokens', '', 'Compra', '5000.00', 'Compra'],
        ])
        self.assertEqual(rows[3][6], str(self.released.pk))
        self.assertEqual(rows[3][6], rows[4][6])

    def test_the_same_filters_as_the_list(self):
        self.assertEqual([row[3] for row in self.rows(month='2026-10')[1:]], ['Devuelto', 'Reserva', 'Reserva', 'Compra'])
        self.assertEqual([row[3] for row in self.rows(kind='released')[1:]], ['Devuelto', 'Reserva'])
        self.assertEqual([row[5] for row in self.rows(project=str(self.interview.pk))[1:]], ['Reservado', 'Cobrado'])
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
        self.assertEqual([row[:6] for row in self.rows(self.team)[1:]], [
            ['2026-11-01T12:00:00-03:00', 'Movimiento', '', 'Reserva', '-444.00', 'Cobrado'],
        ])

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


class ReconciliationTests(WalletHarness, APITestCase):
    """
    The CSV adds up to what the balance did, and the totals to what was
    reserved: a reservation returned whole, a partial charge, a whole charge,
    an open reservation and a purchase.
    """

    def setUp(self):
        super().setUp()
        self.wallet = Wallet.objects.get(workspace=self.home)
        self.project_ = self.project(title='Entrevista')

    def available(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance

    def reserve(self, amount):
        return wallet_service.reserve_funds(
            self.wallet.id, Decimal(amount), project=self.project_, category=RENDER, description='Render',
        )

    def at(self, txn, created, settled=None):
        Transaction.objects.filter(pk=txn.pk).update(created_at=created, settled_at=settled)

    def csv_sum(self, **query):
        response = self.get(EXPORT, **query)
        rows = list(csv.reader(io.StringIO(response.content.decode())))[1:]
        return sum((Decimal(row[4]) for row in rows), Decimal('0'))

    def scenario(self):
        before = self.available()
        released = self.reserve('10')
        wallet_service.rollback_reservation(released.id)
        partial = self.reserve('20')
        wallet_service.commit_partial_reservation(partial.id, Decimal('5'))
        whole = self.reserve('7')
        wallet_service.commit_reservation(whole.id)
        self.reserve('3')
        # A purchase, as the webhook will credit it (PAY-02).
        movement(self.home, '50', DEPOSIT)
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=F('available_balance') + Decimal('50'))
        return before, {'released': released, 'partial': partial, 'whole': whole}

    def test_the_csv_adds_up_to_the_change_of_the_balance(self):
        before, _ = self.scenario()

        delta = self.available() - before

        self.assertEqual(delta, Decimal('35'))  # -10 +10, -20 +15, -7, -3, +50
        self.assertEqual(self.csv_sum(), delta)

    def test_the_totals_add_up_too(self):
        before, _ = self.scenario()

        totals = self.get(TRANSACTIONS).json()['totals']
        amount = {kind: Decimal(entry['amount']) for kind, entry in totals.items()}

        self.assertEqual(totals['released'], {'count': 1, 'amount': '25.0000000000', 'partial_count': 1})
        self.assertEqual((amount['charged'], amount['reserved'], amount['purchase']), (Decimal('12'), Decimal('3'), Decimal('50')))
        # Everything reserved was charged, returned or is still reserved.
        self.assertEqual(amount['charged'] + amount['released'] + amount['reserved'], Decimal('40'))
        self.assertEqual(amount['purchase'] - amount['charged'] - amount['reserved'], self.available() - before)

    def test_a_partial_charge_shows_what_it_reserved_and_when_it_was_settled(self):
        _, made = self.scenario()

        row = next(item for item in self.get(TRANSACTIONS).json()['results'] if item['id'] == str(made['partial'].pk))

        self.assertEqual((row['kind'], row['amount'], row['reserved_amount']), ('charged', '5.0000000000', '20.0000000000'))
        self.assertIsNotNone(row['settled_at'])

    def test_each_month_adds_up_to_what_the_balance_did_in_it(self):
        before, made = self.scenario()
        # Reserved on October 31st at 23:00 in Buenos Aires, returned or charged on November 1st at 01:00.
        october_31, november_1 = local(2026, 10, 31, 23), local(2026, 11, 1, 1)
        for txn in made.values():
            self.at(txn, october_31, november_1)
        Transaction.objects.filter(wallet=self.wallet).exclude(pk__in=[t.pk for t in made.values()]).update(
            created_at=local(2026, 11, 2),
        )

        october, november = self.csv_sum(month='2026-10'), self.csv_sum(month='2026-11')

        self.assertEqual(october, Decimal('-37'))  # the three reservations
        self.assertEqual(november, Decimal('72'))  # their returns (+25), the open one (-3) and the purchase (+50)
        self.assertEqual(october + november, self.available() - before)


class ChargeMonthTests(WalletHarness, APITestCase):
    """A charge is in the month it was charged: in the movements, their totals, the CSV and the usage chart."""

    def test_reserved_on_october_31_and_charged_on_november_1_is_a_november_charge(self):
        wallet = Wallet.objects.get(workspace=self.home)
        txn = wallet_service.reserve_funds(wallet.id, Decimal('8'), project=self.project(), category=RENDER)
        wallet_service.commit_reservation(txn.id)
        Transaction.objects.filter(pk=txn.pk).update(created_at=local(2026, 10, 31, 23), settled_at=local(2026, 11, 1, 1))

        october = self.get(TRANSACTIONS, month='2026-10', kind='charged').json()
        november = self.get(TRANSACTIONS, month='2026-11', kind='charged').json()

        self.assertEqual((october['count'], october['totals']), (0, {}))
        self.assertEqual([row['id'] for row in november['results']], [str(txn.pk)])
        self.assertEqual(november['totals']['charged'], {'count': 1, 'amount': '8.0000000000'})
        usage = {day['date']: day['renders'] for day in self.get(USAGE, month='2026-11').json()}
        self.assertEqual(usage['2026-11-01'], '8.0000000000')
        # The CSV dates each change: the reservation in October, nothing more in November (it was charged whole).
        self.assertEqual(
            [row[:5] for row in csv.reader(io.StringIO(self.get(EXPORT, month='2026-10').content.decode()))][1:],
            [['2026-10-31T23:00:00-03:00', '', 'Entrevista con Ana', 'Reserva', '-8.00']],
        )

    def test_the_list_is_ordered_by_when_each_row_moved_the_balance(self):
        wallet = Wallet.objects.get(workspace=self.home)
        old_reservation = wallet_service.reserve_funds(wallet.id, Decimal('1'))
        wallet_service.commit_reservation(old_reservation.id)
        Transaction.objects.filter(pk=old_reservation.pk).update(created_at=local(2026, 10, 1), settled_at=local(2026, 10, 20))
        newer = movement(self.home, '2', status=RESERVED, at=local(2026, 10, 10))

        ids = [row['id'] for row in self.get(TRANSACTIONS, month='2026-10').json()['results']]

        self.assertEqual(ids, [str(old_reservation.pk), str(newer.pk)])
