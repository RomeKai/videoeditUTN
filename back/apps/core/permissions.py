"""
Roles of the active workspace (table 1.7 of documents/producto/mapa-de-pantallas.md).

ROLE_MATRIX has the same actions and roles as MATRIX in
front/src/lib/permissions.ts, so the API refuses what the UI disables.
"""
import uuid

from rest_framework import permissions
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.users.models import Workspace, WorkspaceMember

from .workspace import NOT_FOUND_DETAIL, OWNER, ActiveWorkspace, resolve_active_workspace, role_in

ADMIN = WorkspaceMember.Role.ADMIN
EDITOR = WorkspaceMember.Role.EDITOR
VIEWER = WorkspaceMember.Role.VIEWER

ALL = frozenset({OWNER, ADMIN, EDITOR, VIEWER})
MAKERS = frozenset({OWNER, ADMIN, EDITOR})
ADMINS = frozenset({OWNER, ADMIN})
OWNER_ONLY = frozenset({OWNER})

ROLE_MATRIX = {
    'project.view': ALL,
    'clip.download': ALL,
    'project.create': MAKERS,
    'project.review': MAKERS,
    'project.rename': MAKERS,
    # An Editor deletes only the projects they uploaded (has_object_permission).
    'project.delete': MAKERS,
    'clip.edit': MAKERS,
    'post.publish': MAKERS,
    'brandkit.manage': MAKERS,
    'social.manage': ADMINS,
    'members.manage': ADMINS,
    'wallet.view': ALL,
    'billing.manage': ADMINS,
    'workspace.rename': ADMINS,
    'workspace.delete': OWNER_ONLY,
    'workspace.transfer': OWNER_ONLY,
}

# A write a view did not map to an action: only the owner and admins.
_UNMAPPED_WRITE = 'members.manage'

DENIED = {
    MAKERS: "Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.",
    ADMINS: "Solo el dueño o un admin del espacio pueden hacer esto. Pedile acceso a un admin.",
    OWNER_ONLY: "Solo el dueño del espacio puede hacer esto.",
}
EDITOR_DELETE_DENIED = "Como Editor solo podés borrar los proyectos que subiste vos."


def require_workspace_role(user, workspace_id, action, lock=False):
    """
    The ActiveWorkspace of an endpoint that names its workspace in the PATH
    (``/workspaces/<id>/...``), once the user's role there allows ``action``
    (a ROLE_MATRIX key).

    ``X-Workspace-Id`` and the default workspace are not read: the role is the
    one in the workspace the request changes, wherever the header points. A
    malformed id, a workspace that does not exist, and one the user neither
    owns nor is an ACTIVE member of are the same 404 (NOT_FOUND_DETAIL); a role
    the matrix leaves out is a 403 with only ``detail``, like
    WorkspaceRolePermission's.

    ``lock``: inside a transaction, takes the workspace's row lock first, so
    the role and the owner are the ones this request will change (a transfer
    and a delete of the same workspace run one after the other).
    """
    try:
        pk = workspace_id if isinstance(workspace_id, uuid.UUID) else uuid.UUID(str(workspace_id))
    except ValueError:
        raise NotFound(NOT_FOUND_DETAIL)
    queryset = Workspace.objects.select_for_update() if lock else Workspace.objects.select_related('subscription_plan')
    workspace = queryset.filter(pk=pk).first()
    role = role_in(workspace, user) if workspace is not None else None
    if role is None:
        raise NotFound(NOT_FOUND_DETAIL)
    allowed = ROLE_MATRIX[action]
    if role not in allowed:
        raise PermissionDenied(DENIED[allowed])
    return ActiveWorkspace(workspace, role)


class WorkspaceRolePermission(permissions.BasePermission):
    """
    Resolves the active workspace of the request (404 when the header names one
    the user cannot act in) and checks its role.

    A view maps its requests to ROLE_MATRIX actions with ``workspace_actions``:
    keys are viewset actions (``create``, ``approve_paper_edit``...) or, for a
    plain APIView, HTTP methods. A safe method left out is a read (every role);
    a write left out is for the owner and admins only. A method the view has no
    handler for is let through, so DRF answers 405 and not 403.
    """

    message = DENIED[MAKERS]

    @staticmethod
    def required_action(request, view):
        actions = getattr(view, 'workspace_actions', {})
        action = actions.get(getattr(view, 'action', None)) or actions.get(request.method)
        if action:
            return action
        return 'project.view' if request.method in permissions.SAFE_METHODS else _UNMAPPED_WRITE

    @staticmethod
    def _handled(request, view):
        method = request.method.lower()
        return method in view.http_method_names and hasattr(view, method)

    def has_permission(self, request, view):
        active = resolve_active_workspace(request)
        if active is None:
            # No workspace at all: the querysets are empty and creating answers 400.
            return True
        if not self._handled(request, view):
            return True
        allowed = ROLE_MATRIX[self.required_action(request, view)]
        if active.role in allowed:
            return True
        self.message = DENIED[allowed]
        return False

    def has_object_permission(self, request, view, obj):
        active = resolve_active_workspace(request)
        if (
            active is not None
            and active.role == EDITOR
            and self.required_action(request, view) == 'project.delete'
            and getattr(obj, 'uploaded_by_id', None) != request.user.pk
        ):
            self.message = EDITOR_DELETE_DENIED
            return False
        return True
