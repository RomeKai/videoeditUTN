from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from apps.core.workspace import role_in
from apps.users.emails import EMAIL_TOO_LONG, InvalidEmailDomain
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


def email_taken(email, exclude_pk=None):
    """
    Another account already uses the email, ignoring case (User.Meta's constraint)
    and the spelling of its domain. A domain that cannot be encoded is nobody's.
    """
    try:
        email = User.objects.normalize_email(email)
    except InvalidEmailDomain:
        return False
    return User.objects.filter(email__iexact=email).exclude(pk=exclude_pk).exists()


def _password_rule_message(error):
    template = PASSWORD_RULE_MESSAGES.get(error.code)
    if template is None:
        # A validator added later: its own message (write it in Spanish).
        return error.messages[0]
    return template.format(**(error.params or {}))


def check_new_password(value, user):
    """
    AUTH_PASSWORD_VALIDATORS on a new password, with their sentences in
    Spanish. `user` can be unsaved (sign-up): the similarity rule compares
    with its name and email.
    """
    try:
        validate_password(value, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError([_password_rule_message(error) for error in exc.error_list])
    return value


class PlanSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class WorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()
    plan = PlanSummarySerializer(allow_null=True)
    available_balance = serializers.DecimalField(max_digits=20, decimal_places=10)


class OnboardingSerializer(serializers.Serializer):
    has_project = serializers.BooleanField()
    has_brand_kit = serializers.BooleanField()
    has_social_account = serializers.BooleanField()
    has_invited_member = serializers.BooleanField()


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField()
    is_email_verified = serializers.BooleanField()
    tour_completed = serializers.BooleanField()
    terms_accepted_at = serializers.DateTimeField(allow_null=True)
    pending_email = serializers.EmailField(allow_null=True)
    onboarding_use_case = serializers.ChoiceField(choices=User.OnboardingUseCase.choices, allow_null=True)
    # First-steps checklist of B2, in the active workspace.
    onboarding = OnboardingSerializer()
    active_workspace_id = serializers.UUIDField(allow_null=True)
    workspaces = WorkspaceSummarySerializer(many=True)


NOT_A_MEMBER = 'No sos miembro de ese workspace.'
EMAIL_TAKEN = 'Ya existe una cuenta con este email.'
PASSWORD_REQUIRED_FOR_EMAIL = 'Escribí tu contraseña para cambiar el email.'


class UpdateMeSerializer(serializers.Serializer):
    """
    Every field is optional. A new `email` needs `current_password` and is kept
    in `pending_email` until its link is opened; the current email (or null)
    cancels a pending change. `active_workspace_id` must be a workspace the
    user owns or is an ACTIVE member of.
    """

    name = serializers.CharField(min_length=1, max_length=150, required=False)
    email = serializers.EmailField(max_length=254, required=False, allow_null=True)
    current_password = serializers.CharField(required=False, write_only=True)
    tour_completed = serializers.BooleanField(required=False)
    onboarding_use_case = serializers.ChoiceField(choices=User.OnboardingUseCase.choices, required=False)
    active_workspace_id = serializers.UUIDField(required=False)

    def validate_active_workspace_id(self, value):
        workspace = Workspace.objects.filter(pk=value).first()
        if workspace is None or role_in(workspace, self.context['user']) is None:
            raise serializers.ValidationError(NOT_A_MEMBER)
        return value

    def validate_email(self, value):
        if value is None:
            return value
        try:
            return User.objects.normalize_email(value)
        except InvalidEmailDomain as error:
            # Also an email that passes max_length as typed and not once its domain is encoded.
            raise serializers.ValidationError(error.message, code='invalid_domain')

    def validate(self, attrs):
        if 'email' not in attrs:
            return attrs
        email = attrs['email']
        user = self.context['user']
        # validate_email already normalized the domain.
        if email is None or email.lower() == user.email.lower():
            # Back to the account's email: the pending change (if any) is cancelled.
            attrs['email'] = None
            return attrs
        # The password goes first: without it nobody learns which emails have an account.
        password = attrs.get('current_password')
        if not password and user.has_usable_password():
            raise serializers.ValidationError({'current_password': [ErrorDetail(PASSWORD_REQUIRED_FOR_EMAIL, code='required')]})
        try:
            check_current_password(user, password or '')
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'current_password': exc.detail})
        if email_taken(email, exclude_pk=user.pk):
            raise serializers.ValidationError({'email': [ErrorDetail(EMAIL_TAKEN, code='unique')]})
        attrs['email'] = email
        return attrs


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()
    # The proof of the browser that signed up (RegisterView). Without it (and without a
    # session of the account) the link of a new account changes nothing.
    signup_nonce = serializers.CharField(required=False, allow_blank=True, max_length=200)


class SignedOutSerializer(serializers.Serializer):
    """A signup link opened outside the browser that signed up: nothing changed; sign in and open it again."""

    signed_in = serializers.BooleanField()


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(
        min_length=8,
        error_messages={'min_length': PASSWORD_RULE_MESSAGES['password_too_short']},
    )

    def validate_new_password(self, value):
        return check_new_password(value, self.context['user'])

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


# --- Account endpoints without a session (apps.users.views_auth) ---

NAME_REQUIRED = 'Escribí tu nombre.'
EMAIL_REQUIRED = 'Escribí tu email.'
EMAIL_INVALID = 'Revisá el email: no parece completo.'
PASSWORD_REQUIRED = 'Elegí una contraseña.'
TERMS_REQUIRED = 'Para crear la cuenta tenés que aceptar los términos.'

_EMAIL_ERRORS = {
    'required': EMAIL_REQUIRED,
    'blank': EMAIL_REQUIRED,
    'null': EMAIL_REQUIRED,
    'invalid': EMAIL_INVALID,
    'max_length': EMAIL_TOO_LONG,
}


def _normalized_email(value):
    try:
        return User.objects.normalize_email(value)
    except InvalidEmailDomain as error:
        raise serializers.ValidationError(error.message, code='invalid_domain')


class RegisterSerializer(serializers.Serializer):
    """
    A2. The email is normalized (apps.users.emails) and must not belong to
    another account under any spelling (User.Meta's constraint): that error
    carries `code: "unique"`, the one the web app links to the login. It is
    checked last, once every other field is valid: a sign-up that would not
    create anything never says whether an email has an account.
    """

    name = serializers.CharField(
        max_length=150,
        error_messages={
            'required': NAME_REQUIRED, 'blank': NAME_REQUIRED, 'null': NAME_REQUIRED,
            'max_length': 'El nombre puede tener hasta {max_length} caracteres.',
        },
    )
    email = serializers.EmailField(max_length=254, error_messages=_EMAIL_ERRORS)
    # Trimmed like the login's (SimpleJWT's PasswordField): what signs up is what signs in.
    password = serializers.CharField(
        write_only=True,
        error_messages={'required': PASSWORD_REQUIRED, 'blank': PASSWORD_REQUIRED, 'null': PASSWORD_REQUIRED},
    )
    terms_accepted = serializers.BooleanField(
        error_messages={'required': TERMS_REQUIRED, 'invalid': TERMS_REQUIRED, 'null': TERMS_REQUIRED},
    )

    def validate_email(self, value):
        return _normalized_email(value)

    def validate_password(self, value):
        # The account does not exist yet: the similarity rule compares with what was typed.
        data = self.initial_data if hasattr(self.initial_data, 'get') else {}
        name = data.get('name') if isinstance(data.get('name'), str) else ''
        first_name, _, last_name = name.strip().partition(' ')
        email = data.get('email') if isinstance(data.get('email'), str) else ''
        return check_new_password(value, User(email=email, first_name=first_name, last_name=last_name))

    def validate_terms_accepted(self, value):
        if value is not True:
            raise serializers.ValidationError(TERMS_REQUIRED, code='required')
        return value

    def validate(self, attrs):
        # Only reached when every field passed (DRF stops at field errors).
        if email_taken(attrs['email']):
            # {message, code}: the web app tells "already taken" apart without reading the text.
            raise serializers.ValidationError({'email': [{'message': EMAIL_TAKEN, 'code': 'unique'}]})
        return attrs


class RegisterResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    is_email_verified = serializers.BooleanField()
    # Kept by the browser that signed up and sent with the link (VerifyEmailSerializer).
    signup_nonce = serializers.CharField()


class EmailRequestSerializer(serializers.Serializer):
    """Resend the verification, password reset: only the email (normalized when its domain can be)."""

    email = serializers.EmailField(max_length=254, error_messages=_EMAIL_ERRORS)

    def validate_email(self, value):
        try:
            return User.objects.normalize_email(value)
        except InvalidEmailDomain:
            # Nobody's email (or an account of before the normalization, found as typed):
            # the answer is the same as for any other address.
            return value.strip()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(error_messages={'required': 'Falta el token del link.', 'blank': 'Falta el token del link.'})
    # Checked by the view once the token says whose account it is (the similarity rule needs it).
    password = serializers.CharField(
        write_only=True,
        error_messages={'required': PASSWORD_REQUIRED, 'blank': PASSWORD_REQUIRED, 'null': PASSWORD_REQUIRED},
    )


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(error_messages={'required': 'Falta el refresh de la sesión.', 'blank': 'Falta el refresh de la sesión.'})
    all_devices = serializers.BooleanField(required=False, default=False)
