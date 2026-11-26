"""
CORE-06 · WalletService: reserve + commit, reserve + rollback, fondos insuficientes
y reservas concurrentes (race condition).

Los tests de concurrencia necesitan PostgreSQL (SELECT ... FOR UPDATE real),
que es lo que usa backend.settings.ci.
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import connection

from apps.payments.models import Transaction, Wallet
from apps.payments.services.wallet_service import (
    InsufficientFundsError,
    PaymentError,
    commit_reservation,
    reserve_funds,
    rollback_reservation,
)

pytestmark = pytest.mark.django_db


def balances(wallet):
    wallet.refresh_from_db()
    return wallet.available_balance, wallet.reserved_balance


# --- reserve -------------------------------------------------------------------

def test_reserve_moves_funds_from_available_to_reserved(wallet_factory, user):
    wallet = wallet_factory("100.00")

    txn = reserve_funds(wallet.id, Decimal("30.00"), user=user, description="render")

    assert balances(wallet) == (Decimal("70.00"), Decimal("30.00"))
    assert txn.status == Transaction.Status.RESERVED
    assert txn.transaction_type == Transaction.Type.SPEND
    assert txn.amount == Decimal("30.00")
    assert txn.created_by == user


def test_reserve_exact_available_balance_is_allowed(wallet_factory):
    wallet = wallet_factory("50.00")
    reserve_funds(wallet.id, Decimal("50.00"))
    assert balances(wallet) == (Decimal("0"), Decimal("50.00"))


def test_insufficient_funds_raises_and_changes_nothing(wallet_factory):
    wallet = wallet_factory("10.00")

    with pytest.raises(InsufficientFundsError):
        reserve_funds(wallet.id, Decimal("10.01"))

    assert balances(wallet) == (Decimal("10.00"), Decimal("0"))
    assert not Transaction.objects.filter(wallet=wallet).exists()


def test_reserve_on_unknown_wallet_raises_payment_error(db):
    with pytest.raises(PaymentError):
        reserve_funds(uuid.uuid4(), Decimal("1.00"))


def test_reserve_with_a_project_id_only_reserves_and_never_queues_an_ingestion(wallet_factory):
    """
    A project's creation queues its own ingestion once its reservation
    commits: a reservation that queued one too would start a second
    ingestion of the same project. ``project_id`` is still accepted, by name
    or in its old position, and ignored.
    """
    from unittest.mock import patch

    from apps.videos.tasks import process_initial_ingestion, process_video_pipeline

    wallet = wallet_factory("100.00")
    project_id = uuid.uuid4()

    with patch.object(process_video_pipeline, "delay") as pipeline, \
            patch.object(process_video_pipeline, "apply_async") as pipeline_async, \
            patch.object(process_initial_ingestion, "delay") as ingestion, \
            patch.object(process_initial_ingestion, "apply_async") as ingestion_async:
        by_name = reserve_funds(wallet.id, Decimal("10.00"), project_id=project_id)
        by_position = reserve_funds(wallet.id, Decimal("5.00"), project_id)

    for queued in (pipeline, pipeline_async, ingestion, ingestion_async):
        queued.assert_not_called()
    assert {by_name.status, by_position.status} == {Transaction.Status.RESERVED}
    assert balances(wallet) == (Decimal("85.00"), Decimal("15.00"))


# --- reserve + commit ------------------------------------------------------------

def test_commit_clears_reserved_and_completes_transaction(wallet_factory):
    wallet = wallet_factory("100.00")
    txn = reserve_funds(wallet.id, Decimal("40.00"))

    commit_reservation(txn.id)

    assert balances(wallet) == (Decimal("60.00"), Decimal("0"))
    txn.refresh_from_db()
    assert txn.status == Transaction.Status.COMPLETED


def test_commit_twice_is_rejected(wallet_factory):
    wallet = wallet_factory("100.00")
    txn = reserve_funds(wallet.id, Decimal("40.00"))
    commit_reservation(txn.id)

    with pytest.raises(PaymentError):
        commit_reservation(txn.id)

    assert balances(wallet) == (Decimal("60.00"), Decimal("0"))


def test_commit_unknown_transaction_raises(db):
    with pytest.raises(PaymentError):
        commit_reservation(uuid.uuid4())


# --- reserve + rollback -------------------------------------------------------------

def test_rollback_returns_funds_and_marks_failed(wallet_factory):
    wallet = wallet_factory("100.00")
    txn = reserve_funds(wallet.id, Decimal("25.00"))

    rollback_reservation(txn.id)

    assert balances(wallet) == (Decimal("100.00"), Decimal("0"))
    txn.refresh_from_db()
    assert txn.status == Transaction.Status.FAILED


def test_rollback_after_commit_is_rejected(wallet_factory):
    wallet = wallet_factory("100.00")
    txn = reserve_funds(wallet.id, Decimal("25.00"))
    commit_reservation(txn.id)

    with pytest.raises(PaymentError):
        rollback_reservation(txn.id)

    assert balances(wallet) == (Decimal("75.00"), Decimal("0"))


def test_commit_after_rollback_is_rejected(wallet_factory):
    wallet = wallet_factory("100.00")
    txn = reserve_funds(wallet.id, Decimal("25.00"))
    rollback_reservation(txn.id)

    with pytest.raises(PaymentError):
        commit_reservation(txn.id)

    assert balances(wallet) == (Decimal("100.00"), Decimal("0"))


def test_mixed_operations_keep_total_balance_consistent(wallet_factory):
    """available + reserved + gastado siempre suma el saldo inicial."""
    wallet = wallet_factory("100.00")
    t1 = reserve_funds(wallet.id, Decimal("10.00"))
    t2 = reserve_funds(wallet.id, Decimal("20.00"))
    t3 = reserve_funds(wallet.id, Decimal("30.00"))
    commit_reservation(t1.id)
    rollback_reservation(t2.id)

    available, reserved = balances(wallet)
    spent = Decimal("10.00")
    assert available == Decimal("60.00")
    assert reserved == t3.amount
    assert available + reserved + spent == Decimal("100.00")


# --- concurrencia (solo PostgreSQL) -------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_concurrent_reserves_never_overspend(wallet_factory):
    """
    12 threads intentan reservar 15 cada uno sobre un saldo de 100.
    Con select_for_update solo 6 pueden ganar (90) y el saldo nunca queda negativo.
    """
    if connection.vendor != "postgresql":
        pytest.skip("SELECT FOR UPDATE requiere PostgreSQL")

    wallet = wallet_factory("100.00")
    wallet_id = wallet.id

    def attempt():
        try:
            reserve_funds(wallet_id, Decimal("15.00"))
            return True
        except InsufficientFundsError:
            return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: attempt(), range(12)))

    wallet = Wallet.objects.get(pk=wallet_id)
    assert sum(results) == 6
    assert wallet.available_balance == Decimal("10.00")
    assert wallet.reserved_balance == Decimal("90.00")
    assert Transaction.objects.filter(wallet_id=wallet_id).count() == 6


# --- what the project's activity reads ---------------------------------------

def test_a_reservation_keeps_what_it_reserved_and_when_it_was_settled(wallet_factory):
    from apps.payments.services.wallet_service import commit_partial_reservation

    wallet = wallet_factory("100.00")
    reserved = reserve_funds(wallet.id, Decimal("30.00"))
    assert (reserved.reserved_amount, reserved.settled_at) == (Decimal("30.00"), None)

    charged = commit_reservation(reserve_funds(wallet.id, Decimal("10.00")).id)
    returned = rollback_reservation(reserve_funds(wallet.id, Decimal("10.00")).id)
    partial = commit_partial_reservation(reserved.id, Decimal("12.00"))

    for txn in (charged, returned, partial):
        txn.refresh_from_db()
        assert txn.settled_at is not None
    # The partial charge overwrites the amount; what was reserved stays.
    assert (partial.amount, partial.reserved_amount) == (Decimal("12.00"), Decimal("30.00"))


def test_a_partial_charge_of_an_old_reservation_keeps_what_it_had_reserved(wallet_factory):
    from apps.payments.services.wallet_service import commit_partial_reservation

    wallet = wallet_factory("100.00")
    old = reserve_funds(wallet.id, Decimal("30.00"))
    Transaction.objects.filter(pk=old.pk).update(reserved_amount=None)  # made before migration 0012

    charged = commit_partial_reservation(old.id, Decimal("12.00"))

    charged.refresh_from_db()
    assert (charged.amount, charged.reserved_amount) == (Decimal("12.00"), Decimal("30.00"))
