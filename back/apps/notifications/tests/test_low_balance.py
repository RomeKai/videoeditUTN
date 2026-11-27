"""
The "saldo bajo" notice (``wallet_service.track_low_balance`` and
``services.notify_low_balance``): a reservation that leaves the available
balance under the plan's threshold tells who can buy tokens, once, until the
balance is back at the threshold.
"""
import logging
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.db import connection, transaction

from apps.notifications.models import Notification
from apps.notifications.services import low_balance_message
from apps.payments.models import LowBalanceNotice, Payment, Wallet
from apps.payments.services.estimate import low_balance_threshold
from apps.payments.services.payments import settle_payment
from apps.payments.services.wallet_service import (
    commit_partial_reservation, commit_reservation, reserve_funds, rollback_reservation,
)
from apps.users.models import NotificationPreference, User
from apps.videos.tests.test_ingestion_pipeline import fund_wallet

from .helpers import ADMIN, broken_bell, join, make_user, sent
from .test_events import EventHarness

BODY = 'El saldo quedó por debajo de lo que cuesta un video promedio.'


class LowBalanceTests(EventHarness):
    """The team of EventHarness plus ``ada``, an admin. The threshold of its plan is 600 tokens."""

    def setUp(self):
        super().setUp()
        self.ada = make_user('ada')
        join(self.workspace, self.ada, ADMIN)
        User.objects.update(is_email_verified=True)
        self.wallet = fund_wallet(self.workspace, '1000.00')
        self.workspace.subscription_plan.refresh_from_db()
        self.assertEqual(low_balance_threshold(self.workspace.subscription_plan), Decimal('600.00'))

    def spend(self, amount):
        return self.committed(lambda: reserve_funds(self.wallet.id, Decimal(amount)))

    def notices(self):
        return sorted((row.title, row.user.username) for row in Notification.objects.select_related('user').order_by('created_at', 'pk'))

    def marked(self):
        return LowBalanceNotice.objects.filter(wallet=self.wallet).exists()

    def test_a_reservation_that_leaves_the_balance_under_the_threshold_tells_who_can_buy(self):
        self.spend('400.01')

        expected = ('low_balance', 'Te quedan 599,99 tokens', BODY)
        self.assertEqual(sent(), {'ana': [expected], 'ada': [expected]})
        self.assertEqual(set(Notification.objects.values_list('project_id', 'post_id')), {(None, None)})
        self.assertTrue(self.marked())

    def test_at_the_threshold_or_above_nobody_is_told(self):
        self.spend('400.00')

        self.assertEqual(sent(), {})
        self.assertFalse(self.marked())

    def test_it_is_sent_once_while_the_balance_stays_low(self):
        self.spend('500')
        self.spend('100')
        txn = self.spend('100')
        self.committed(lambda: commit_reservation(txn.id))

        self.assertEqual(self.notices(), [('Te quedan 500 tokens', 'ada'), ('Te quedan 500 tokens', 'ana')])

    def test_a_refund_back_to_the_threshold_arms_it_again(self):
        txn = self.spend('500')
        self.committed(lambda: rollback_reservation(txn.id))
        self.assertFalse(self.marked())

        self.spend('450')

        self.assertEqual(self.notices(), [
            ('Te quedan 500 tokens', 'ada'), ('Te quedan 500 tokens', 'ana'),
            ('Te quedan 550 tokens', 'ada'), ('Te quedan 550 tokens', 'ana'),
        ])

    def test_a_refund_that_leaves_the_balance_low_does_not_arm_it(self):
        self.spend('500')
        txn = self.spend('50')
        self.committed(lambda: rollback_reservation(txn.id))

        self.spend('10')

        self.assertEqual(len(self.notices()), 2)
        self.assertTrue(self.marked())

    def test_a_partial_charge_that_returns_enough_arms_it_again(self):
        txn = self.spend('500')
        self.committed(lambda: commit_partial_reservation(txn.id, Decimal('100')))
        self.assertFalse(self.marked())

        self.spend('400')

        self.assertEqual(len(self.notices()), 4)

    def test_a_purchase_arms_it_again(self):
        self.spend('500')
        payment = Payment.objects.create(
            workspace=self.workspace, created_by=self.ana, tokens=200, amount_usd=Decimal('2.00'), provider='simulated',
        )
        self.committed(lambda: settle_payment(payment.pk, approved=True))
        self.assertFalse(self.marked())
        Notification.objects.all().delete()

        self.spend('200')

        self.assertEqual(self.notices(), [('Te quedan 500 tokens', 'ada'), ('Te quedan 500 tokens', 'ana')])

    def test_a_reservation_that_is_rolled_back_tells_nobody_and_stays_armed(self):
        self.rolled_back(lambda: reserve_funds(self.wallet.id, Decimal('500')))

        self.assertEqual(sent(), {})
        self.assertFalse(self.marked())
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('1000'))

    def test_a_fall_that_is_refunded_before_the_commit_tells_nobody(self):
        def reserve_and_refund():
            with transaction.atomic():
                rollback_reservation(reserve_funds(self.wallet.id, Decimal('500')).id)

        self.committed(reserve_and_refund)

        self.assertEqual(sent(), {})
        self.assertFalse(self.marked())

    def test_the_email_goes_to_who_has_it_on(self):
        NotificationPreference.objects.create(user=self.ada, low_balance=False)

        self.spend('500')

        self.assertEqual([message.to for message in mail.outbox], [['ana@test.com']])
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'Te queda poco saldo en OneCreator')
        self.assertIn('Te quedan 500 tokens', message.body)
        self.assertIn('/uso/comprar', message.body)
        self.assertEqual(set(sent()), {'ana', 'ada'})

    def test_a_notice_that_fails_does_not_stop_the_reservation(self):
        with broken_bell(), self.assertLogs('apps.notifications.services', logging.ERROR):
            txn = self.spend('500')

        self.assertEqual(txn.status, 'RESERVED')
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('500'))

    def test_a_tracking_that_fails_does_not_stop_the_reservation(self):
        with patch('apps.notifications.services.notify_low_balance', side_effect=RuntimeError('texto')), \
                self.assertLogs('apps.payments.services.wallet_service', logging.ERROR) as logs:
            txn = self.spend('500')

        self.assertEqual(txn.status, 'RESERVED')
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('500'))
        self.assertIn(f'wallet.low_balance_not_tracked wallet={self.wallet.pk} error=RuntimeError', logs.output[0])
        self.assertNotIn('texto', logs.output[0])

    def test_a_database_error_in_the_tracking_does_not_undo_the_reservation(self):
        def refused_query(*args):
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM una_tabla_que_no_existe')

        with patch('apps.payments.services.estimate.low_balance_threshold', side_effect=refused_query), \
                self.assertLogs('apps.payments.services.wallet_service', logging.ERROR) as logs:
            txn = self.spend('500')

        self.assertIn('error=ProgrammingError', logs.output[0])
        txn.refresh_from_db()
        self.assertEqual(txn.status, 'RESERVED')
        self.assertEqual(Wallet.objects.get(pk=self.wallet.pk).available_balance, Decimal('500'))

    def test_the_balance_in_the_text_is_never_rounded_up(self):
        self.assertEqual(low_balance_message(Decimal('599.999'))[0], 'Te quedan 599,99 tokens')
        self.assertEqual(low_balance_message(Decimal('1.004'))[0], 'Te queda 1 token')
        self.assertEqual(low_balance_message(Decimal('0'))[0], 'Te quedan 0 tokens')
