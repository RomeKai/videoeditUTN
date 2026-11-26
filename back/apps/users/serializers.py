from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from apps.core.workspace import role_in
from apps.users.models import NotificationPreference, User, Workspace


# A user created by social login has no usable password: nothing to confirm with.
NO_PASSWORD = (
    'Tu cuenta no tiene contraseña porque entrás con una red social. '
    'Para crear una, usá «¿Olvidaste tu contraseña?» al iniciar sesión.'
)
WRONG_PASSWORD = 'La contraseña actual no es correcta.'

# Django's validators answer in English (LANGUAGE_CODE = 'en-us') and the
# profile screen shows the message under the field as it comes: each rule of
# AUTH_PASSWORD_VALIDATORS gets its sentence here, by the error's code.
PASSWORD_RULE_MESSAGES = {
    'password_too_short': 'La contraseña tiene que tener {min_length} caracteres como mínimo.',
    'password_too_similar': 'La contraseña se parece demasiado a tu nombre o a tu email.',
    'password_too_common': 'Esa contraseña es muy común. Elegí otra.',
    'password_entirely_numeric': 'La contraseña no puede ser solo números.',
}


def check_current_password(user, value):
    """Raises the field error of `current_password` unless it is the user's password."""
    if not user.has_usable_password():
        raise serializers.ValidationError(NO_PASSWORD, code='no_password')
    if not user.check_password(value):
        raise serializers.ValidationError(WRONG_PASSWORD, code='wrong_password')


def _password_rule_message(error):
    template = PASSWORD_RULE_MESSAGES.get(error.code)
    if template is None:
        # A validator added later: its own message (write it in Spanish).
        return error.messages[0]
    return template.format(**(error.params or {}))


class PlanSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class WorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()
    plan = PlanSummarySerializer(allow_null=True)
    available_balance = serializers.DecimalField(max_digits=20, decimal_places=10)


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField()
    is_email_verified = serializers.BooleanField()
    tour_completed = serializers.BooleanField()
    terms_accepted_at = serializers.DateTimeField(allow_null=True)
    pending_email = serializers.EmailField(allow_null=True)
    onboarding_use_case = serializers.ChoiceField(choices=User.OnboardingUseCase.choices, allow_null=True)
    active_workspace_id = serializers.UUIDField(allow_null=True)
    workspaces = WorkspaceSummarySerializer(many=True)


NOT_A_MEMBER = 'No sos miembro de ese workspace.'
EMAIL_TAKEN = 'Ya existe una cuenta con este email.'
PASSWORD_REQUIRED_FOR_EMAIL = 'Escribí tu contraseña para cambiar el email.'


class UpdateMeSerializer(serializers.Serializer):
    """
    Every field is optional. A new `email` needs `current_password` and is kept
    in `pending_email` until its link is opened; `active_workspace_id` must be a
    workspace the user owns or is an ACTIVE member of.
    """

    name = serializers.CharField(min_length=1, max_length=150, required=False)
    email = serializers.EmailField(max_length=254, required=False)
    current_password = serializers.CharField(required=False, write_only=True)
    tour_completed = serializers.BooleanField(required=False)
    onboarding_use_case = serializers.ChoiceField(choices=User.OnboardingUseCase.choices, required=False)
    active_workspace_id = serializers.UUIDField(required=False)

    def validate_active_workspace_id(self, value):
        workspace = Workspace.objects.filter(pk=value).first()
        if workspace is None or role_in(workspace, self.context['user']) is None:
            raise serializers.ValidationError(NOT_A_MEMBER)
        return value

    def validate(self, attrs):
        email = attrs.get('email')
        if email is None:
            return attrs
        user = self.context['user']
        email = User.objects.normalize_email(email)
        if email.lower() == user.email.lower():
            # Already the account's email: nothing to change or verify.
            attrs.pop('email')
            return attrs
        # The password goes first: without it nobody learns which emails have an account.
        password = attrs.get('current_password')
        if not password and user.has_usable_password():
            raise serializers.ValidationError({'current_password': [ErrorDetail(PASSWORD_REQUIRED_FOR_EMAIL, code='required')]})
        try:
            check_current_password(user, password or '')
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'current_password': exc.detail})
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise serializers.ValidationError({'email': [ErrorDetail(EMAIL_TAKEN, code='unique')]})
        attrs['email'] = email
        return attrs


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(
        min_length=8,
        error_messages={'min_length': PASSWORD_RULE_MESSAGES['password_too_short']},
    )

    def validate_new_password(self, value):
        try:
            validate_password(value, user=self.context['user'])
        except DjangoValidationError as exc:
            raise serializers.ValidationError([_password_rule_message(error) for error in exc.error_list])
        return value

    def validate_current_password(self, value):
        check_current_password(self.context['user'], value)
        return value


class TokenPairSerializer(serializers.Serializer):
    """A new session, like the answer of `/api/token/`."""

    access = serializers.CharField()
    refresh = serializers.CharField()


class NotificationPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['project_ready', 'render_finished', 'project_failed', 'post_failed', 'low_balance']
