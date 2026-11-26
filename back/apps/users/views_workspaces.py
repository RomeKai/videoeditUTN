"""
Workspaces and their members: `/api/v1/workspaces/` (contract:
front/src/api/contracts/workspaces.ts).

The workspace comes in the PATH, so the role is checked in that workspace
(core.permissions.require_workspace_role) and never in the one of
`X-Workspace-Id`: a 404 for anyone who neither owns it nor is an ACTIVE member,
a 403 with only `detail` for a role the ROLE_MATRIX leaves out.

Invitations (create, resend, accept) are not here.
"""
import logging
import uuid

from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ADMIN, require_workspace_role
from apps.core.workspace import OWNER
from apps.payments.models import Transaction, Wallet
from apps.users.models import Workspace, WorkspaceMember
from apps.users.serializers_workspaces import (
    ALREADY_OWNER,
    CONFIRM_NAME_MISMATCH,
    NEW_OWNER_NOT_ACTIVE,
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
from apps.users.workspaces import create_owned_workspace
from apps.videos.models import VideoProject

logger = logging.getLogger(__name__)

ACTIVE = WorkspaceMember.Status.ACTIVE

MEMBER_NOT_FOUND = 'No encontramos a ese miembro.'
WORKSPACE_BUSY = 'Hay proyectos procesándose o renderizándose en este espacio: esperá a que terminen para borrarlo.'
WORKSPACE_RESERVED = (
    'El espacio tiene tokens reservados que todavía no se liquidaron. Probá de nuevo en unos minutos.'
)

UNAUTHORIZED = OpenApiResponse(description='Sin sesión, o con un token vencido o revocado.')
NOT_FOUND = OpenApiResponse(description='El workspace no existe o el usuario no es dueño ni miembro activo.')
FORBIDDEN = OpenApiResponse(description='El rol del usuario en ese workspace no puede hacerlo: solo `detail`.')
INVALID = OpenApiResponse(description='Validación por campo: `{campo: [mensajes]}`.')

# What cannot be deleted under: a project the pipeline or a render may still touch.
_BUSY = (VideoProject.Status.UPLOADED, VideoProject.Status.INGESTING, VideoProject.Status.RENDERING)


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
    user = member.user
    return {
        'id': member.pk,
        'user': {'id': user.pk, 'name': _full_name(user), 'email': user.email},
        'email': user.email,
        'role': OWNER if member.user_id == workspace.owner_id else member.role,
        'status': member.status,
        'joined_at': member.joined_at,
        'invited_by': None,
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


def _error(code_status, message, code):
    return Response({'error': message, 'code': code}, status=code_status)


class WorkspaceListView(APIView):
    permission_classes = [IsAuthenticated]

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
        responses={201: WorkspaceSerializer, 400: INVALID, 401: UNAUTHORIZED},
    )
    def post(self, request):
        """A new workspace of the user's: default plan, empty wallet, the user as owner."""
        serializer = WorkspaceNameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
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
                '`{error, code}`: `project_processing` con un proyecto subido, en ingesta o renderizando; '
                '`reservation_open` con tokens reservados sin liquidar. No se borra nada.'
            )),
        },
    )
    def delete(self, request, workspace_id):
        """
        Deletes the workspace for good: only its owner, typing its name.

        Never with work in progress or tokens set aside: a project uploaded,
        ingesting or rendering, or a reservation still RESERVED (or a wallet
        with reserved balance) is a 409 and nothing goes, as when deleting one
        project (videos.views destroy). Nothing would settle those tokens once
        the wallet is gone.

        What goes with it (the models' CASCADE): its projects with their clips
        and posts, its brand kits, its memberships and pending invitations,
        and its wallet with its transactions. The available balance is not
        returned or moved. `User.last_workspace` of whoever had it chosen
        becomes null, and /me falls back to their default.
        """
        with transaction.atomic():
            active = require_workspace_role(request.user, workspace_id, 'workspace.delete', lock=True)
            workspace = active.workspace
            serializer = DeleteWorkspaceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if serializer.validated_data['confirm_name'] != workspace.name:
                raise ValidationError({'confirm_name': [CONFIRM_NAME_MISMATCH]})
            # Locks in the order an approval or a paid render takes them (project, then
            # wallet: wallet_service.reserve_funds), so one that starts meanwhile waits
            # for this delete instead of deadlocking with it.
            statuses = list(
                VideoProject.objects.select_for_update()
                .filter(workspace=workspace)
                .order_by('pk')
                .values_list('status', flat=True)
            )
            wallet = Wallet.objects.select_for_update().filter(workspace=workspace).first()
            if any(project_status in _BUSY for project_status in statuses):
                return _error(409, WORKSPACE_BUSY, 'project_processing')
            if wallet is not None and (
                wallet.reserved_balance > 0
                or Transaction.objects.filter(wallet=wallet, status=Transaction.Status.RESERVED).exists()
            ):
                return _error(409, WORKSPACE_RESERVED, 'reservation_open')
            pk = workspace.pk
            workspace.delete()
        logger.warning('workspace.deleted workspace_id=%s user_id=%s', pk, request.user.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceTransferView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=TransferWorkspaceSerializer,
        responses={
            200: WorkspaceSerializer,
            400: OpenApiResponse(description='`new_owner_id` no es un miembro activo del workspace, o es el dueño.'),
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
        stops being one.
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
            # Before the owner changes: the owner's own row always fits the plan's seats.
            WorkspaceMember.objects.update_or_create(
                workspace=workspace, user=user, defaults={'role': ADMIN, 'status': ACTIVE},
            )
            target.role = ADMIN
            target.save(update_fields=['role'])
            workspace.owner_id = new_owner_id
            workspace.save(update_fields=['owner'])
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
        `seats_used` counts every row, pending ones too, against the plan's
        `max_members` (one seat without a plan): the count of seats.check_seat_limit.
        """
        active = require_workspace_role(request.user, workspace_id, 'members.manage')
        workspace = active.workspace
        members = list(
            WorkspaceMember.objects.filter(workspace=workspace).select_related('user').order_by('joined_at', 'pk')
        )
        plan = workspace.subscription_plan
        return Response(MembersSerializer({
            'members': [_member_payload(member, workspace) for member in members],
            'max_members': plan.max_members if plan else 1,
            'seats_used': len(members),
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
        member = WorkspaceMember.objects.select_related('user').get(pk=member.pk)
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
