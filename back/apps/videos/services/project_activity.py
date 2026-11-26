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
- ``tokens_reserved`` / ``tokens_charged`` / ``tokens_refunded``: the
  Transactions linked in the metadata (every ``*reservation_id`` key):
  reserved at ``created_at``, charged or returned at ``settled_at``.
- ``render_finished``: a project that ended ``completed`` or ``partial``, at
  the settlement of the reservation that paid the render.
- ``project_failed``: ``pipeline_error_at``.
- ``post_scheduled`` / ``post_published`` / ``post_failed``: the
  ScheduledPosts of its clips (``created_at``, then ``updated_at``).

Not kept anywhere, so never in the feed: renames (``project_renamed``),
clip edits and retries (``clip_edited``, ``clip_retried``), who scheduled a
post, and a reservation whose metadata key was dropped (a settled
auto-render once the review is approved).

Each item gets a stable id (uuid5 of the project, the kind and its source).
Five queries whatever the size of the project.
"""
import uuid
from decimal import Decimal

from django.db.models import Max

from apps.payments.models import Transaction
from apps.videos.models import AIUsageRecord, ScheduledPost, VideoClip, VideoProject

_NAMESPACE = uuid.UUID('6b0c9f8e-2a51-4c7e-9a43-1d5e8f0b7c21')
MAX_ITEMS = 200

# Metadata key of the reservation -> (what it reserved, what it charged).
_RESERVATIONS = {
    'reservation_id': ("Reserva por el procesamiento", "Cobro del procesamiento"),
    'render_reservation_id': ("Reserva por el render", "Cobro del render"),
    'auto_render_reservation_id': ("Reserva por renderizar sin revisar", "Cobro del render automático"),
}
_GENERIC_RESERVATION = ("Reserva de tokens", "Cobro de tokens")
_RENDER_KEYS = ('render_reservation_id', 'auto_render_reservation_id')

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


class _Feed:
    def __init__(self, project):
        self.project = project
        self.items = []

    def add(self, kind, at, description, *, source='', actor=None, amount=None, platform=None):
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
        self.items.append(item)

    def sorted(self):
        ordered = sorted(self.items, key=lambda item: (item['created_at'], _ORDER.get(item['type'], 99)), reverse=True)
        return ordered[:MAX_ITEMS]


def _linked_reservations(project):
    """{metadata key: transaction id} of the reservations linked to the project."""
    linked = {}
    for key, value in (project.metadata or {}).items():
        if not key.endswith('reservation_id') or not isinstance(value, str):
            continue
        try:
            linked[key] = uuid.UUID(value)
        except ValueError:
            continue
    return linked


def _prompt_description(pte):
    status = pte.get('status') if isinstance(pte, dict) else None
    if status == 'applied':
        return "La IA aplicó la configuración del prompt"
    if status == 'empty':
        return "La IA no encontró nada para cambiar en el prompt"
    return "No pudimos aplicar el prompt: el proyecto siguió con su configuración"


def _is_whole_video(project):
    return (
        project.project_type == VideoProject.Type.SINGLE_EDIT
        and (project.metadata or {}).get('priced_duration_s') is not None
    )


def build_activity(project):
    """The activity items of ``project`` (contract ``ActivityItem[]``), newest first."""
    metadata = project.metadata or {}
    feed = _Feed(project)
    feed.add('project_created', project.created_at, "Creó el proyecto", actor=project.uploaded_by)

    linked = _linked_reservations(project)
    transactions = {
        txn.pk: txn
        for txn in Transaction.objects.filter(
            pk__in=list(linked.values()), wallet__workspace_id=project.workspace_id,
        ).select_related('created_by')
    }
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

    approval = transactions.get(linked.get('render_reservation_id'))
    whole_video = _is_whole_video(project)
    if not whole_video and (project.status == VideoProject.Status.AWAITING_APPROVAL or approval is not None):
        suggested = (project.ai_rationale_log or {}).get('suggestions_count')
        if not isinstance(suggested, int):
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

    for key, txn_id in linked.items():
        txn = transactions.get(txn_id)
        if txn is None:
            continue
        reserved_text, charged_text = _RESERVATIONS.get(key, _GENERIC_RESERVATION)
        reserved = txn.reserved_amount if txn.reserved_amount is not None else txn.amount
        settled = txn.settled_at or txn.created_at
        feed.add('tokens_reserved', txn.created_at, reserved_text, source=str(txn.pk), actor=txn.created_by, amount=-reserved)
        if txn.status == Transaction.Status.COMPLETED:
            feed.add('tokens_charged', settled, charged_text, source=str(txn.pk), amount=-txn.amount)
            if reserved > txn.amount:
                returned_text = (
                    "Devolución por los clips que no se generaron" if key in _RENDER_KEYS
                    else "Devolución de la parte que no se usó"
                )
                feed.add('tokens_refunded', settled, returned_text, source=str(txn.pk), amount=reserved - txn.amount)
        elif txn.status == Transaction.Status.FAILED:
            feed.add('tokens_refunded', settled, "Devolución de la reserva", source=str(txn.pk), amount=reserved)

    if project.status in (VideoProject.Status.COMPLETED, VideoProject.Status.PARTIAL):
        paying = (
            approval
            or transactions.get(linked.get('auto_render_reservation_id'))
            or (transactions.get(linked.get('reservation_id')) if whole_video else None)
        )
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
