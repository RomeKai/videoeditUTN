from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from ..models import Wallet, Transaction

class PaymentError(Exception):
    """Base exception for payment-related errors."""
    pass

class InsufficientFundsError(PaymentError):
    """Raised when available balance is lower than requested amount."""
    pass

@transaction.atomic
def reserve_funds(
    wallet_id, amount: Decimal, project_id=None, user=None, description="", *, project=None, category=None,
) -> Transaction:
    """
    Locks funds in the workspace's wallet before an operation begins.
    Uses select_for_update to prevent race conditions.

    ``project`` and ``category`` say what the reservation pays for
    (Transaction.project and Transaction.category: the wallet's movements and
    the usage chart). They are only written on the row.

    ``project_id`` is accepted and ignored. It used to queue
    ``process_video_pipeline``, a second ingestion of a project whose creation
    already queues its own: the caller queues what the reservation pays for,
    after it commits.
    """
    # Select for update locks the row until the transaction block ends
    try:
        wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
    except Wallet.DoesNotExist:
        raise PaymentError(f"Wallet with id {wallet_id} does not exist.")

    if wallet.available_balance < amount:
        raise InsufficientFundsError(
            f"Insufficient funds. Required: {amount}, Available: {wallet.available_balance}"
        )

    # Move funds from available to reserved
    wallet.available_balance -= amount
    wallet.reserved_balance += amount
    wallet.save()

    # Create a transaction record in RESERVED status
    txn = Transaction.objects.create(
        wallet=wallet,
        created_by=user,
        amount=amount,
        reserved_amount=amount,
        transaction_type=Transaction.Type.SPEND,
        status=Transaction.Status.RESERVED,
        description=description,
        project=project,
        category=category or Transaction.Category.OTHER,
    )

    return txn

@transaction.atomic
def commit_reservation(transaction_id) -> Transaction:
    """
    Finalizes a reservation. The funds are already deducted from available_balance,
    so we just clear them from reserved_balance.
    """
    try:
        txn = Transaction.objects.select_for_update().get(pk=transaction_id)
    except Transaction.DoesNotExist:
        raise PaymentError(f"Transaction with id {transaction_id} does not exist.")
    
    if txn.status != Transaction.Status.RESERVED:
        raise PaymentError(f"Cannot commit transaction in status {txn.status}")

    wallet = Wallet.objects.select_for_update().get(pk=txn.wallet_id)
    
    # Finalize the deduction
    wallet.reserved_balance -= txn.amount
    wallet.save()

    txn.status = Transaction.Status.COMPLETED
    txn.settled_at = timezone.now()
    txn.save()
    
    return txn

@transaction.atomic
def commit_partial_reservation(transaction_id, amount: Decimal) -> Transaction:
    """
    Charges only ``amount`` of a reservation and returns the rest to the
    available balance: the work was delivered in part (e.g. some auto-rendered
    clips failed). The transaction keeps the charged amount. ``amount`` at or
    above the reservation commits it whole; zero or less rolls it back.
    """
    # Through str: a float must not become its binary expansion.
    amount = Decimal(str(amount))
    try:
        txn = Transaction.objects.select_for_update().get(pk=transaction_id)
    except Transaction.DoesNotExist:
        raise PaymentError(f"Transaction with id {transaction_id} does not exist.")

    if txn.status != Transaction.Status.RESERVED:
        raise PaymentError(f"Cannot commit transaction in status {txn.status}")
    if amount >= txn.amount:
        return commit_reservation(transaction_id)
    if amount <= 0:
        return rollback_reservation(transaction_id)

    wallet = Wallet.objects.select_for_update().get(pk=txn.wallet_id)
    released = txn.amount - amount
    if txn.reserved_amount is None:
        # A reservation from before migration 0012: what it reserved is kept
        # before ``amount`` becomes what was charged (the activity shows the refund).
        txn.reserved_amount = txn.amount
    wallet.reserved_balance -= txn.amount
    wallet.available_balance += released
    wallet.save()

    txn.amount = amount
    txn.status = Transaction.Status.COMPLETED
    txn.settled_at = timezone.now()
    txn.save()

    return txn

@transaction.atomic
def rollback_reservation(transaction_id) -> Transaction:
    """
    Reverts a reservation in case of error (e.g., rendering failure).
    Returns reserved funds back to available_balance.
    """
    try:
        txn = Transaction.objects.select_for_update().get(pk=transaction_id)
    except Transaction.DoesNotExist:
        raise PaymentError(f"Transaction with id {transaction_id} does not exist.")
    
    if txn.status != Transaction.Status.RESERVED:
        raise PaymentError(f"Cannot rollback transaction in status {txn.status}")

    wallet = Wallet.objects.select_for_update().get(pk=txn.wallet_id)
    
    # Return funds to available balance
    wallet.available_balance += txn.amount
    wallet.reserved_balance -= txn.amount
    wallet.save()

    txn.status = Transaction.Status.FAILED
    txn.settled_at = timezone.now()
    txn.save()
    
    return txn
