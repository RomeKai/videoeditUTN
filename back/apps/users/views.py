from decimal import Decimal

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.workspace import OWNER, ActiveWorkspace, resolve_active_workspace
from apps.payments.models import Wallet
from apps.users.models import NotificationPreference, Workspace, WorkspaceMember
from apps.users.serializers import (
    MeSerializer,
    NotificationPreferencesSerializer,
    PasswordChangeSerializer,
    UpdateMeSerializer,
)


def _user_workspaces(user):
    """Every workspace the user owns or is an ACTIVE member of. Owning one wins over a membership row."""
    found = {}
    for workspace in Workspace.objects.filter(owner=user).select_related('subscription_plan'):
        found[workspace.pk] = ActiveWorkspace(workspace, OWNER)
    memberships = (
        WorkspaceMember.objects.filter(user=user, status=WorkspaceMember.Status.ACTIVE)
        .select_related('workspace__subscription_plan')
    )
    for membership in memberships:
        found.setdefault(membership.workspace_id, ActiveWorkspace(membership.workspace, membership.role))
    return list(found.values())


def _workspace_summary(active):
    workspace = active.workspace
    plan = workspace.subscription_plan
    wallet = Wallet.objects.filter(workspace=workspace).first()
    return {
        'id': workspace.pk,
        'name': workspace.name,
        'role': active.role,
        'plan': {'id': plan.pk, 'name': plan.name} if plan else None,
        'available_balance': wallet.available_balance if wallet else Decimal('0'),
    }


def _me_payload(request):
    user = request.user
    active = resolve_active_workspace(request)
    return MeSerializer({
        'id': user.pk,
        'email': user.email,
        'name': f'{user.first_name} {user.last_name}'.strip(),
        'is_email_verified': user.is_email_verified,
        'tour_completed': user.tour_completed,
        'terms_accepted_at': user.terms_accepted_at,
        'active_workspace_id': active.workspace.pk if active else None,
        'workspaces': [_workspace_summary(item) for item in _user_workspaces(user)],
    }).data


class MeView(APIView):
    """The signed-in user, their workspaces with their role in each, and the active one."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_me_payload(request))

    def patch(self, request):
        serializer = UpdateMeSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if 'name' in serializer.validated_data:
            # Maps to first_name and last_name: the first word is the first name, the rest the last one.
            user.first_name, _, user.last_name = serializer.validated_data['name'].partition(' ')
        if 'tour_completed' in serializer.validated_data:
            user.tour_completed = serializer.validated_data['tour_completed']
        user.save(update_fields=['first_name', 'last_name', 'tour_completed'])
        return Response(_me_payload(request))


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'user': request.user})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def _preferences(self, user):
        preferences, _ = NotificationPreference.objects.get_or_create(user=user)
        return preferences

    def get(self, request):
        serializer = NotificationPreferencesSerializer(self._preferences(request.user))
        return Response(serializer.data)

    def patch(self, request):
        preferences = self._preferences(request.user)
        serializer = NotificationPreferencesSerializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
