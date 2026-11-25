"""
Roles of the active workspace (table 1.7 of documents/producto/mapa-de-pantallas.md).

ROLE_MATRIX has the same actions and roles as MATRIX in
front/src/lib/permissions.ts, so the API refuses what the UI disables.
"""
from rest_framework import permissions

from apps.users.models import WorkspaceMember

from .workspace import OWNER, resolve_active_workspace

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


class WorkspaceRolePermission(permissions.BasePermission):
    """
    Resolves the active workspace of the request (404 when the header names one
    the user cannot act in) and checks its role.

    A view maps its requests to ROLE_MATRIX actions with ``workspace_actions``:
    keys are viewset actions (``create``, ``approve_paper_edit``...) or, for a
    plain APIView, HTTP methods. A safe method left out is a read (every role);
    a write left out is for the owner and admins only.
    """

    message = DENIED[MAKERS]

    @staticmethod
    def required_action(request, view):
        actions = getattr(view, 'workspace_actions', {})
        action = actions.get(getattr(view, 'action', None)) or actions.get(request.method)
        if action:
            return action
        return 'project.view' if request.method in permissions.SAFE_METHODS else _UNMAPPED_WRITE

    def has_permission(self, request, view):
        active = resolve_active_workspace(request)
        if active is None:
            # No workspace at all: the querysets are empty and creating answers 400.
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
