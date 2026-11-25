from decimal import Decimal

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.workspace import OWNER, ActiveWorkspace, resolve_active_workspace
from apps.payments.models import Wallet
from apps.users.models import Workspace, WorkspaceMember
from apps.users.serializers import MeSerializer


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


class MeView(APIView):
    """The signed-in user, their workspaces with their role in each, and the active one."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        active = resolve_active_workspace(request)
        data = {
            'id': user.pk,
            'email': user.email,
            'name': f'{user.first_name} {user.last_name}'.strip(),
            'is_email_verified': user.is_email_verified,
            'tour_completed': user.tour_completed,
            'terms_accepted_at': user.terms_accepted_at,
            'active_workspace_id': active.workspace.pk if active else None,
            'workspaces': [_workspace_summary(item) for item in _user_workspaces(user)],
        }
        return Response(MeSerializer(data).data)
