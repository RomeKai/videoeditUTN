"""
The active workspace of a request.

The web app sends it in the ``X-Workspace-Id`` header on every scoped request
(front/src/api/config.ts ``WORKSPACE_HEADER``). Only an ACTIVE membership, or
owning the workspace, gives access: a pending invitation is not a membership
(``Workspace.members`` goes through ``WorkspaceMember``, whose ``status``
starts as PENDING, so ``user.workspaces`` and ``workspace__members`` include
invitations nobody accepted).
"""
import uuid
from dataclasses import dataclass

from rest_framework.exceptions import NotFound

from apps.users.models import Workspace, WorkspaceMember

WORKSPACE_HEADER = 'X-Workspace-Id'

# Role of the workspace's owner (Workspace.owner). The other roles are
# WorkspaceMember.Role; the owner wins over any membership row of their own.
OWNER = 'owner'

# One answer for a malformed id, a workspace that does not exist, one the user
# is not a member of and a pending invitation: workspaces cannot be enumerated.
NOT_FOUND_DETAIL = "No encontramos ese espacio de trabajo."

_UNRESOLVED = object()


@dataclass(frozen=True)
class ActiveWorkspace:
    workspace: Workspace
    # OWNER or a WorkspaceMember.Role value.
    role: str


def role_in(workspace, user):
    """The user's role in the workspace, or None (not a member, or only invited)."""
    if workspace.owner_id == user.pk:
        return OWNER
    return (
        WorkspaceMember.objects.filter(
            workspace=workspace, user=user, status=WorkspaceMember.Status.ACTIVE,
        )
        .values_list('role', flat=True)
        .first()
    )


def _default_workspace(user):
    """
    The workspace of a request without the header: the oldest one the user
    owns, else the oldest ACTIVE membership.

    Clients other than the web app (scripts, curl, integrations written before
    the header) never sent it, and they worked with the user's only workspace.
    They keep working, now always with the same one: the old
    ``user.workspaces.first()`` ordered by a random UUID and counted pending
    invitations.
    """
    owned = Workspace.objects.filter(owner=user).order_by('created_at', 'pk').first()
    if owned is not None:
        return ActiveWorkspace(owned, OWNER)
    membership = (
        WorkspaceMember.objects.filter(user=user, status=WorkspaceMember.Status.ACTIVE)
        .select_related('workspace')
        .order_by('joined_at', 'pk')
        .first()
    )
    if membership is not None:
        return ActiveWorkspace(membership.workspace, membership.role)
    return None


def _from_header(user, raw):
    try:
        workspace_id = uuid.UUID(raw.strip())
    except ValueError:
        raise NotFound(NOT_FOUND_DETAIL)
    workspace = Workspace.objects.filter(pk=workspace_id).first()
    role = role_in(workspace, user) if workspace is not None else None
    if role is None:
        raise NotFound(NOT_FOUND_DETAIL)
    return ActiveWorkspace(workspace, role)


def resolve_active_workspace(request):
    """
    The ActiveWorkspace of an authenticated request, cached on the request.

    With ``X-Workspace-Id``: that workspace if the user owns it or has an
    ACTIVE membership in it; anything else raises NotFound (404). Without the
    header (or with it blank): the default of ``_default_workspace``. None only
    when there is no header and the user has no workspace at all.
    """
    cached = getattr(request, '_active_workspace', _UNRESOLVED)
    if cached is not _UNRESOLVED:
        return cached
    user = request.user
    if not user or not user.is_authenticated:
        return None
    raw = request.headers.get(WORKSPACE_HEADER)
    if raw is not None and raw.strip():
        active = _from_header(user, raw)
    else:
        active = _default_workspace(user)
    request._active_workspace = active
    return active
