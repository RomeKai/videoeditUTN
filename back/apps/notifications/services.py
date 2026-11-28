"""
In-app notifications (the bell, J2): the only place that creates them.

Each ``notify_*`` is called where its event happens (the pipeline, the render,
a payment's settlement, the publisher), never from a view that renders. All of them:

- write after the caller's transaction commits (``transaction.on_commit``;
  right away when there is none), so a change that is rolled back never
  notifies;
- are best effort: nothing they do raises. An error is logged with ids and the
  exception CLASS only, and the step that called them goes on;
- only reach people who can act in the workspace when it is written: its owner
  and its ACTIVE members.

The table stays bounded: after each write, only the newest ``KEPT_PER_USER``
of each recipient in that workspace are kept (what the panel shows).

Each notification of a kind that has an email (``emails.EMAIL_KINDS``) also
queues ``tasks.send_notification_email`` with its id, once its row is
committed. The task decides who gets it (the user's preferences) and what it
says; queueing never raises, and the bell is already written by then.
"""
import logging
import uuid
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.payments.models import Payment, Transaction, Wallet
from apps.users.models import Workspace, WorkspaceMember
from apps.videos.models import ScheduledPost, VideoClip, VideoProject

from .emails import EMAIL_KINDS
from .models import Notification

logger = logging.getLogger(__name__)

Kind = Notification.Kind

# What the bell shows and what is kept of each user in each workspace.
KEPT_PER_USER = 20
# ``reason`` of notify_project_failed: the project's error code is AutoRenderNotPaid.
AUTO_RENDER_UNPAID = 'auto_render_unpaid'
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


def _queue_emails(kind, notification_ids):
    """One email task per notification, with its id only. Never raises: the bell is already written."""
    from .tasks import send_notification_email

    for notification_id in notification_ids:
        try:
            send_notification_email.delay(str(notification_id))
        except Exception as exc:
            logger.error(
                'notifications.email_not_queued kind=%s notification_id=%s error=%s',
                kind, notification_id, type(exc).__name__,
            )


def _write(workspace, user_ids, kind, title, body, project=None, post=None, email=True, reason=''):
    """
    One notification for each of ``user_ids`` who can still act in the
    workspace and, with ``email``, its email task once the rows are committed.
    """
    # In one order: two events for the same people never wait for each other's rows (the prune deletes).
    targets = sorted(recipients_of(workspace) & set(user_ids))
    with transaction.atomic():
        created = []
        for user_id in targets:
            row = Notification.objects.create(
                user_id=user_id, workspace=workspace, kind=kind, title=title[:300], body=body[:300],
                project=project, post=post, reason=reason,
            )
            created.append(row.pk)
            _prune(user_id, workspace.pk)
        if email and created and kind in EMAIL_KINDS:
            transaction.on_commit(lambda: _queue_emails(kind, created))
    return len(targets)


def write(workspace, user_ids, kind, title, body, project=None, post=None):
    """
    For the demo seed (seed_demo), which writes example notifications with the
    texts of the real events: only the bell, no email (nothing just happened).
    """
    return _write(workspace, user_ids, kind, title, body, project=project, post=post, email=False)


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
    """
    Tokens that went back to the wallet for this project since ``since``: the
    moment the caller took right before its refund step, so a refund of an
    earlier event is never counted again. None: the event returns nothing.
    """
    total = Decimal('0')
    if since is None:
        return total
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


def _holds_tokens(project):
    """Something of the project was charged, or is still reserved: "nothing was charged" would be false."""
    return _reservations(project).filter(
        Q(status=Transaction.Status.COMPLETED, amount__gt=0) | Q(status=Transaction.Status.RESERVED),
    ).exists()


def _to_workspace(project, kind, title, body, reason=''):
    workspace = project.workspace
    return _write(workspace, recipients_of(workspace), kind, title, body, project=project, reason=reason)


def project_ready_message(project):
    """(title, body) of ``project_ready``."""
    clips = VideoClip.objects.filter(project=project).count()
    found = 'Encontramos 1 clip.' if clips == 1 else f'Encontramos {clips} clips.'
    return f'{project.title[:_TITLE_ROOM]} está listo para revisar', found


def render_finished_message(project, returned=0):
    """
    (title, body) of ``render_finished``, for a project that ended completed
    or partial. ``returned``: the tokens that close gave back. A completed
    project that returns some had a clip rendered again that failed: the clip
    went back to the file it had.
    """
    refund = f' Te devolvimos {_tokens(returned)}.' if returned > 0 else ''
    if project.status == VideoProject.Status.PARTIAL:
        body = f'Parte del render falló.{refund}'
    elif returned > 0:
        body = f'No pudimos volver a generar todos los clips: los que ya tenías quedaron como estaban.{refund}'
    else:
        ready = VideoClip.objects.filter(project=project, status=VideoClip.Status.COMPLETED).count()
        body = 'Tu video está listo.' if ready <= 1 else f'{ready} clips listos.'
    return f'{project.title[:_TITLE_ROOM]}: terminó el render', body


def project_failed_message(project_title, returned=0, holds_tokens=False, has_clips=False, rendering=False, reason=None):
    """
    (title, body) of ``project_failed``. The body never says more than what
    happened to the tokens: what went back (``returned``); "nothing was
    charged" only when nothing of the project is charged or reserved
    (``holds_tokens``); otherwise no money line, only that the clips are
    still there when there are some. ``reason`` AUTO_RENDER_UNPAID has its
    own text, the web's for that error code.
    """
    title = project_title[:_TITLE_ROOM]
    if reason == AUTO_RENDER_UNPAID:
        return (
            f'{title}: no alcanzó el saldo para renderizar',
            'Los clips siguen ahí. Cargá tokens y volvé a intentarlo.',
        )
    if returned > 0:
        body = f'Te devolvimos {_tokens(returned)}.'
    elif not holds_tokens:
        body = 'No se cobró nada.'
    else:
        body = 'Los clips que ya estaban siguen en el proyecto.' if has_clips else ''
    return f"{title} {'no se pudo generar' if rendering else 'no se pudo procesar'}", body


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


def low_balance_message(available):
    """
    (title, body) of ``low_balance``. The balance is cut to cents, never
    rounded up: the text does not say there is more than there is.
    """
    left = Decimal(available).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    title = 'Te queda 1 token' if left == 1 else f'Te quedan {_tokens(left)}'
    return title, 'El saldo quedó por debajo de lo que cuesta un video promedio.'


# Failure codes of a post (the API's `error_code`) that mean "it may be out".
POST_MAYBE_OUT = ('outcome_unknown', 'duplicate_content')
# ``reason`` of the one notification of a network that was disconnected with several posts to go.
NETWORK_DISCONNECTED = 'network_disconnected'
# A workspace is told of this many failed posts one by one within POST_BURST_WINDOW; the next one
# writes a single notification for all that follow (``reason`` SEVERAL_POSTS_FAILED) and the rest
# of the window is silent. A broker or worker outage can fail every post of a bulk create (60) or
# of a sweep at once: one notification and one email each would empty the bell of everything else.
POST_BURST_LIMIT = 3
POST_BURST_WINDOW = timedelta(minutes=10)
SEVERAL_POSTS_FAILED = 'several_posts_failed'


def post_failed_message(post):
    """
    (title, body, reason) of ``post_failed`` for a FAILED post. The title
    names the clip (its project when it has no title) and the network. The
    body is the API's `error_message` of the post, the words Publicaciones
    shows for its code: it never has text a user typed, so the email carries
    it. ``reason`` is the code (the API's `error_code`).
    """
    from apps.videos.serializers_social import ScheduledPostSerializer

    serializer = ScheduledPostSerializer()
    code = serializer.get_error_code(post) or ''
    clip = post.video_clip
    name = (clip.title or clip.project.title)[:_TITLE_ROOM - 15]
    network = post.get_platform_display()
    if code in POST_MAYBE_OUT:
        title = f'{name}: no sabemos si se publicó en {network}'
    else:
        title = f'{name}: no se pudo publicar en {network}'
    return title, serializer.get_error_message(post) or '', code[:32]


def network_disconnected_message(network, posts):
    """(title, body) of the ``post_failed`` of a disconnected network with ``posts`` (2 or more) that will not go out."""
    return (
        f'Se desconectó {network}: {posts} publicaciones no van a salir',
        f'Reconectá la cuenta de {network} y reintentalas desde Publicaciones.',
    )


def several_posts_failed_message():
    """(title, body) of the one ``post_failed`` that stands for the failures after POST_BURST_LIMIT."""
    return (
        'Fallaron varias publicaciones en pocos minutos',
        'No te avisamos de cada una: revisalas en Publicaciones y reintentalas.',
    )


def buyers_of(workspace):
    """The ids of who can buy tokens in the workspace: its owner and its ACTIVE admins."""
    admins = WorkspaceMember.objects.filter(
        workspace=workspace, status=WorkspaceMember.Status.ACTIVE, role=WorkspaceMember.Role.ADMIN,
    ).values_list('user_id', flat=True)
    return {workspace.owner_id, *admins} - {None}


def notify_project_ready(project_id):
    """The clips are selected and the project waits for the review (``awaiting_approval``)."""
    def build():
        project = _project(project_id)
        if project is None or project.status != VideoProject.Status.AWAITING_APPROVAL:
            return
        _to_workspace(project, Kind.PROJECT_READY, *project_ready_message(project))

    _after_commit('project_ready', build, project_id=project_id)


def notify_render_finished(project_id, since=None):
    """
    The render ended: every clip delivered (``completed``) or only some
    (``partial``). ``since``: ``timezone.now()`` taken before the close
    settled its reservations, to say what it returned (None: nothing).
    """
    def build():
        project = _project(project_id)
        if project is None or project.status not in (VideoProject.Status.COMPLETED, VideoProject.Status.PARTIAL):
            return
        _to_workspace(project, Kind.RENDER_FINISHED, *render_finished_message(project, _returned(project, since)))

    _after_commit('render_finished', build, project_id=project_id)


def clip_render_failed_message(project, reservation):
    """
    (title, body) of the ``render_finished`` of one clip's render (its editor,
    a retry) that gave nothing: ``reservation`` was returned whole. The body
    is the text of the project's activity for that render, word for word
    (``project_activity.refunded_render_text``): what stayed and what came
    back. It has no title of a clip: the email carries it.
    """
    from apps.videos.services.project_activity import refunded_render_text

    return f'{project.title[:_TITLE_ROOM]}: no se pudo renderizar un clip', refunded_render_text(project, reservation)


def notify_clip_render_finished(project_id, reservation_id):
    """
    The render of one clip closed (``reservation_id`` paid it). Delivered, it
    tells nobody: who asked for it is watching the clip, and the project
    already told its render ended. Returned whole (the render failed, and the
    clip went back to what it was), one ``render_finished`` with the words of
    the activity and the reason ``Notification.CLIP_RENDER_FAILED``, which
    gives its email its own subject and text. Call it after the settle.
    """
    def build():
        project = _project(project_id)
        reservation = Transaction.objects.filter(pk=reservation_id, status=Transaction.Status.FAILED).first()
        if project is None or reservation is None:
            return
        # The reason tells the email it is not a render that ended (emails._content).
        _to_workspace(
            project, Kind.RENDER_FINISHED, *clip_render_failed_message(project, reservation),
            reason=Notification.CLIP_RENDER_FAILED,
        )

    _after_commit('clip_render_finished', build, project_id=project_id)


def notify_project_failed(project_id, since=None, rendering=False, reason=None):
    """
    The project ended ``failed``. ``since``: ``timezone.now()`` taken before
    the failure returned its reservations, to say what went back (None: it
    returns nothing). Call it after the refund, or in its transaction.
    ``rendering``: it was the final render that failed, not the processing.
    ``reason``: AUTO_RENDER_UNPAID for a re-dispatch that could not pay the
    clips left to render.
    """
    def build():
        project = _project(project_id)
        if project is None or project.status != VideoProject.Status.FAILED:
            return
        title, body = project_failed_message(
            project.title, _returned(project, since), holds_tokens=_holds_tokens(project),
            has_clips=VideoClip.objects.filter(project=project).exists(), rendering=rendering, reason=reason,
        )
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


def notify_low_balance(workspace_id):
    """
    A reservation left the workspace's available balance under
    ``low_balance_threshold`` of its plan (the web app's "saldo bajo"). For
    who can buy tokens: the owner and the admins. Called by
    ``wallet_service.track_low_balance``, which sends it once per fall.
    Nothing is written if the balance is back at the threshold by the time
    the reservation commits.
    """
    def build():
        from apps.payments.services.estimate import low_balance_threshold

        workspace = Workspace.objects.select_related('subscription_plan').filter(pk=workspace_id).first()
        if workspace is None:
            return
        available = Wallet.objects.filter(workspace=workspace).values_list('available_balance', flat=True).first()
        if available is None or available >= low_balance_threshold(workspace.subscription_plan):
            return
        _write(workspace, buyers_of(workspace), Kind.LOW_BALANCE, *low_balance_message(available))

    _after_commit('low_balance', build, workspace_id=workspace_id)


def _failed_post(post_id):
    return (
        ScheduledPost.objects.select_related('video_clip__project__workspace')
        .filter(pk=post_id, status=ScheduledPost.Status.FAILED).first()
    )


def _write_post_failed(post):
    project = post.video_clip.project
    workspace = project.workspace
    recent = Notification.objects.filter(
        workspace=workspace, kind=Kind.POST_FAILED, created_at__gte=timezone.now() - POST_BURST_WINDOW,
    )
    if recent.filter(reason=SEVERAL_POSTS_FAILED).exists():
        return  # already told that several are failing
    if recent.exclude(post=None).values('post_id').distinct().count() >= POST_BURST_LIMIT:
        _write(
            workspace, recipients_of(workspace), Kind.POST_FAILED, *several_posts_failed_message(),
            reason=SEVERAL_POSTS_FAILED,
        )
        return
    title, body, reason = post_failed_message(post)
    _write(workspace, recipients_of(workspace), Kind.POST_FAILED, title, body, project=project, post=post, reason=reason)


def notify_post_failed(post_id):
    """
    A scheduled post ended FAILED: it did not go out (or nobody knows if it
    did), and someone has to retry it, fix its account or write its text. For
    the workspace's owner and its ACTIVE members, with a link to the post in
    Publicaciones.

    Call it once per failure, from the write that moved the post to FAILED,
    never from one that writes FAILED over FAILED: a post that is retried and
    fails again is a new failure and tells again. Nothing is written if the
    post is gone or no longer FAILED by the time the write commits (a retry
    was faster).

    A burst is bounded: after POST_BURST_LIMIT posts of a workspace told one
    by one within POST_BURST_WINDOW, the next failure writes one notification
    for all that follow, and the rest of the window writes nothing.
    """
    def build():
        post = _failed_post(post_id)
        if post is not None:
            _write_post_failed(post)

    _after_commit('post_failed', build, post_id=post_id)


def notify_network_posts_failed(post_ids):
    """
    A network was disconnected and ``post_ids`` (its posts that were going
    out, all of one workspace and one network) turned FAILED. One post tells
    like any other failure. Several tell once for all of them, with a link to
    Publicaciones: a notification and an email per post would bury the bell,
    which keeps ``KEPT_PER_USER``.
    """
    post_ids = list(post_ids)

    def build():
        posts = list(
            ScheduledPost.objects.select_related('video_clip__project__workspace')
            .filter(pk__in=post_ids, status=ScheduledPost.Status.FAILED).order_by('pk')
        )
        if len(posts) == 1:
            _write_post_failed(posts[0])
        elif posts:
            workspace = posts[0].video_clip.project.workspace
            title, body = network_disconnected_message(posts[0].get_platform_display(), len(posts))
            _write(workspace, recipients_of(workspace), Kind.POST_FAILED, title, body, reason=NETWORK_DISCONNECTED)

    if post_ids:
        _after_commit('network_posts_failed', build, posts=len(post_ids))
