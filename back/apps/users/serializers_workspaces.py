"""
Bodies of `/api/v1/workspaces/` (views_workspaces), with the field names of
front/src/api/contracts/workspaces.ts. The messages go to the screen as they are.
"""
from rest_framework import serializers

from apps.users.models import Workspace, WorkspaceMember

NAME_MAX_LENGTH = Workspace._meta.get_field('name').max_length

NAME_MESSAGES = {
    'required': 'Escribí un nombre para el espacio.',
    'null': 'Escribí un nombre para el espacio.',
    'blank': 'Escribí un nombre para el espacio.',
    'invalid': 'Escribí un nombre para el espacio.',
    'max_length': f'El nombre puede tener hasta {NAME_MAX_LENGTH} caracteres.',
}
CONFIRM_NAME_REQUIRED = 'Escribí el nombre del espacio para confirmar.'
CONFIRM_NAME_MISMATCH = 'El nombre no coincide.'
NEW_OWNER_REQUIRED = 'Elegí a quién le transferís el espacio.'
NEW_OWNER_NOT_ACTIVE = 'Tiene que ser un miembro activo del workspace.'
ALREADY_OWNER = 'Ya sos el dueño de este espacio.'
ROLE_INVALID = 'Elegí un rol: Admin, Editor o Lector.'
OWNER_ROLE_FIXED = 'No se puede cambiar el rol del dueño.'
OWNER_NOT_REMOVABLE = 'No se puede quitar al dueño del workspace.'
NO_SEAT_FOR_PREVIOUS_OWNER = (
    'No quedan lugares en el plan para que sigas como admin después de transferir. '
    'Quitá a un miembro o cancelá una invitación, y probá de nuevo.'
)


class WorkspaceNameSerializer(serializers.Serializer):
    """Create and rename. The name is trimmed: only spaces is an empty name."""

    name = serializers.CharField(max_length=NAME_MAX_LENGTH, error_messages=NAME_MESSAGES)


class DeleteWorkspaceSerializer(serializers.Serializer):
    # Compared as typed: the confirmation is the exact name.
    confirm_name = serializers.CharField(
        trim_whitespace=False,
        error_messages={key: CONFIRM_NAME_REQUIRED for key in ('required', 'null', 'blank', 'invalid')},
    )


class TransferWorkspaceSerializer(serializers.Serializer):
    new_owner_id = serializers.UUIDField(
        error_messages={'required': NEW_OWNER_REQUIRED, 'null': NEW_OWNER_REQUIRED, 'invalid': NEW_OWNER_NOT_ACTIVE},
    )


class UpdateMemberSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=WorkspaceMember.Role.choices,
        error_messages={key: ROLE_INVALID for key in ('required', 'null', 'invalid_choice')},
    )


class WorkspaceAdminSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()


class WorkspaceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    owner = serializers.UUIDField()
    subscription_plan = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    # Role of the user who asks: `owner` or a WorkspaceMember.Role.
    role = serializers.CharField()


class WorkspaceDetailSerializer(WorkspaceSerializer):
    # The owner first, then the ACTIVE admins: who to ask for access.
    admins = WorkspaceAdminSerializer(many=True)


class UserRefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)
    email = serializers.EmailField()


class MemberSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    user = UserRefSerializer()
    email = serializers.EmailField()
    # `owner` for Workspace.owner's row; otherwise WorkspaceMember.role.
    role = serializers.CharField()
    status = serializers.CharField()
    joined_at = serializers.DateTimeField()
    # Nobody is recorded as the inviter yet (WorkspaceMember has no such column).
    invited_by = serializers.JSONField(allow_null=True)


class MembersSerializer(serializers.Serializer):
    members = MemberSerializer(many=True)
    max_members = serializers.IntegerField()
    seats_used = serializers.IntegerField()
