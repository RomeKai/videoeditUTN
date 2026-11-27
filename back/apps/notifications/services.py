"""
In-app notifications (the bell, J2): the only place that creates them.

Each ``notify_*`` is called where its event happens (the pipeline, the render,
a payment's settlement), never from a view that renders. All of them:

- write after the caller's transaction commits (``transaction.on_commit``;
  right away when there is none), so a change that is rolled back never
  notifies;
- are best effort: nothing they do raises. An error is logged with ids and the
  exception CLASS only, and the step that called them goes on;
- only reach people who can act in the workspace when it is written: its owner
  and its ACTIVE members.

The table stays bounded: after each write, only the newest ``KEPT_PER_USER``
of each recipient in that workspace are kept (what the panel shows).
"""
import logging
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.payments.models import Payment, Transaction
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import VideoClip, VideoProject

from .models import Notification

logger = logging.getLogger(__name__)

Kind = Notification.Kind

# What the bell shows and what is kept of each user in each workspace.
KEPT_PER_USER = 20
# A settlement counts as part of an event when it happened this close to it.
_SETTLED_WINDOW = timedelta(minutes=2)
_TITLE_ROOM = 255


def format_tokens(amount):
    """Decimal('1250.5') → "1.250,50" and 20 → "20", like the web app's formatNumber."""
    value = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    whole, _, cents = f'{value:,.2f}'.partition('.')
    return whole.replace(',', '.') + ('' if cents == '00' else f',{cents}')


def _tokens(amount):
    """ "12,50 tokens", "1 token". """
    return f"{format_tokens(amount)} {'token' if Decimal(amount) == 1 else 'tokens'}"


def recipients_of(workspace):
    """The ids of who can act in the workspace now: its owner and its ACTIVE members."""
    members = WorkspaceMember.objects.filter(
        workspace=workspace, status=WorkspaceMember.Status.ACTIVE,
    ).values_list('user_id', flat=True)
    return {workspace.owner_id, *members} - {None}


def _prune(user_id, workspace_id):
    old = list(
        Notification.objects.filter(user_id=user_id, workspace_id=workspace_id)
        .order_by('-created_at', '-pk').values_list('pk', flat=True)[KEPT_PER_USER:]
    )
    if old:
        Notification.objects.filter(pk__in=old).delete()


def _write(workspace, user_ids, kind, title, body, project=None, post=None):
    """One notification for each of ``user_ids`` who can still act in the workspace."""
    targets = recipients_of(workspace) & set(user_ids)
    with transaction.atomic():
        for user_id in targets:
            Notification.objects.create(
                user_id=user_id, workspace=workspace, kind=kind, title=title[:300], body=body[:300],
                project=project, post=post,
            )
            _prune(user_id, workspace.pk)
    return len(targets)


# For the demo seed (seed_demo), which writes example notifications with the texts of the real events.
write = _write


def _after_commit(event, build, **ids):
    """
    Runs ``build`` once the caller's transaction commits. Never raises, now or
    then: ``ids`` (ids only) go to the log with the class of the error.
    """
    def run():
        try:
            build()
        except Exception as exc:
            logger.error(
                'notifications.not_created event=%s %s error=%s',
                event, ' '.join(f'{key}={value}' for key, value in ids.items()), type(exc).__name__,
            )

    try:
        transaction.on_commit(run)
    except Exception as exc:
        logger.error(
            'notifications.not_scheduled event=%s %s error=%s',
            event, ' '.join(f'{key}={value}' for key, value in ids.items()), type(exc).__name__,
        )


def _project(project_id):
    return VideoProject.objects.select_related('workspace').filter(pk=project_id).first()


def _reservations(project):
    """The project's reservations: linked to it, or named in its metadata (rows from before the link)."""
    named = []
    for key, value in (project.metadata or {}).items():
        if key.endswith('reservation_id'):
            try:
                named.append(uuid.UUID(str(value)))
            except ValueError:
                continue
    return Transaction.objects.filter(Q(project=project) | Q(pk__in=named))


def _returned(project, since):
    """Tokens that went back to the wallet for this project since ``since``."""
    total = Decimal('0')
    settled = _reservations(project).filter(
        settled_at__gte=since, status__in=[Transaction.Status.FAILED, Transaction.Status.COMPLETED],
    )
    for txn in settled:
        reserved = txn.reserved_amount if txn.reserved_amount is not None else txn.amount
        if txn.status == Transaction.Status.FAILED:
            total += reserved
        elif reserved > txn.amount:
            total += reserved - txn.amount
    return total


def _to_workspace(project, kind, title, body):
    workspace = project.workspace
    return _write(workspace, recipients_of(workspace), kind, title, body, project=project)


def project_ready_message(project):
    """(title, body) of ``project_ready``."""
    clips = VideoClip.objects.filter(project=project).count()
    found = 'Encontramos 1 clip.' if clips == 1 else f'Encontramos {clips} clips.'
    return f'{project.title[:_TITLE_ROOM]} está listo para revisar', found


def render_finished_message(project):
    """(title, body) of ``render_finished``, for a project that ended completed or partial."""
    if project.status == VideoProject.Status.PARTIAL:
        returned = _returned(project, timezone.now() - _SETTLED_WINDOW)
        body = 'Parte del render falló.'
        if returned > 0:
            body += f' Te devolvimos {_tokens(returned)}.'
    else:
        ready = VideoClip.objects.filter(project=project, status=VideoClip.Status.COMPLETED).count()
        body = 'Tu video está listo.' if ready <= 1 else f'{ready} clips listos.'
    return f'{project.title[:_TITLE_ROOM]}: terminó el render', body


def project_failed_message(project_title, returned, rendering=False):
    """(title, body) of ``project_failed``: ``returned`` tokens went back (0: nothing was charged)."""
    body = f'Te devolvimos {_tokens(returned)}.' if returned > 0 else 'No se cobró nada.'
    what = 'no se pudo generar' if rendering else 'no se pudo procesar'
    return f'{project_title[:_TITLE_ROOM]} {what}', body


def payment_message(approved, tokens=0, plan_name=None):
    """(title, body) of a settled payment: of the plan ``plan_name``, else of ``tokens``."""
    if plan_name is not None:
        title = f'Tu plan ahora es {plan_name}' if approved else f'No pudimos cobrar el plan {plan_name}'
        body = 'El pago se aprobó.' if approved else 'No se cobró nada. Podés intentarlo de nuevo.'
    else:
        amount = format_tokens(tokens)
        title = f'Se acreditaron {amount} tokens' if approved else f'No pudimos cobrar tu compra de {amount} tokens'
        body = 'Tu compra se aprobó.' if approved else 'No se cobró nada. Podés intentarlo de nuevo.'
    return title, body


def notify_project_ready(project_id):
    """The clips are selected and the project waits for the review (``awaiting_approval``)."""
    def build():
        project = _project(project_id)
        if project is None or project.status != VideoProject.Status.AWAITING_APPROVAL:
            return
        _to_workspace(project, Kind.PROJECT_READY, *project_ready_message(project))

    _after_commit('project_ready', build, project_id=project_id)


def notify_render_finished(project_id):
    """The render ended: every clip delivered (``completed``) or only some (``partial``)."""
    def build():
        project = _project(project_id)
        if project is None or project.status not in (VideoProject.Status.COMPLETED, VideoProject.Status.PARTIAL):
            return
        _to_workspace(project, Kind.RENDER_FINISHED, *render_finished_message(project))

    _after_commit('render_finished', build, project_id=project_id)


def notify_project_failed(project_id, rendering=False):
    """
    The project ended ``failed``, with what was returned for it. Call it after
    the refund (or in its transaction). ``rendering``: it was the final render
    that failed, not the processing.
    """
    def build():
        project = _project(project_id)
        if project is None or project.status != VideoProject.Status.FAILED:
            return
        # The refund is settled after the error is recorded (pipeline_error_at).
        since = project.pipeline_error_at or timezone.now() - _SETTLED_WINDOW
        title, body = project_failed_message(project.title, _returned(project, since), rendering)
        _to_workspace(project, Kind.PROJECT_FAILED, title, body)

    _after_commit('project_failed', build, project_id=project_id)


def notify_payment_settled(payment_id):
    """A token purchase or a plan payment was approved or rejected: for who started it."""
    def build():
        payment = Payment.objects.select_related('workspace', 'plan').filter(pk=payment_id).first()
        if payment is None or payment.created_by_id is None:
            return
        approved = payment.status == Payment.Status.APPROVED
        if not approved and payment.status != Payment.Status.REJECTED:
            return
        title, body = payment_message(
            approved, tokens=payment.tokens, plan_name=payment.plan.name if payment.plan_id is not None else None,
        )
        kind = Kind.PAYMENT_APPROVED if approved else Kind.PAYMENT_REJECTED
        _write(payment.workspace, {payment.created_by_id}, kind, title, body)

    _after_commit('payment_settled', build, payment_id=payment_id)
