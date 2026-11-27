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
- ``eta_seconds``: while a project is processing, how long until "Listo para
  revisar": the estimate for its duration (the same one Nuevo proyecto shows,
  apps.payments.services.estimate) minus what its finished stages took.
"""
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.db.models import QuerySet

from apps.payments.models import Transaction

from .project_activity import user_ref

CREATION_RESERVATION_KEY = 'reservation_id'
_CACHE_KEY = '_project_creation_reservations'
_ROOT_KEY = '_project_creation_reservations_root'
_RATIO_KEY = '_project_review_seconds_per_second'

# No stage and no video lasts this long (115 days): a larger number in the
# metadata is not a duration, and it would overflow the dates and the ETA.
MAX_SECONDS = 10_000_000

_STATE = {
    Transaction.Status.RESERVED: 'reserved',
    Transaction.Status.COMPLETED: 'charged',
    Transaction.Status.FAILED: 'refunded',
}


def _metadata(project):
    """The project's metadata, or {} when it is not an object (it is written by many hands)."""
    metadata = project.metadata
    return metadata if isinstance(metadata, dict) else {}


def _seconds(value):
    """Whether a value of the metadata is a duration: a finite number from 0 to MAX_SECONDS."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= MAX_SECONDS


def creation_reservation_id(project):
    value = _metadata(project).get(CREATION_RESERVATION_KEY)
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
        found = Transaction.objects.filter(pk__in=missing).only('id', 'status', 'amount')
        cache.update({transaction.pk: transaction for transaction in found})
        # Remember the ids that are gone too, so they are not asked for again.
        cache.update({reservation_id: None for reservation_id in missing if reservation_id not in cache})
    return cache


def response_cost(project, root_instance, context):
    """
    ``ProjectCost`` of one project of a response. ``root_instance`` is what the
    response serializes: its projects (a list or a queryset) are read together
    the first time, in one query, and after that each project only looks up
    its own reservation. A single project reads its own, and so does one
    nested in a response of something else (a list of clips or of dicts): only
    the projects of the root are read together.
    """
    from apps.videos.models import VideoProject

    if context.get(_ROOT_KEY) is not root_instance:
        context[_ROOT_KEY] = root_instance
        if isinstance(root_instance, (list, tuple, QuerySet)):
            creation_reservations(
                [item for item in root_instance if isinstance(item, VideoProject)], context,
            )
    reservation_id = creation_reservation_id(project)
    if reservation_id is None:
        return None
    reservations = context.setdefault(_CACHE_KEY, {})
    if reservation_id not in reservations:
        reservations = creation_reservations([project], context)
    return _cost(reservations.get(reservation_id))


def project_cost(project, reservations):
    """``ProjectCost`` of the contract, or None for a project without a creation reservation."""
    reservation_id = creation_reservation_id(project)
    return _cost(reservations.get(reservation_id) if reservation_id else None)


def _cost(transaction):
    if transaction is None:
        return None
    state = _STATE.get(transaction.status)
    if state is None:
        return None
    # ``amount`` is what was set aside for a reservation and for a refund
    # (rollback_reservation never rewrites it), and what was charged for a
    # charge: a partial one shows only that (the returned part is in the
    # project's activity).
    amount = transaction.amount
    return {
        'amount': format(Decimal(amount), 'f'),
        'state': state,
        'transaction_id': str(transaction.pk),
    }


def _parse(value):
    """An instant of the metadata, always with its offset: one written without it is UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _iso(instant):
    try:
        return instant.isoformat() if instant else None
    except (OverflowError, ValueError):
        return None


def _started_at(finished_at, duration):
    if finished_at is None:
        return None
    try:
        return finished_at - timedelta(seconds=duration)
    except OverflowError:  # before the year 1
        return None


def stage_timings(project):
    """``{stage: StageTiming}`` of the stages that finished, from the metadata."""
    metadata = _metadata(project)
    durations = metadata.get('stage_durations')
    if not isinstance(durations, dict):
        return {}
    finished = metadata.get('stage_finished_at')
    finished = finished if isinstance(finished, dict) else {}
    timings = {}
    for stage, duration in durations.items():
        if not _seconds(duration):
            continue
        finished_at = _parse(finished.get(stage))
        timings[str(stage)] = {
            'started_at': _iso(_started_at(finished_at, duration)),
            'finished_at': _iso(finished_at),
            'duration_s': float(duration),
        }
    return timings


def uploaded_by_detail(project):
    return user_ref(project.uploaded_by) if project.uploaded_by_id else None


def _review_ratio(context):
    """
    Seconds of processing per second of video, read once per response and kept
    in the serializer ``context``: when the cache does not have it (or does
    not answer), a list of projects being processed costs one query, not one
    per project.
    """
    from apps.payments.services.estimate import review_seconds_per_second

    if context is None:
        return review_seconds_per_second()
    if _RATIO_KEY not in context:
        context[_RATIO_KEY] = review_seconds_per_second()
    return context[_RATIO_KEY]


def eta_seconds(project, context=None):
    """
    Seconds until the review of a project still being processed, or None: not
    processing, a whole-video edit (its render comes after and is not in the
    estimate), past the selection, no duration, or no history to estimate from.
    """
    from apps.payments.services.estimate import REVIEW_STAGES, review_eta_seconds
    from apps.videos.models import VideoProject

    if project.status not in (VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING):
        return None
    if project.project_type == VideoProject.Type.SINGLE_EDIT:
        return None
    metadata = _metadata(project)
    durations = metadata.get('stage_durations')
    durations = durations if isinstance(durations, dict) else {}
    if REVIEW_STAGES[-1] in durations:
        return None
    source = metadata.get('source_media')
    duration = metadata.get('priced_duration_s')
    if not _seconds(duration) or not duration:
        duration = source.get('duration_s') if isinstance(source, dict) else None
    if not _seconds(duration) or not duration:
        return None
    total = review_eta_seconds(duration, ratio=_review_ratio(context))
    if total is None:
        return None
    spent = sum(durations[stage] for stage in REVIEW_STAGES if _seconds(durations.get(stage)))
    return max(0, int(round(total - spent)))
