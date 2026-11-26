"""
Roles of the active workspace (table 1.7 of documents/producto/mapa-de-pantallas.md).

ROLE_MATRIX has the same actions and roles as MATRIX in
front/src/lib/permissions.ts, so the API refuses what the UI disables.
Nothing is copied by hand: ``manage.py export_role_matrix`` writes ROLE_MATRIX
to front/src/lib/role-matrix.json, a test on each side compares its own table
with that file, and both fail naming the role and the action that differ.
"""
import json
from pathlib import Path

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

# Order of the roles in the export, from most to least access.
ROLE_ORDER = (OWNER, ADMIN, EDITOR, VIEWER)

# The file the frontend test reads. parents[3] is the repository root; an image
# built from back/ alone does not have front/.
ROLE_MATRIX_FIXTURE = Path(__file__).resolve().parents[3] / 'front' / 'src' / 'lib' / 'role-matrix.json'


def role_matrix_export():
    """ROLE_MATRIX as plain data: ``{action: [role, ...]}``, in a stable order."""
    return {
        'roles': [str(role) for role in ROLE_ORDER],
        'matrix': {
            action: [str(role) for role in ROLE_ORDER if role in allowed]
            for action, allowed in ROLE_MATRIX.items()
        },
    }


def role_matrix_json():
    """The exact text of ROLE_MATRIX_FIXTURE."""
    return json.dumps(role_matrix_export(), indent=2) + '\n'


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
    a write left out is for the owner and admins only. A method the view has no
    handler for is let through, so DRF answers 405 and not 403.
    """

    message = DENIED[MAKERS]

    @staticmethod
    def required_action(request, view):
        actions = getattr(view, 'workspace_actions', {})
        # Django answers HEAD with the GET handler, so it needs GET's action:
        # unmapped, it fell to project.view and a Lector got a 200 (and the
        # work of the GET) where GET is a 403.
        method = 'GET' if request.method == 'HEAD' else request.method
        action = actions.get(getattr(view, 'action', None)) or actions.get(method)
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
