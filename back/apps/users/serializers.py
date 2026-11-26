from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.users.models import NotificationPreference


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
    active_workspace_id = serializers.UUIDField(allow_null=True)
    workspaces = WorkspaceSummarySerializer(many=True)


class UpdateMeSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=150, required=False)
    tour_completed = serializers.BooleanField(required=False)


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
    """The session a password change hands back, like `/api/token/`."""

    access = serializers.CharField()
    refresh = serializers.CharField()


class NotificationPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['project_ready', 'render_finished', 'project_failed', 'post_failed', 'low_balance']
