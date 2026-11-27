"""
How a workspace is born, for the sign-up (signals.create_default_workspace) and
for `POST /api/v1/workspaces/` (views_workspaces): one path, so both start the
same.
"""
from apps.payments.services.plans import get_default_plan_id

from .models import Workspace, WorkspaceMember


def create_owned_workspace(user, name):
    """
    A workspace owned by ``user`` on the default plan (settings.DEFAULT_PLAN_NAME,
    'Gratis'), with the owner's membership row as an ACTIVE Admin.

    ``get_default_plan_id`` never raises: without the plan the workspace is
    created with none and it is logged. The empty Wallet comes from the
    post_save signal of Workspace (apps.payments.signals). The owner's row
    always fits the plan's seats (seats.check_seat_limit).
    """
    workspace = Workspace.objects.create(name=name, owner=user, subscription_plan_id=get_default_plan_id())
    WorkspaceMember.objects.create(
        workspace=workspace,
        user=user,
        role=WorkspaceMember.Role.ADMIN,
        status=WorkspaceMember.Status.ACTIVE,
    )
    return workspace
