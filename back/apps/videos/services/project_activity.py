"""
Activity of a project (D1 › Actividad): who did what and its token movements,
newest first (``GET /api/v1/projects/<id>/activity/``).

There is no event log: the feed is read from what the backend already keeps.

- ``project_created``: ``created_at`` and ``uploaded_by`` of the project.
- ``prompt_applied``: ``metadata['pte']`` (its status), at the time of the
  last ``prompt_parse`` AIUsageRecord.
- ``review_ready``: a project that waits for the review or whose review was
  approved, at the time of the last successful ``selection`` AIUsageRecord.
- ``review_approved``: the approval's reservation (``metadata
  ['render_reservation_id']``): its time and who made it.
- ``tokens_reserved`` / ``tokens_charged`` / ``tokens_refunded``: every
  reservation (a SPEND Transaction) of the project: the ones linked to it
  (``Transaction.project``) and, for a row without a project (older than
  migration 0015 of payments, that 0016 could not link), the ones its metadata
  names (every ``*reservation_id`` key and ``replaced_reservation_ids``, the
  ones a later reservation replaced in its key). Only from the wallet of the
  project's workspace, and never one linked to another project. Reserved at
  ``created_at``, charged or returned at ``settled_at``. The reservation, its
  charge and its refund are items of ONE movement of the balance: each of the
  three says how the reservation ended in ``reservation_state``
  (``reserved``, ``charged``, ``partially_charged`` or ``refunded``), so the
  web app shows them as one group whose amounts add up to what the balance
  moved (a charged reservation is not a second charge; the part a partial
  charge gave back is not income). With every reservation in the feed, the
  groups add up to what the project moved the balance in all.
- ``render_finished``: a project that ended ``completed`` or ``partial``, at
  the settlement of the last reservation that paid a render.
- ``project_failed``: ``pipeline_error_at``.
- ``post_scheduled`` / ``post_published`` / ``post_failed``: the
  ScheduledPosts of its clips (``created_at``, then ``updated_at``).

What a reservation paid for is read from the description the code writes
when it reserves (``Procesamiento: …``, ``Render de clip: …``…), else from
its metadata key, else from its category. The first reservation of the
processing is the creation's; a later one, a re-dispatch's.

Not kept anywhere, so never in the feed: renames (``project_renamed``),
clip edits and retries (``clip_edited``, ``clip_retried``) and who scheduled
a post.

At most MAX_ITEMS items. The movements of the balance go in first, each
group whole: the cut drops the oldest of the other items, so the amounts
still add up to what the project moved (a project with more than MAX_ITEMS
token items, dozens of paid renders, loses its oldest groups whole).

Each item gets a stable id (uuid5 of the project, the kind and its source).
Five queries whatever the size of the project.
"""
import uuid
from decimal import Decimal
from typing import NamedTuple

from django.db.models import Max, Q

from apps.payments.models import Transaction
from apps.videos.models import AIUsageRecord, ScheduledPost, VideoClip, VideoProject

_NAMESPACE = uuid.UUID('6b0c9f8e-2a51-4c7e-9a43-1d5e8f0b7c21')
MAX_ITEMS = 200

# Where views_clips.py (REPLACED_RESERVATIONS_KEY) keeps the reservations a new
# one replaced in its key: a second clip render, a re-dispatch.
REPLACED_KEY = 'replaced_reservation_ids'


class _Purpose(NamedTuple):
    """What a reservation paid for: what its reservation and its charge are called."""

    reserved: str
    charged: str
    # It paid a render: what a partial charge gave back are the clips not generated.
    render: bool = False


_CREATION = _Purpose("Reserva por el procesamiento", "Cobro del procesamiento")
_REDISPATCH = _Purpose("Reserva por volver a procesar", "Cobro del procesamiento")
_APPROVAL = _Purpose("Reserva por el render", "Cobro del render", True)
_AUTO_RENDER = _Purpose("Reserva por renderizar sin revisar", "Cobro del render automático", True)
_RERENDER = _Purpose("Reserva por renderizar todo de nuevo", "Cobro del render", True)
_CLIP_RENDER = _Purpose("Reserva por volver a renderizar", "Cobro del render", True)
_GENERIC = _Purpose("Reserva de tokens", "Cobro de tokens")
_PROCESSING = (_CREATION, _REDISPATCH)

# The description each reservation is written with (views.py, views_clips.py, tasks/ingestion.py).
_BY_DESCRIPTION = (
    ('Procesamiento: ', _CREATION),
    ('Render de los clips aprobados: ', _APPROVAL),
    ('Render automático: ', _AUTO_RENDER),
    ('Render con otro estilo: ', _RERENDER),
)
# Those of one clip end with its title.
_CLIP_DESCRIPTIONS = (
    ('Render de clip: ', "Reserva por renderizar «{}»"),
    ('Reintento de clip: ', "Reserva por reintentar «{}»"),
)
# A row with another description (written by hand, or older): its metadata key, else its category.
_BY_KEY = {
    'reservation_id': _CREATION,
    'render_reservation_id': _APPROVAL,
    'auto_render_reservation_id': _AUTO_RENDER,
    'clip_render_reservation_id': _CLIP_RENDER,
}
# A render that does not say which one is called like the approval's: "the render".
_BY_CATEGORY = {
    Transaction.Category.PROJECT: _CREATION,
    Transaction.Category.RENDER: _APPROVAL,
}
# What moved the balance: a reservation still open, charged or returned.
_MOVED = (Transaction.Status.RESERVED, Transaction.Status.COMPLETED, Transaction.Status.FAILED)

PLATFORM_NAMES = dict(ScheduledPost.Platform.choices)

# Order of items with the same time, oldest first: a reservation right after
# what made it, a charge right after the render.
_ORDER = {
    'project_created': 0, 'prompt_applied': 1, 'review_ready': 2, 'review_approved': 3,
    'tokens_reserved': 4, 'render_finished': 5, 'tokens_charged': 6, 'project_failed': 7,
    'tokens_refunded': 8, 'post_scheduled': 9, 'post_published': 10, 'post_failed': 11,
}


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def user_ref(user):
    """``UserRef`` of the contract: id and display name (never the email)."""
    if user is None:
        return None
    name = user.get_full_name().strip() or (user.username or '').split('@')[0] or "Usuario"
    return {'id': str(user.pk), 'name': name}


def _amount(value):
    return format(Decimal(value), 'f')


def _sort_key(item):
    return item['created_at'], _ORDER.get(item['type'], 99)


def _newest_first(items):
    return sorted(items, key=_sort_key, reverse=True)


class _Feed:
    def __init__(self, project):
        self.project = project
        self.items = []
        # Transaction id -> its items: one movement of the balance, never split by the cut.
        self.movements = {}

    def add(self, kind, at, description, *, source='', actor=None, amount=None, platform=None,
            reservation_state=None, movement=None):
        if at is None:
            return
        item = {
            'id': str(uuid.uuid5(_NAMESPACE, f"{self.project.pk}:{kind}:{source}")),
            'type': kind,
            'actor': user_ref(actor),
            'created_at': at,
            'description': description,
            'amount': _amount(amount) if amount is not None else None,
        }
        if platform is not None:
            item['platform'] = platform
        if reservation_state is not None:
            item['reservation_state'] = reservation_state
        self.items.append(item)
        if movement is not None:
            self.movements.setdefault(movement, []).append(item)

    def sorted(self):
        """
        At most MAX_ITEMS, newest first. The movements of the balance go in
        first, whole, the newest first; the other items fill what is left,
        the newest first.
        """
        kept = []
        for group in sorted(self.movements.values(), key=lambda items: max(map(_sort_key, items)), reverse=True):
            if len(kept) + len(group) > MAX_ITEMS:
                break
            kept.extend(group)
        money = {id(item) for items in self.movements.values() for item in items}
        others = _newest_first(item for item in self.items if id(item) not in money)
        kept.extend(others[:MAX_ITEMS - len(kept)])
        return _newest_first(kept)


def _uuid(value):
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _linked_reservations(metadata):
    """{metadata key: transaction id} of the reservations the project's metadata holds in a key."""
    linked = {}
    for key, value in metadata.items():
        txn_id = _uuid(value) if key.endswith('reservation_id') else None
        if txn_id is not None:
            linked[key] = txn_id
    return linked


def _replaced_reservations(metadata):
    """The transaction ids a later reservation replaced in their key."""
    replaced = metadata.get(REPLACED_KEY)
    if not isinstance(replaced, list):
        return set()
    return {txn_id for txn_id in map(_uuid, replaced) if txn_id is not None}


def _project_reservations(project, named):
    """
    The reservations of ``project``, oldest first, in one query: the ones
    linked to it and, without a project, the ones its metadata ``named``. Only
    from its workspace's wallet: another project's or workspace's never.
    """
    return list(
        Transaction.objects.filter(
            Q(project_id=project.pk) | Q(project__isnull=True, pk__in=named),
            wallet__workspace_id=project.workspace_id,
            transaction_type=Transaction.Type.SPEND,
            status__in=_MOVED,
        ).select_related('created_by').order_by('created_at', 'pk')
    )


def _purpose(txn, key):
    description = txn.description or ''
    for prefix, purpose in _BY_DESCRIPTION:
        if description.startswith(prefix):
            return purpose
    for prefix, reserved in _CLIP_DESCRIPTIONS:
        if description.startswith(prefix) and description[len(prefix):].strip():
            return _Purpose(reserved.format(description[len(prefix):].strip()), "Cobro del render", True)
    if key is not None:
        return _BY_KEY.get(key, _GENERIC)
    return _BY_CATEGORY.get(txn.category, _GENERIC)


def _reservation_state(txn, reserved):
    """How a reservation ended: what the web app shows next to it instead of a second charge."""
    if txn.status == Transaction.Status.COMPLETED:
        return 'partially_charged' if reserved > txn.amount else 'charged'
    if txn.status == Transaction.Status.FAILED:
        return 'refunded'
    return 'reserved'


def _add_movement(feed, txn, purpose):
    """The items of one reservation: its reservation and, once settled, its charge and what came back."""
    reserved = txn.reserved_amount if txn.reserved_amount is not None else txn.amount
    settled = txn.settled_at or txn.created_at
    state = _reservation_state(txn, reserved)
    source = str(txn.pk)
    feed.add(
        'tokens_reserved', txn.created_at, purpose.reserved, source=source, actor=txn.created_by,
        amount=-reserved, reservation_state=state, movement=txn.pk,
    )
    if txn.status == Transaction.Status.COMPLETED:
        feed.add(
            'tokens_charged', settled, purpose.charged, source=source, amount=-txn.amount,
            reservation_state=state, movement=txn.pk,
        )
        if reserved > txn.amount:
            returned_text = (
                "Devolución por los clips que no se generaron" if purpose.render
                else "Devolución de la parte que no se usó"
            )
            feed.add(
                'tokens_refunded', settled, returned_text, source=source, amount=reserved - txn.amount,
                reservation_state=state, movement=txn.pk,
            )
    elif txn.status == Transaction.Status.FAILED:
        feed.add(
            'tokens_refunded', settled, "Devolución de la reserva", source=source, amount=reserved,
            reservation_state=state, movement=txn.pk,
        )


def _prompt_description(pte):
    status = pte.get('status') if isinstance(pte, dict) else None
    if status == 'applied':
        return "La IA aplicó la configuración del prompt"
    if status == 'empty':
        return "La IA no encontró nada para cambiar en el prompt"
    return "No pudimos aplicar el prompt: el proyecto siguió con su configuración"


def _is_whole_video(project, metadata):
    return project.project_type == VideoProject.Type.SINGLE_EDIT and metadata.get('priced_duration_s') is not None


def build_activity(project):
    """The activity items of ``project`` (contract ``ActivityItem[]``), newest first."""
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    feed = _Feed(project)
    feed.add('project_created', project.created_at, "Creó el proyecto", actor=project.uploaded_by)

    linked = _linked_reservations(metadata)
    keys = {txn_id: key for key, txn_id in linked.items()}
    transactions = _project_reservations(project, _replaced_reservations(metadata) | set(keys))
    by_id = {txn.pk: txn for txn in transactions}
    usage = dict(
        AIUsageRecord.objects.filter(
            project_id=project.pk, success=True,
            stage__in=[AIUsageRecord.Stage.PROMPT_PARSE, AIUsageRecord.Stage.SELECTION],
        ).values_list('stage').annotate(last=Max('created_at'))
    )
    clip_statuses = list(VideoClip.objects.filter(project_id=project.pk).values_list('status', 'created_at'))

    # Prompt-to-Edit: parsed at the start of the ingestion.
    if metadata.get('pte'):
        feed.add(
            'prompt_applied', usage.get(AIUsageRecord.Stage.PROMPT_PARSE) or project.created_at,
            _prompt_description(metadata['pte']),
        )

    approval = by_id.get(linked.get('render_reservation_id'))
    whole_video = _is_whole_video(project, metadata)
    if not whole_video and (project.status == VideoProject.Status.AWAITING_APPROVAL or approval is not None):
        suggested = (project.ai_rationale_log or {}).get('suggestions_count')
        if isinstance(suggested, bool) or not isinstance(suggested, int) or suggested < 0:
            # Not recorded: the clips there are now (fewer than suggested once a
            # review left some out, which deletes them).
            suggested = len(clip_statuses)
        first_clip = min((created for _status, created in clip_statuses), default=None)
        feed.add(
            'review_ready', usage.get(AIUsageRecord.Stage.SELECTION) or first_clip,
            f"Listo para revisar: {_plural(suggested, 'clip sugerido', 'clips sugeridos')}",
        )
    if approval is not None:
        approved = metadata.get('render_clip_ids')
        count = len(approved) if isinstance(approved, list) else len(project.approved_segments or [])
        feed.add(
            'review_approved', approval.created_at,
            f"Aprobó {_plural(count, 'clip', 'clips')} para renderizar",
            source=str(approval.pk), actor=approval.created_by,
        )

    renders = []  # the reservations that paid a render
    processed = False
    for txn in transactions:
        purpose = _purpose(txn, keys.get(txn.pk))
        if purpose in _PROCESSING:
            # Oldest first: the first one is the creation, a later one a re-dispatch.
            purpose = _REDISPATCH if processed else _CREATION
            processed = True
        if purpose.render or (whole_video and purpose in _PROCESSING):
            renders.append(txn)
        _add_movement(feed, txn, purpose)

    if project.status in (VideoProject.Status.COMPLETED, VideoProject.Status.PARTIAL):
        # The last render the project ended with: the clips it counts are those it left.
        paying = max(renders, key=lambda txn: txn.settled_at or txn.created_at, default=None)
        ready = sum(1 for status, _created in clip_statuses if status in ('completed', 'published'))
        failed = sum(1 for status, _created in clip_statuses if status == 'failed')
        if whole_video:
            text = "Terminó el render del video" if not failed else "El render del video falló"
        elif failed:
            text = f"Terminó el render: {_plural(ready, 'clip listo', 'clips listos')} y {failed} con error"
        else:
            text = f"Terminó el render: {_plural(ready, 'clip listo', 'clips listos')}"
        at = (paying.settled_at or paying.created_at) if paying is not None else None
        feed.add('render_finished', at, text)

    if project.status == VideoProject.Status.FAILED:
        feed.add('project_failed', project.pipeline_error_at or project.created_at, "El proceso falló")

    posts = (
        ScheduledPost.objects.filter(video_clip__project_id=project.pk)
        .exclude(status=ScheduledPost.Status.DRAFT)
        .select_related('video_clip')
        .order_by('-created_at')[:MAX_ITEMS]
    )
    for post in posts:
        clip_title = post.video_clip.title
        network = PLATFORM_NAMES.get(post.platform, post.platform)
        source = str(post.pk)
        feed.add(
            'post_scheduled', post.created_at, f"Se programó «{clip_title}» en {network}",
            source=source, platform=post.platform,
        )
        if post.status == ScheduledPost.Status.PUBLISHED:
            feed.add('post_published', post.updated_at, f"Se publicó «{clip_title}» en {network}", source=source, platform=post.platform)
        elif post.status == ScheduledPost.Status.FAILED:
            feed.add(
                'post_failed', post.updated_at, f"Falló la publicación de «{clip_title}» en {network}",
                source=source, platform=post.platform,
            )

    return feed.sorted()
