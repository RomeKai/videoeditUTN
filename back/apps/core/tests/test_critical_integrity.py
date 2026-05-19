import threading
import uuid
from decimal import Decimal
from django.test import TransactionTestCase
from django.db import transaction, connection
from django.contrib.auth import get_user_model
from apps.users.models import Workspace
from apps.payments.models import Wallet, Transaction
from apps.payments.services.wallet_service import reserve_funds, commit_reservation
from concurrent.futures import ThreadPoolExecutor

User = get_user_model()

class FinancialIntegrityTests(TransactionTestCase):
    """
    Critical tests for FinOps: Race conditions and atomicity.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='finadmin', email='fin@test.com', password='password')
        # Workspace signal already creates the Wallet
        self.workspace = Workspace.objects.create(name="Fin Workspace", owner=self.user)
        self.wallet = Wallet.objects.get(workspace=self.workspace)
        self.wallet.available_balance = Decimal('100.0000000000')
        self.wallet.save()

    def test_concurrent_balance_deduction(self):
        """
        STRESS TEST: Simulate 10 concurrent requests trying to spend 10 coins each.
        Original balance: 100. Final should be 0, and exactly 10 transactions created.
        """
        amount_to_spend = Decimal('10.0000000000')
        num_threads = 10
        
        def attempt_spend():
            # We must close the connection in each thread for Django's TransactionTestCase
            connection.close() 
            try:
                reserve_funds(self.wallet.id, amount_to_spend, user=self.user, description="Concurrent test")
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            results = list(executor.map(lambda _: attempt_spend(), range(num_threads)))

        self.wallet.refresh_from_db()
        success_count = sum(1 for r in results if r)
        
        # In a perfect world with select_for_update, all 10 should succeed if balance allows
        # and balance should be exactly 0.
        self.assertEqual(success_count, 10)
        self.assertEqual(self.wallet.available_balance, Decimal('0.0000000000'))
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet).count(), 10)

    def test_overspending_prevention(self):
        """
        CRITICAL: Ensure a workspace cannot spend more than it has.
        """
        huge_amount = Decimal('101.0000000000')
        with self.assertRaises(Exception): # Should raise InsufficientFundsError
             reserve_funds(self.wallet.id, huge_amount, user=self.user)
        
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal('100.0000000000'))

class TaskAtomicityTests(TransactionTestCase):
    """
    Tests if system remains consistent if a task fails halfway.
    """
    def setUp(self):
        self.user = User.objects.create_user(username='taskuser', email='task@test.com', password='password')
        self.workspace = Workspace.objects.create(name="Task Workspace", owner=self.user)
        self.wallet = Wallet.objects.get(workspace=self.workspace)
        self.wallet.available_balance = Decimal('50.00')
        self.wallet.save()

    def test_rollback_on_failed_operation(self):
        """
        If reserve_funds works but the next step fails, funds must not be lost.
        """
        from apps.payments.services.wallet_service import rollback_reservation
        
        # 1. Reserve 20
        txn = reserve_funds(self.wallet.id, Decimal('20.00'), user=self.user)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal('30.00'))
        self.assertEqual(self.wallet.reserved_balance, Decimal('20.00'))

        # 2. Simulate failure and rollback
        rollback_reservation(txn.id)
        
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal('50.00'))
        self.assertEqual(self.wallet.reserved_balance, Decimal('0.00'))
        
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
