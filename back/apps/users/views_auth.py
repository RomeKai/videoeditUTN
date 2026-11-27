"""
Account endpoints without a session (A2, A3, A4) and the logout
(front/src/api/contracts/auth.ts).

None of them tells whether an email has an account, except the sign-up,
which the contract asks for (A2: "ya existe una cuenta", with a link to the
login): it is limited per IP. Resend the verification and password reset
answer 204 for any address, and the request does the same work for every
one: one query for the id of the account (None without one) and one task
queued with it (`apps.users.tasks.send_account_email`). The worker decides
whether to send and sends; the slow part never runs in the request. Neither
the answer nor its time depends on the account, and the address never goes
to the queue or to Celery's logs (only the id, or None).

Every endpoint has its attempt limits (apps.users.throttles), which still
count while Redis is down. Nothing here logs an email or a token.
"""
import logging
import smtplib
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from kombu.exceptions import OperationalError as BrokerError
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenBackendError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.email_verification import new_signup_nonce, send_signup_verification, user_for_password_reset_token
from apps.users.models import User, Workspace
from apps.users.serializers import (
    EMAIL_TAKEN,
    EmailRequestSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
    check_new_password,
)
from apps.users.tasks import PASSWORD_RESET, RESEND_VERIFICATION, send_account_email
from apps.users.throttles import AttemptThrottle, EmailAttemptThrottle, SpanishThrottleMixin
from apps.users.tokens import revoke_sessions
from apps.users.views import EMAIL_REFUSED, INVALID_LINK, THROTTLED, UNAUTHORIZED, EmailNotSent

logger = logging.getLogger(__name__)

INVALID_REFRESH = 'La sesión no es válida o ya venció.'
OTHER_SESSION = 'Ese refresh es de otra cuenta: no se cerró nada.'

NO_CONTENT = OpenApiResponse(description='Sin cuerpo.')
EMAIL_NOT_QUEUED = OpenApiResponse(description='No se pudo encolar el email (cola caída). Probá de nuevo en unos minutos.')


class EmailNotQueued(EmailNotSent):
    """The queue did not take the email: the same answer whether the address has an account or not."""

    default_detail = 'No pudimos mandar el email. Probá de nuevo en unos minutos.'
    default_code = 'email_not_queued'


def _queue_account_email(kind, email):
    """The same query and the same task whether the email has an account or not."""
    # Found like the login finds it: `email` is already normalized (EmailRequestSerializer).
    user_id = User.objects.filter(email__iexact=email).values_list('pk', flat=True).first()
    try:
        send_account_email.delay(kind, str(user_id) if user_id else None)
    except (BrokerError, OSError) as exc:
        logger.warning('account_email.not_queued kind=%s error=%s', kind, type(exc).__name__)
        raise EmailNotQueued()


def _workspace_name(first_name):
    """The workspace the sign-up creates carries the person's name (Workspace.name has 100 characters)."""
    return f'Espacio de {first_name}'[:100]


class PublicAccountView(SpanishThrottleMixin, APIView):
    """
    No session: a token in the header (even an expired one) is not read, so
    it cannot answer 401 and the limits always count per IP.

    The limits are checked in order and the first one that refuses ends the
    check. DRF checks them all, so a request its IP limit refused would still
    count in the limit of the email it names: from one IP, someone could keep
    every address waiting its 60 s for ever without sending a single email.
    That is why the IP limit goes first.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def check_throttles(self, request):
        for throttle in self.get_throttles():
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())


class RegisterView(PublicAccountView):
    """
    A2: creates the account, its own workspace (the post_save signal of User,
    with create_owned_workspace like POST /workspaces/: default plan, its
    wallet, the user as active Admin) and sends the verification email. Until the link is opened the account is unverified;
    the link signs the user in (VerifyEmailView). If the email server does not
    take the email nothing is saved, as in the email change of `PATCH /me`.
    """

    throttle_classes = [AttemptThrottle]
    throttle_scope = 'register'

    @extend_schema(
        request=RegisterSerializer,
        responses={
            201: RegisterResponseSerializer,
            400: OpenApiResponse(description=(
                'Validación por campo. `email` ya usado (con cualquier escritura de su dominio) '
                'llega como `[{"message", "code": "unique"}]`; dominio que no se puede codificar, o '
                'dirección que el servidor de email rechaza. `password` no cumple las reglas. '
                '`terms_accepted` falta o no es `true`.'
            )),
            429: THROTTLED,
            502: OpenApiResponse(description='El servidor de email no aceptó el email de verificación; no se guardó nada.'),
        },
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        first_name, _, last_name = data['name'].partition(' ')
        # The link signs in only where this answer arrives (VerifyEmailView): the account keeps the hash.
        nonce, nonce_hash = new_signup_nonce()
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    # Required and unique in AbstractUser; the account signs in with its email.
                    username=uuid.uuid4().hex,
                    email=data['email'],
                    password=data['password'],
                    first_name=first_name,
                    last_name=last_name.strip(),
                    terms_accepted_at=timezone.now(),
                    signup_nonce_hash=nonce_hash,
                )
                # The signal names it after the username; here there is a name.
                Workspace.objects.filter(owner=user).update(name=_workspace_name(first_name))
                # A failed send rolls the account back: no account without its link.
                send_signup_verification(user)
        except IntegrityError:
            # Two sign-ups of the same email at once: the constraint stops the second one.
            raise ValidationError({'email': [{'message': EMAIL_TAKEN, 'code': 'unique'}]})
        except smtplib.SMTPRecipientsRefused:
            raise ValidationError({'email': [EMAIL_REFUSED]})
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning('register.send_failed error=%s', type(exc).__name__)
            raise EmailNotSent()
        # The first email is the one just sent: «Reenviar» waits its 60 s from now (A3).
        EmailAttemptThrottle.record('resend', user.email)
        logger.info('register.created user_id=%s', user.pk)
        body = RegisterResponseSerializer({
            'id': user.pk, 'email': user.email, 'is_email_verified': user.is_email_verified, 'signup_nonce': nonce,
        })
        return Response(body.data, status=201)


class ResendVerificationView(PublicAccountView):
    """
    A3: sends the verification link again to an account that is not verified
    yet. Always 204: with an account or without, verified or not. Once every
    60 s per email (any spelling), and a limit per IP shared with password reset.
    """

    # The IP first: a request it refuses must not count for the email (PublicAccountView).
    throttle_classes = [AttemptThrottle, EmailAttemptThrottle]
    email_throttle_scope = 'resend'
    throttle_scope = 'auth_email'

    @extend_schema(
        request=EmailRequestSerializer,
        responses={
            204: NO_CONTENT,
            400: OpenApiResponse(description='El email falta o no tiene forma de email.'),
            429: THROTTLED,
            502: EMAIL_NOT_QUEUED,
        },
    )
    def post(self, request):
        serializer = EmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _queue_account_email(RESEND_VERIFICATION, serializer.validated_data['email'])
        return Response(status=204)


class PasswordResetView(PublicAccountView):
    """
    A4, step 1: emails the link to choose a new password. Always 204, whether
    the email has an account or not. Once every 60 s per email, and a limit
    per IP shared with resend. An account without a usable password (social
    login) gets it too: that is how it creates one.
    """

    throttle_classes = [AttemptThrottle, EmailAttemptThrottle]
    email_throttle_scope = 'password_reset'
    throttle_scope = 'auth_email'

    @extend_schema(
        request=EmailRequestSerializer,
        responses={
            204: NO_CONTENT,
            400: OpenApiResponse(description='El email falta o no tiene forma de email.'),
            429: THROTTLED,
            502: EMAIL_NOT_QUEUED,
        },
    )
    def post(self, request):
        serializer = EmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _queue_account_email(PASSWORD_RESET, serializer.validated_data['email'])
        return Response(status=204)


class PasswordResetConfirmView(PublicAccountView):
    """
    A4, step 2: the token of the link and the new password. 204 without
    tokens: the web app signs in with the email of the link. Like
    `POST /me/password/`, User.save ends every session of the account (the
    tokens of the old password answer 401). The link reached the account's
    email, so opening it also verifies that email, and what whoever had the
    old password left behind goes: a pending change of email and the
    sign-up's nonce.
    """

    throttle_classes = [AttemptThrottle]
    throttle_scope = 'password_reset_confirm'

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={
            204: NO_CONTENT,
            400: OpenApiResponse(description=(
                '`token`: link inválido, vencido o ya usado (también después de otro cambio de contraseña). '
                '`password`: no cumple las reglas. El token se revisa primero.'
            )),
            429: THROTTLED,
        },
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            # Locked: two requests with the same link cannot both pass the check.
            user = user_for_password_reset_token(data['token'], queryset=User.objects.select_for_update())
            if user is None:
                raise ValidationError({'token': [INVALID_LINK]})
            try:
                check_new_password(data['password'], user)
            except ValidationError as exc:
                raise ValidationError({'password': exc.detail})
            user.set_password(data['password'])
            user.is_email_verified = True
            user.pending_email = None
            user.signup_nonce_hash = None
            # User.save revokes the refresh tokens of the old password; the access ones fail its hash.
            user.save(update_fields=['password', 'is_email_verified', 'pending_email', 'signup_nonce_hash'])
        logger.info('password_reset.done user_id=%s', user.pk)
        return Response(status=204)


class LogoutView(SpanishThrottleMixin, APIView):
    """
    Ends the session of this refresh token (blacklist). With `all_devices`,
    every session of the user, access tokens included (apps.users.tokens).

    The session of the request is the proof: the refresh must be of that same
    account, or nothing is revoked (400). A refresh already revoked (the
    client renewed the pair to send this request, or logs out twice) answers
    204: that session is already closed.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [AttemptThrottle]
    throttle_scope = 'logout'

    @extend_schema(
        request=LogoutSerializer,
        responses={
            204: NO_CONTENT,
            400: OpenApiResponse(description='`refresh` inválido, vencido, o de otra cuenta. No se revoca nada.'),
            401: UNAUTHORIZED,
            429: THROTTLED,
        },
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            # Signature and expiry; the blacklist is not checked here (a revoked one is a 204).
            payload = token_backend.decode(data['refresh'], verify=True)
        except TokenBackendError:
            raise ValidationError({'refresh': [INVALID_REFRESH]})
        if payload.get(api_settings.TOKEN_TYPE_CLAIM) != RefreshToken.token_type or api_settings.JTI_CLAIM not in payload:
            raise ValidationError({'refresh': [INVALID_REFRESH]})
        if str(payload.get(api_settings.USER_ID_CLAIM)) != str(getattr(request.user, api_settings.USER_ID_FIELD)):
            raise ValidationError({'refresh': [OTHER_SESSION]})
        with transaction.atomic():
            RefreshToken(data['refresh'], verify=False).blacklist()
            if data['all_devices']:
                revoke_sessions(request.user)
        logger.info('logout.done user_id=%s all_devices=%s', request.user.pk, data['all_devices'])
        return Response(status=204)
