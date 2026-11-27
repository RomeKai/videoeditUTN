"""
Workspaces and their members: `/api/v1/workspaces/` (contract:
front/src/api/contracts/workspaces.ts).

The workspace comes in the PATH, so the role is checked in that workspace
(core.permissions.require_workspace_role) and never in the one of
`X-Workspace-Id`: a 404 for anyone who neither owns it nor is an ACTIVE member,
a 403 with only `detail` for a role the ROLE_MATRIX leaves out.

Invitations (create, resend, read, accept) are in views_invitations.
"""
import logging
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ADMIN, require_workspace_role
from apps.core.workspace import OWNER
from apps.integrations.ayrshare_api import AyrshareClient
from apps.payments.models import Payment, Transaction, Wallet
from apps.payments.services.payments import payment_pending_message
from apps.users.models import User, Workspace, WorkspaceMember
from apps.users.seats import seat_limit
from apps.users.serializers_workspaces import (
    ALREADY_OWNER,
    CONFIRM_NAME_MISMATCH,
    NEW_OWNER_NOT_ACTIVE,
    NO_SEAT_FOR_PREVIOUS_OWNER,
    OWNER_NOT_REMOVABLE,
    OWNER_ROLE_FIXED,
    DeleteWorkspaceSerializer,
    MemberSerializer,
    MembersSerializer,
    TransferWorkspaceSerializer,
    UpdateMemberSerializer,
    WorkspaceDetailSerializer,
    WorkspaceNameSerializer,
    WorkspaceSerializer,
)
from apps.users.throttles import AttemptThrottle, SpanishThrottleMixin
from apps.users.workspaces import create_owned_workspace
from apps.videos.models import ScheduledPost, VideoProject

logger = logging.getLogger(__name__)

ACTIVE = WorkspaceMember.Status.ACTIVE

MEMBER_NOT_FOUND = 'No encontramos a ese miembro.'
WORKSPACE_BUSY = (
    'Hay proyectos procesándose o renderizándose en este espacio: esperá a que terminen para borrarlo. '
    'Si alguno quedó trabado, borrá ese proyecto primero.'
)
WORKSPACE_RESERVED = (
    'El espacio tiene tokens reservados que todavía no se liquidaron. Probá de nuevo en unos minutos.'
)
POSTS_IN_FLIGHT = (
    'Hay publicaciones saliendo a las redes en este momento: esperá a que terminen para borrar el espacio. '
    'Si alguna quedó trabada, se libera sola un rato después de su hora de publicación: probá de nuevo más tarde.'
)
LAST_WORKSPACE = (
    'Es tu único espacio de trabajo y no se puede borrar. Creá otro primero, o sumate al de alguien más.'
)
WORKSPACE_LIMIT = 'Llegaste al máximo de {limit} espacios de trabajo propios. Borrá uno para crear otro.'

# How many workspaces one user may own (POST /workspaces/). Each is a free plan and a wallet.
MAX_OWNED_WORKSPACES = 10


def _tokens(amount):
    """A balance as the user reads it: no trailing zeros, never an exponent."""
    text = f'{amount:f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _balance_left(amount):
    one = amount == 1
    return (
        f'{"Queda" if one else "Quedan"} {_tokens(amount)} {"token" if one else "tokens"} en este espacio y no se '
        f'pueden recuperar después de borrarlo. Usalos o escribinos antes de borrarlo.'
    )


UNAUTHORIZED = OpenApiResponse(description='Sin sesión, o con un token vencido o revocado.')
NOT_FOUND = OpenApiResponse(description='El workspace no existe o el usuario no es dueño ni miembro activo.')
FORBIDDEN = OpenApiResponse(description='El rol del usuario en ese workspace no puede hacerlo: solo `detail`.')
INVALID = OpenApiResponse(description='Validación por campo: `{campo: [mensajes]}`.')
THROTTLED = OpenApiResponse(description='Demasiados intentos: `detail` dice en castellano cuánto esperar, y va Retry-After.')

# What cannot be deleted under: a project the pipeline or a render may still touch.
_BUSY = (VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING, VideoProject.Status.RENDERING)
# A post counts as going out only while its task can still be working on it.
# PROCESSING: for as long as the task holds its lock (tasks.social: the slowest
# Ayrshare answer plus a margin) since the attempt started (`updated_at`).
POST_PROCESSING_WINDOW = timedelta(seconds=AyrshareClient.TIMEOUT_SECONDS[1] + 300)
# QUEUED: until its task is due (`publish_at`), plus its retries for the lock
# (60 + 120 + 240 s) and a late worker.
POST_QUEUED_GRACE = timedelta(minutes=15)


def _post_in_flight(status, updated_at, publish_at, now):
    """
    Older rows are stuck (a lost message, a worker that died): they never end
    on their own, so they must not keep the workspace forever. If their task
    does run later, it finds no post and ends (tasks.social).
    """
    if status == ScheduledPost.Status.PROCESSING:
        return updated_at >= now - POST_PROCESSING_WINDOW
    if status == ScheduledPost.Status.QUEUED:
        return publish_at >= now - POST_QUEUED_GRACE
    return False


def _full_name(user):
    return f'{user.first_name} {user.last_name}'.strip()


def _workspace_payload(workspace, role):
    return {
        'id': workspace.pk,
        'name': workspace.name,
        'owner': workspace.owner_id,
        'subscription_plan': workspace.subscription_plan_id,
        'created_at': workspace.created_at,
        'role': role,
    }


def _member_payload(member, workspace):
    """
    A pending invitation never carries `user`, even when its email has an
    account: whoever invites an address must not learn whose it is.
    """
    user = member.user if member.status == ACTIVE else None
    inviter = member.invited_by
    return {
        'id': member.pk,
        'user': {'id': user.pk, 'name': _full_name(user), 'email': user.email} if user is not None else None,
        'email': user.email if user is not None else (member.invited_email or member.user.email),
        'role': OWNER if user is not None and member.user_id == workspace.owner_id else member.role,
        'status': member.status,
        'joined_at': member.joined_at,
        'invited_by': {'id': inviter.pk, 'name': _full_name(inviter)} if inviter is not None else None,
    }


def _admins(workspace):
    """The owner, then the ACTIVE admins by seniority: who a member asks for access."""
    owner = workspace.owner
    people = [{'id': owner.pk, 'name': _full_name(owner) or owner.email, 'role': OWNER}]
    rows = (
        WorkspaceMember.objects.filter(workspace=workspace, role=ADMIN, status=ACTIVE)
        .exclude(user_id=workspace.owner_id)
        .select_related('user')
        .order_by('joined_at', 'pk')
    )
    people += [{'id': row.user_id, 'name': _full_name(row.user) or row.user.email, 'role': ADMIN} for row in rows]
    return people


def _member_or_404(workspace, member_id, lock=False):
    """The membership row (ACTIVE or a pending invitation) of THIS workspace."""
    try:
        pk = uuid.UUID(str(member_id))
    except ValueError:
        raise NotFound(MEMBER_NOT_FOUND)
    queryset = WorkspaceMember.objects.select_for_update() if lock else WorkspaceMember.objects.all()
    member = queryset.filter(pk=pk, workspace=workspace).first()
    if member is None:
        raise NotFound(MEMBER_NOT_FOUND)
    return member


def _error(code_status, message, code, **extra):
    return Response({'error': message, 'code': code, **extra}, status=code_status)


def _other_workspaces(user, workspace):
    """Whether the user owns, or is an ACTIVE member of, a workspace other than this one."""
    return (
        Workspace.objects.filter(Q(owner=user) | Q(workspacemember__user=user, workspacemember__status=ACTIVE))
        .exclude(pk=workspace.pk)
        .exists()
    )


class WorkspaceCreateThrottle(AttemptThrottle):
    """
    Only the POSTs count (scope `workspace_create`, per user): listing stays
    unlimited. AttemptThrottle keeps limiting while the shared cache is down.
    """

    def allow_request(self, request, view):
        if request.method != 'POST':
            return True
        return super().allow_request(request, view)


class WorkspaceListView(SpanishThrottleMixin, APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [WorkspaceCreateThrottle]
    throttle_scope = 'workspace_create'

    @extend_schema(responses={200: WorkspaceSerializer(many=True), 401: UNAUTHORIZED})
    def get(self, request):
        """The workspaces the user owns or is an ACTIVE member of, oldest first, with their role in each."""
        user = request.user
        roles = dict(
            WorkspaceMember.objects.filter(user=user, status=ACTIVE).values_list('workspace_id', 'role')
        )
        workspaces = Workspace.objects.filter(Q(owner=user) | Q(pk__in=list(roles))).order_by('created_at', 'pk')
        payload = [
            _workspace_payload(workspace, OWNER if workspace.owner_id == user.pk else roles[workspace.pk])
            for workspace in workspaces
        ]
        return Response(WorkspaceSerializer(payload, many=True).data)

    @extend_schema(
        request=WorkspaceNameSerializer,
        responses={
            201: WorkspaceSerializer,
            400: INVALID,
            401: UNAUTHORIZED,
            409: OpenApiResponse(description='`{error, code: "workspace_limit"}`: el usuario ya es dueño del máximo de workspaces.'),
            429: THROTTLED,
        },
    )
    def post(self, request):
        """
        A new workspace of the user's: default plan, empty wallet, the user as
        owner. At most MAX_OWNED_WORKSPACES owned by one user (409
        `workspace_limit`), and a rate per user (scope `workspace_create`).
        """
        serializer = WorkspaceNameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            # The user's row lock: two creations at once count one after the other.
            User.objects.select_for_update().filter(pk=request.user.pk).first()
            if Workspace.objects.filter(owner=request.user).count() >= MAX_OWNED_WORKSPACES:
                return _error(409, WORKSPACE_LIMIT.format(limit=MAX_OWNED_WORKSPACES), 'workspace_limit')
            workspace = create_owned_workspace(request.user, serializer.validated_data['name'])
        logger.info('workspace.created workspace_id=%s user_id=%s', workspace.pk, request.user.pk)
        return Response(WorkspaceSerializer(_workspace_payload(workspace, OWNER)).data, status=status.HTTP_201_CREATED)


class WorkspaceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: WorkspaceDetailSerializer, 401: UNAUTHORIZED, 404: NOT_FOUND})
    def get(self, request, workspace_id):
        """Every role reads it. `admins` says who to ask for access."""
        active = require_workspace_role(request.user, workspace_id, 'project.view')
        payload = {**_workspace_payload(active.workspace, active.role), 'admins': _admins(active.workspace)}
        return Response(WorkspaceDetailSerializer(payload).data)

    @extend_schema(
        request=WorkspaceNameSerializer,
        responses={200: WorkspaceSerializer, 400: INVALID, 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND},
    )
    def patch(self, request, workspace_id):
        """Renames it: owner and admins."""
        with transaction.atomic():
            active = require_workspace_role(request.user, workspace_id, 'workspace.rename', lock=True)
            serializer = WorkspaceNameSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            workspace = active.workspace
            workspace.name = serializer.validated_data['name']
            workspace.save(update_fields=['name'])
        return Response(WorkspaceSerializer(_workspace_payload(workspace, active.role)).data)

    @extend_schema(
        request=DeleteWorkspaceSerializer,
        responses={
            204: None,
            400: OpenApiResponse(description='`confirm_name` falta o no es el nombre del workspace.'),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: NOT_FOUND,
            409: OpenApiResponse(description=(
                '`{error, code}`, y no se borra nada: `project_processing` con un proyecto subido, en ingesta o '
                'renderizando; `posts_in_flight` con una publicación en cola o saliendo (una trabada no cuenta); '
                '`reservation_open` con tokens reservados sin liquidar; `payment_pending` con una compra de tokens o un '
                'pago de plan pendiente; `balance_left` con saldo disponible (trae `available_balance`); '
                '`last_workspace`, que se revisa primero, si es el único workspace del usuario.'
            )),
        },
    )
    def delete(self, request, workspace_id):
        """
        Deletes the workspace for good: only its owner, typing its name.

        A 409 `{error, code}`, with nothing deleted, while any of these holds
        (checked in this order):

        - `last_workspace`: the only workspace the owner owns or is an ACTIVE
          member of. Nobody is left without a place to work. First, because
          it is the only one that waiting does not fix.
        - `project_processing`: a project uploaded, ingesting or rendering. A
          stuck one keeps the workspace until that project is deleted (its own
          delete returns its reservations).
        - `posts_in_flight`: a post its task may be sending right now: PROCESSING
          since less than POST_PROCESSING_WINDOW, or QUEUED and not more than
          POST_QUEUED_GRACE past its `publish_at`. An older one is stuck and
          does not keep the workspace.
        - `reservation_open`: a reservation still RESERVED, or reserved balance.
          Nothing would settle those tokens once the wallet is gone.
        - `payment_pending`: a Payment still pending (a token purchase or a
          plan payment, with its checkout open or still opening). Its row is
          what a confirmation settles, and what a late payment is logged
          against (`payment.late_paid`): it must not go with the workspace.
          It ends when the provider settles it or when its checkout expires
          (PAYMENTS_CHECKOUT_TTL_SECONDS). The web cannot cancel a token
          purchase (a plan payment it can, with «Seguir con …» in Planes),
          so the sentence, one for both, says to pay it or wait.
        - `balance_left`: available balance above zero. Tokens are money and
          are never deleted; there is no way to move them yet.

        What goes with it (the models' CASCADE): its projects with their clips
        and posts (DRAFT, SCHEDULED, PUBLISHED and FAILED ones; a SCHEDULED post
        is never published), its brand kits, its memberships and pending
        invitations, its settled payments, and its empty wallet with its
        transactions.
        `User.last_workspace` of whoever had it chosen becomes null, and /me
        falls back to their default. The files in R2 and the Ayrshare profile
        are not cleaned up here.
        """
        # Who may delete it (the same 404 or 403), before any lock.
        workspace_pk = require_workspace_role(request.user, workspace_id, 'workspace.delete').workspace.pk
        with transaction.atomic():
            # The projects first, then the workspace's row. Whatever locks a project and
            # writes its row twice (a paid render, an approval, a stage of the pipeline)
            # also takes the workspace's row when it commits: FOR KEY SHARE, Postgres's
            # check of the project's foreign key, deferred to the commit. A delete that
            # held the workspace and waited for that project deadlocked with it. Locked
            # in pk order, and the wallet after them (reserve_funds: project, then
            # wallet): an approval, a paid render or a creation's reservation that starts
            # meanwhile waits for this delete, or this delete for it.
            projects = list(
                VideoProject.objects.select_for_update()
                .filter(workspace_id=workspace_pk)
                .order_by('pk')
                .values_list('pk', 'status')
            )
            active = require_workspace_role(request.user, workspace_id, 'workspace.delete', lock=True)
            workspace = active.workspace
            serializer = DeleteWorkspaceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if serializer.validated_data['confirm_name'] != workspace.name:
                raise ValidationError({'confirm_name': [CONFIRM_NAME_MISMATCH]})
            # First: it is the one refusal that waiting does not fix. Under the user's row
            # lock (the one a creation takes), so two deletes of the same person's last two
            # workspaces count one after the other. NO KEY: rows that point at the user
            # (a project, a membership) can still be written meanwhile.
            User.objects.select_for_update(no_key=True).filter(pk=request.user.pk).first()
            if not _other_workspaces(request.user, workspace):
                return _error(409, LAST_WORKSPACE, 'last_workspace')
            # A project created after the projects were locked and before the workspace's
            # row was: its INSERT held that row until it committed, so it is visible now,
            # and none can start until this ends. It is new, so it is processing.
            if VideoProject.objects.filter(workspace=workspace).exclude(pk__in=[pk for pk, _ in projects]).exists():
                return _error(409, WORKSPACE_BUSY, 'project_processing')
            statuses = [project_status for _, project_status in projects]
            # The post's lock is the one its publishing task takes (tasks.social): a post
            # the dispatcher queues meanwhile waits, and then is gone with its workspace.
            posts = list(
                ScheduledPost.objects.select_for_update(of=('self',))
                .filter(video_clip__project__workspace=workspace)
                .order_by('pk')
                .values_list('status', 'updated_at', 'publish_at')
            )
            # Read with the workspace row locked, and before the wallet (the order of settle_payment:
            # workspace, payment, wallet). Everything that creates or settles a payment locks the
            # workspace first, so none starts or ends until this delete does: a purchase that comes
            # meanwhile finds no workspace.
            payment_pending = Payment.objects.filter(workspace=workspace, status=Payment.Status.PENDING).exists()
            wallet = Wallet.objects.select_for_update().filter(workspace=workspace).first()
            if any(project_status in _BUSY for project_status in statuses):
                return _error(409, WORKSPACE_BUSY, 'project_processing')
            now = timezone.now()
            if any(_post_in_flight(*post, now) for post in posts):
                return _error(409, POSTS_IN_FLIGHT, 'posts_in_flight')
            if wallet is not None and (
                wallet.reserved_balance > 0
                or Transaction.objects.filter(wallet=wallet, status=Transaction.Status.RESERVED).exists()
            ):
                return _error(409, WORKSPACE_RESERVED, 'reservation_open')
            if payment_pending:
                return _error(409, payment_pending_message(), 'payment_pending')
            if wallet is not None and wallet.available_balance > 0:
                return _error(
                    409, _balance_left(wallet.available_balance), 'balance_left',
                    available_balance=_tokens(wallet.available_balance),
                )
            pk = workspace.pk
            # Zero by the checks above; logged so the ledger that goes with the wallet leaves a trace.
            available = _tokens(wallet.available_balance) if wallet is not None else None
            reserved = _tokens(wallet.reserved_balance) if wallet is not None else None
            transactions = Transaction.objects.filter(wallet=wallet).count() if wallet is not None else 0
            workspace.delete()
        logger.warning(
            'workspace.deleted workspace_id=%s user_id=%s wallet_available=%s wallet_reserved=%s '
            'wallet_transactions=%d projects=%d posts=%d',
            pk, request.user.pk, available, reserved, transactions, len(statuses), len(posts),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceTransferView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TransferWorkspaceSerializer,
        responses={
            200: WorkspaceSerializer,
            400: OpenApiResponse(description=(
                '`new_owner_id` no es un miembro activo del workspace, o es el dueño. `detail`: el dueño '
                'anterior no tenía fila de miembro y no queda lugar en el plan para dejarlo como admin.'
            )),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: NOT_FOUND,
        },
    )
    def post(self, request, workspace_id):
        """
        Gives the workspace to another ACTIVE member: only its owner. The new
        owner's row and the previous owner's stay as ACTIVE Admin rows, so the
        previous owner is an Admin from now on. It is the only way an owner
        stops being one. A previous owner without a membership row needs a
        free seat of the plan for it: without one it is a 400 and nothing changes.
        """
        user = request.user
        with transaction.atomic():
            active = require_workspace_role(user, workspace_id, 'workspace.transfer', lock=True)
            workspace = active.workspace
            serializer = TransferWorkspaceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            new_owner_id = serializer.validated_data['new_owner_id']
            if new_owner_id == user.pk:
                raise ValidationError({'new_owner_id': [ALREADY_OWNER]})
            target = (
                WorkspaceMember.objects.select_for_update()
                .filter(workspace=workspace, user_id=new_owner_id, status=ACTIVE)
                .first()
            )
            if target is None:
                raise ValidationError({'new_owner_id': [NEW_OWNER_NOT_ACTIVE]})
            # The owner changes first: the previous owner's row is then a member's row
            # like any other, and goes through the plan's seats (signals.check_workspace_member_limit).
            workspace.owner_id = new_owner_id
            workspace.save(update_fields=['owner'])
            target.role = ADMIN
            target.save(update_fields=['role'])
            previous = WorkspaceMember.objects.select_for_update().filter(workspace=workspace, user=user).first()
            if previous is not None:
                # The usual case: the row exists and keeps its seat.
                previous.role, previous.status = ADMIN, ACTIVE
                previous.save(update_fields=['role', 'status'])
            else:
                try:
                    WorkspaceMember.objects.create(workspace=workspace, user=user, role=ADMIN, status=ACTIVE)
                except DjangoValidationError:
                    # No seat left for them: nothing is transferred (the transaction rolls back).
                    raise ValidationError({'detail': NO_SEAT_FOR_PREVIOUS_OWNER})
        logger.warning(
            'workspace.transferred workspace_id=%s from_user_id=%s to_user_id=%s', workspace.pk, user.pk, new_owner_id,
        )
        return Response(WorkspaceSerializer(_workspace_payload(workspace, ADMIN)).data)


class WorkspaceMembersView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: MembersSerializer, 401: UNAUTHORIZED, 403: FORBIDDEN, 404: NOT_FOUND})
    def get(self, request, workspace_id):
        """
        Members and pending invitations, oldest first: owner and admins.
        `seats_used` counts every row, pending ones too, against `max_members`:
        the limit that inviting checks (seats.seat_limit). It is the plan's
        (one seat without a plan), or the smaller one of a scheduled downgrade
        or of a plan payment waiting for its checkout; `limited_by` says which.
        """
        active = require_workspace_role(request.user, workspace_id, 'members.manage')
        workspace = active.workspace
        members = list(
            WorkspaceMember.objects.filter(workspace=workspace).select_related('user', 'invited_by').order_by('joined_at', 'pk')
        )
        limit = seat_limit(workspace)
        return Response(MembersSerializer({
            'members': [_member_payload(member, workspace) for member in members],
            'max_members': limit.limit,
            'seats_used': len(members),
            'limited_by': limit.limited_by,
        }).data)


class WorkspaceMemberView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=UpdateMemberSerializer,
        responses={
            200: MemberSerializer,
            400: OpenApiResponse(description='`role` no es admin, editor ni viewer, o el miembro es el dueño.'),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: OpenApiResponse(description='El workspace o el miembro no existen para este usuario.'),
        },
    )
    def patch(self, request, workspace_id, member_id):
        """Changes a member's (or a pending invitation's) role: owner and admins. Never the owner's."""
        with transaction.atomic():
            active = require_workspace_role(request.user, workspace_id, 'members.manage', lock=True)
            workspace = active.workspace
            member = _member_or_404(workspace, member_id, lock=True)
            serializer = UpdateMemberSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if member.user_id == workspace.owner_id:
                # The owner stops being one only through a transfer.
                raise ValidationError({'role': [OWNER_ROLE_FIXED]})
            member.role = serializer.validated_data['role']
            member.save(update_fields=['role'])
        member = WorkspaceMember.objects.select_related('user', 'invited_by').get(pk=member.pk)
        return Response(MemberSerializer(_member_payload(member, workspace)).data)

    @extend_schema(
        responses={
            204: None,
            400: OpenApiResponse(description='El miembro es el dueño: `detail`.'),
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: OpenApiResponse(description='El workspace o el miembro no existen para este usuario.'),
        },
    )
    def delete(self, request, workspace_id, member_id):
        """
        Removes a member or cancels a pending invitation: owner and admins.
        The row is deleted and its seat is free again. Never the owner's row,
        whoever asks: an admin cannot remove the owner and the owner cannot
        leave (they transfer the workspace first). An admin may remove
        themselves.
        """
        with transaction.atomic():
            active = require_workspace_role(request.user, workspace_id, 'members.manage', lock=True)
            workspace = active.workspace
            member = _member_or_404(workspace, member_id, lock=True)
            if member.user_id == workspace.owner_id:
                raise ValidationError({'detail': OWNER_NOT_REMOVABLE})
            member.delete()
        logger.info('workspace.member_removed workspace_id=%s member_id=%s user_id=%s', workspace.pk, member_id, request.user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
