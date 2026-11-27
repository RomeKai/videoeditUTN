"""
Bodies of `/api/v1/workspaces/` (views_workspaces), with the field names of
front/src/api/contracts/workspaces.ts. The messages go to the screen as they are.
"""
from rest_framework import serializers

from apps.users.emails import InvalidEmailDomain, normalize_email
from apps.users.models import Workspace, WorkspaceMember
from apps.users.serializers import NAME_REQUIRED, PASSWORD_REQUIRED, TERMS_REQUIRED, TokenPairSerializer

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


class InviterSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)


class MemberSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    # Null in a pending invitation, whether its email has an account or not:
    # inviting an address never says whose account it is.
    user = UserRefSerializer(allow_null=True)
    email = serializers.EmailField()
    # `owner` for Workspace.owner's row; otherwise WorkspaceMember.role.
    role = serializers.CharField()
    status = serializers.CharField()
    joined_at = serializers.DateTimeField()
    # Who sent the invitation (WorkspaceMember.invited_by); null for the rows nobody invited.
    invited_by = InviterSerializer(allow_null=True)


class SeatLimitSerializer(serializers.Serializer):
    """Why `max_members` is below the current plan's (seats.SeatLimit.limited_by)."""

    # pending_plan: a scheduled downgrade; pending_payment: a plan payment waiting for its checkout.
    reason = serializers.ChoiceField(choices=['pending_plan', 'pending_payment'])
    # The plan whose seats count.
    plan_name = serializers.CharField()
    # When the downgrade applies; null for a payment.
    effective_at = serializers.DateTimeField(allow_null=True)


class MembersSerializer(serializers.Serializer):
    members = MemberSerializer(many=True)
    # The limit that inviting checks (seats.seat_limit): the plan's, or a smaller one while a change waits.
    max_members = serializers.IntegerField()
    seats_used = serializers.IntegerField()
    # Null when max_members is the current plan's.
    limited_by = SeatLimitSerializer(allow_null=True)


# --- Invitations (apps.users.views_invitations) ---

MAX_INVITED_EMAILS = 10
EMAILS_REQUIRED = 'Escribí al menos un email.'
EMAILS_TOO_MANY = f'Podés invitar hasta {MAX_INVITED_EMAILS} emails por vez.'
EMAIL_NOT_VALID = '«{email}» no es un email válido.'
ALREADY_MEMBER = '{email} ya es miembro o tiene una invitación pendiente.'


class InviteSerializer(serializers.Serializer):
    """
    D7. Every error of `emails` is one sentence in a flat list (the dialog
    shows the first). The emails come back normalized (apps.users.emails) and
    without repeats, whatever their case.
    """

    emails = serializers.ListField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=True),
        allow_empty=False,
        error_messages={key: EMAILS_REQUIRED for key in ('required', 'null', 'not_a_list', 'empty')},
    )
    # `owner` is not a choice: nobody invites an owner (the workspace is transferred).
    role = serializers.ChoiceField(
        choices=WorkspaceMember.Role.choices,
        error_messages={key: ROLE_INVALID for key in ('required', 'null', 'invalid_choice')},
    )

    def to_internal_value(self, data):
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as exc:
            # A child error of the list comes as {index: [...]}: one flat sentence instead.
            detail = exc.detail
            if isinstance(detail, dict) and isinstance(detail.get('emails'), dict):
                detail = {**detail, 'emails': [EMAILS_REQUIRED]}
                raise serializers.ValidationError(detail)
            raise

    def validate_emails(self, values):
        # Before any work per email: a long list is refused as it comes.
        if len(values) > MAX_INVITED_EMAILS:
            raise serializers.ValidationError(EMAILS_TOO_MANY)
        field = serializers.EmailField(max_length=254)
        found, seen = [], set()
        for value in values:
            try:
                email = normalize_email(field.run_validation(value))
            except (serializers.ValidationError, InvalidEmailDomain):
                raise serializers.ValidationError(EMAIL_NOT_VALID.format(email=str(value)[:254]))
            if email.upper() in seen:
                continue
            seen.add(email.upper())
            found.append(email)
        return found


class InvitedSerializer(serializers.Serializer):
    invited = MemberSerializer(many=True)


class InvitationPreviewSerializer(serializers.Serializer):
    """A5: what the link shows before accepting. The inviter's name, never their email."""

    workspace_id = serializers.UUIDField()
    workspace_name = serializers.CharField()
    inviter_name = serializers.CharField()
    role = serializers.CharField()
    email = serializers.EmailField()
    # Always `pending`: a link that expired, was used or was cancelled is a 404.
    status = serializers.CharField()
    has_account = serializers.BooleanField()


TOKEN_REQUIRED = 'Falta el token del link.'


class InvitationTokenSerializer(serializers.Serializer):
    """The token of the link, in the body: never in the path, which ends up in logs."""

    token = serializers.CharField(
        max_length=200, trim_whitespace=False,
        error_messages={key: TOKEN_REQUIRED for key in ('required', 'null', 'blank', 'invalid', 'max_length')},
    )


class AcceptInvitationSerializer(serializers.Serializer):
    """Only read when the link creates the account; with a session only `token` is read."""

    # Read by the view before anything else; a missing one is the 410 of any dead link.
    token = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    name = serializers.CharField(
        max_length=150,
        error_messages={
            'required': NAME_REQUIRED, 'blank': NAME_REQUIRED, 'null': NAME_REQUIRED,
            'max_length': 'El nombre puede tener hasta {max_length} caracteres.',
        },
    )
    # Checked by the view against the invited email (the similarity rule needs it).
    password = serializers.CharField(
        write_only=True,
        error_messages={'required': PASSWORD_REQUIRED, 'blank': PASSWORD_REQUIRED, 'null': PASSWORD_REQUIRED},
    )
    terms_accepted = serializers.BooleanField(
        error_messages={'required': TERMS_REQUIRED, 'invalid': TERMS_REQUIRED, 'null': TERMS_REQUIRED},
    )

    def validate_terms_accepted(self, value):
        if value is not True:
            raise serializers.ValidationError(TERMS_REQUIRED, code='required')
        return value


class AcceptedInvitationSerializer(serializers.Serializer):
    workspace_id = serializers.UUIDField()
    # A new session when the account was created here; null with a session.
    tokens = TokenPairSerializer(allow_null=True)
