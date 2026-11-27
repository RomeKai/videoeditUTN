"""
What the project screens show about a project beyond its fields (D1 header,
Resumen and Actividad), read from what the backend already keeps:

- ``cost``: the creation reservation (``metadata['reservation_id']``), the
  Transaction that pays for processing the project: how much and whether it
  is still reserved, was charged or was returned.
- ``stage_timings``: ``metadata['stage_durations']`` (the last successful run
  of each stage, INFRA-02) and ``metadata['stage_finished_at']`` (when it
  ended). Stages that finished before ``stage_finished_at`` existed only have
  their duration.
- ``uploaded_by_detail``: who created the project, as a ``UserRef``.
"""
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from apps.payments.models import Transaction

from .project_activity import user_ref

CREATION_RESERVATION_KEY = 'reservation_id'
_CACHE_KEY = '_project_creation_reservations'

_STATE = {
    Transaction.Status.RESERVED: 'reserved',
    Transaction.Status.COMPLETED: 'charged',
    Transaction.Status.FAILED: 'refunded',
}


def creation_reservation_id(project):
    value = (project.metadata or {}).get(CREATION_RESERVATION_KEY)
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def creation_reservations(projects, context):
    """
    {transaction id: Transaction} of the creation reservations of ``projects``,
    read once per response and kept in the serializer ``context``: a page of
    projects costs one query, not one per project.
    """
    cache = context.setdefault(_CACHE_KEY, {})
    missing = {
        reservation_id for reservation_id in map(creation_reservation_id, projects)
        if reservation_id is not None and reservation_id not in cache
    }
    if missing:
        found = Transaction.objects.filter(pk__in=missing).only('id', 'status', 'amount', 'reserved_amount')
        cache.update({transaction.pk: transaction for transaction in found})
        # Remember the ids that are gone too, so they are not asked for again.
        cache.update({reservation_id: None for reservation_id in missing if reservation_id not in cache})
    return cache


def project_cost(project, reservations):
    """``ProjectCost`` of the contract, or None for a project without a creation reservation."""
    reservation_id = creation_reservation_id(project)
    transaction = reservations.get(reservation_id) if reservation_id else None
    if transaction is None:
        return None
    state = _STATE.get(transaction.status)
    if state is None:
        return None
    # A refund shows what had been set aside; a partial charge only what was charged
    # (the returned part is in the project's activity).
    amount = transaction.amount
    if state == 'refunded' and transaction.reserved_amount is not None:
        amount = transaction.reserved_amount
    return {
        'amount': format(Decimal(amount), 'f'),
        'state': state,
        'transaction_id': str(transaction.pk),
    }


def _parse(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def stage_timings(project):
    """``{stage: StageTiming}`` of the stages that finished, from the metadata."""
    metadata = project.metadata or {}
    durations = metadata.get('stage_durations')
    if not isinstance(durations, dict):
        return {}
    finished = metadata.get('stage_finished_at')
    finished = finished if isinstance(finished, dict) else {}
    timings = {}
    for stage, duration in durations.items():
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
            continue
        finished_at = _parse(finished.get(stage))
        started_at = finished_at - timedelta(seconds=duration) if finished_at else None
        timings[str(stage)] = {
            'started_at': started_at.isoformat() if started_at else None,
            'finished_at': finished_at.isoformat() if finished_at else None,
            'duration_s': float(duration),
        }
    return timings


def uploaded_by_detail(project):
    return user_ref(project.uploaded_by) if project.uploaded_by_id else None
