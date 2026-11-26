import logging
import smtplib
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from rest_framework.exceptions import APIException, AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.workspace import OWNER, ActiveWorkspace
from apps.payments.models import Wallet
from apps.users.models import NotificationPreference, User, Workspace, WorkspaceMember
from apps.videos.models import BrandKit, VideoProject
from apps.users.serializers import (
    EMAIL_TAKEN,
    MeSerializer,
    NotificationPreferencesSerializer,
    PasswordChangeSerializer,
    UpdateMeSerializer,
    TokenPairSerializer,
    VerifyEmailSerializer,
    email_taken,
)
from apps.users.email_verification import EMAIL_CHANGE, send_email_change_verification, user_for_token
from apps.users.throttles import EmailChangeThrottle, LoginFailureGuard, SpanishThrottleMixin, TooManyAttempts
from apps.users.tokens import RevocableTokenRefreshSerializer, issue_token_pair


logger = logging.getLogger(__name__)


class EmailNotSent(APIException):
    """
    The email server did not take the verification email: nothing was saved.
    502, not 503: the web app reads any 503 as maintenance (page K5).
    """

    status_code = 502
    default_detail = 'No pudimos mandar el email de verificación. Probá de nuevo en unos minutos.'
    default_code = 'email_not_sent'


EMAIL_REFUSED = 'El servidor de email rechazó esa dirección. Revisá que esté bien escrita.'

UNAUTHORIZED = OpenApiResponse(description='Sin sesión, o con un token vencido o revocado.')
THROTTLED = OpenApiResponse(description='Demasiados intentos: `detail` dice en castellano cuánto esperar, y va Retry-After.')


def _user_workspaces(user):
    """
    Every workspace the user owns or is an ACTIVE member of, oldest first
    (created_at, pk), and the default one. Owning a workspace wins over a
    membership row of it. Plans and wallets come in the same two queries.

    The default follows `resolve_active_workspace` without the header: the
    oldest owned workspace, else the oldest ACTIVE membership.
    """
    owned = list(
        Workspace.objects.filter(owner=user)
        .select_related('subscription_plan', 'wallet')
        .order_by('created_at', 'pk')
    )
    memberships = list(
        WorkspaceMember.objects.filter(user=user, status=WorkspaceMember.Status.ACTIVE)
        .select_related('workspace__subscription_plan', 'workspace__wallet')
        .order_by('joined_at', 'pk')
    )
    found = {workspace.pk: ActiveWorkspace(workspace, OWNER) for workspace in owned}
    for membership in memberships:
        found.setdefault(membership.workspace_id, ActiveWorkspace(membership.workspace, membership.role))
    default = owned[0] if owned else (memberships[0].workspace if memberships else None)
    ordered = sorted(found.values(), key=lambda item: (item.workspace.created_at, item.workspace.pk))
    return ordered, default


def _workspace_summary(active):
    workspace = active.workspace
    plan = workspace.subscription_plan
    try:
        balance = workspace.wallet.available_balance
    except Wallet.DoesNotExist:
        balance = Decimal('0')
    return {
        'id': workspace.pk,
        'name': workspace.name,
        'role': active.role,
        'plan': {'id': plan.pk, 'name': plan.name} if plan else None,
        'available_balance': balance,
    }


def _onboarding(workspace):
    """
    First steps of B2 in the active workspace. A network counts as connected
    when the workspace has its Ayrshare profile (`ayrshare_profile_key`, the
    only link to the networks today); an invitation counts while pending.
    """
    if workspace is None:
        return {'has_project': False, 'has_brand_kit': False, 'has_social_account': False, 'has_invited_member': False}
    flags = (
        Workspace.objects.filter(pk=workspace.pk)
        .annotate(
            has_project=Exists(VideoProject.objects.filter(workspace=OuterRef('pk'))),
            has_brand_kit=Exists(BrandKit.objects.filter(workspace=OuterRef('pk'))),
            has_invited_member=Exists(
                WorkspaceMember.objects.filter(workspace=OuterRef('pk')).exclude(user=OuterRef('owner'))
            ),
        )
        .values('has_project', 'has_brand_kit', 'has_invited_member')
        .get()
    )
    return {**flags, 'has_social_account': bool(workspace.ayrshare_profile_key)}


def _me_payload(user):
    """
    `active_workspace_id` is the workspace the user last chose (PATCH /me,
    `User.last_workspace`) while they still own it or are an ACTIVE member;
    else the default. It never depends on X-Workspace-Id: a stale header
    cannot break the session.
    """
    workspaces, default = _user_workspaces(user)
    by_id = {item.workspace.pk: item.workspace for item in workspaces}
    active = by_id.get(user.last_workspace_id, default)
    return MeSerializer({
        'id': user.pk,
        'email': user.email,
        'name': f'{user.first_name} {user.last_name}'.strip(),
        'is_email_verified': user.is_email_verified,
        'tour_completed': user.tour_completed,
        'terms_accepted_at': user.terms_accepted_at,
        'pending_email': user.pending_email,
        'onboarding_use_case': user.onboarding_use_case,
        'onboarding': _onboarding(active),
        'active_workspace_id': active.pk if active else None,
        'workspaces': [_workspace_summary(item) for item in workspaces],
    }).data


class MeView(SpanishThrottleMixin, APIView):
    """The signed-in user, their workspaces with their role in each, the active one and the first steps."""

    permission_classes = [IsAuthenticated]
    # Only the PATCHes that change the email count (EmailChangeThrottle).
    throttle_classes = [EmailChangeThrottle]
    throttle_scope = 'email_change'

    @extend_schema(responses={200: MeSerializer, 401: UNAUTHORIZED})
    def get(self, request):
        return Response(_me_payload(request.user))

    @extend_schema(
        request=UpdateMeSerializer,
        responses={
            200: MeSerializer,
            400: OpenApiResponse(description=(
                'Validación por campo: `current_password` falta, no coincide o la cuenta no tiene '
                'contraseña; `email` lo usa otra cuenta o el servidor de email rechaza esa dirección; `active_workspace_id` no es '
                'un workspace del usuario.'
            )),
            401: UNAUTHORIZED,
            429: THROTTLED,
            502: OpenApiResponse(description='El servidor de email no aceptó el email de verificación; no se guardó nada.'),
        },
    )
    def patch(self, request):
        user = request.user
        serializer = UpdateMeSerializer(data=request.data, context={'user': user})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        fields = []
        if 'name' in data:
            # Maps to first_name and last_name: the first word is the first name, the rest the last one.
            user.first_name, _, user.last_name = data['name'].partition(' ')
            fields += ['first_name', 'last_name']
        if 'tour_completed' in data:
            user.tour_completed = data['tour_completed']
            fields.append('tour_completed')
        if 'onboarding_use_case' in data:
            user.onboarding_use_case = data['onboarding_use_case']
            fields.append('onboarding_use_case')
        if 'active_workspace_id' in data:
            user.last_workspace_id = data['active_workspace_id']
            fields.append('last_workspace')
        if 'email' in data:
            # A new email waits for its link; None (the current email, or null)
            # cancels a pending change, and with it its link.
            user.pending_email = data['email']
            fields.append('pending_email')
        try:
            with transaction.atomic():
                if fields:
                    user.save(update_fields=fields)
                if data.get('email'):
                    # A failed send rolls the change back: no pending email without its link.
                    send_email_change_verification(user)
        except smtplib.SMTPRecipientsRefused:
            # The server refuses that address (it does not exist, or it takes no mail): the user can fix it.
            user.refresh_from_db()
            raise ValidationError({'email': [EMAIL_REFUSED]})
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning('email_change.send_failed user_id=%s error=%s', user.pk, type(exc).__name__)
            user.refresh_from_db()
            raise EmailNotSent()
        return Response(_me_payload(request.user))


@extend_schema(responses={
    200: TokenPairSerializer,
    401: OpenApiResponse(description='Email o contraseña incorrectos.'),
    429: THROTTLED,
})
class LoginView(SpanishThrottleMixin, TokenObtainPairView):
    """
    POST /api/token/ (SimpleJWT) with attempt limits: every request per IP
    (ScopedRateThrottle), and the failed ones per account (LoginFailureGuard).
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        guard = LoginFailureGuard.for_request(request)
        if guard is None:
            return super().post(request, *args, **kwargs)
        wait = guard.reserve()
        if wait:
            raise TooManyAttempts(wait)
        try:
            response = super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            # A failure: the attempt stays counted.
            raise
        except Exception:
            guard.release()
            raise
        guard.succeeded()
        return response


@extend_schema(responses={
    200: RevocableTokenRefreshSerializer,
    401: OpenApiResponse(description='Refresh vencido, inválido o revocado (por ejemplo, después de cambiar la contraseña).'),
    429: THROTTLED,
})
class TokenRefreshThrottledView(SpanishThrottleMixin, TokenRefreshView):
    """POST /api/token/refresh/ (SimpleJWT) with a limit per IP; a token of an old password answers 401."""

    serializer_class = RevocableTokenRefreshSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'token_refresh'


class PasswordChangeView(SpanishThrottleMixin, APIView):
    """
    Changes the password and ends every other session: the tokens issued before
    stop working (apps.users.tokens). This client stays signed in with the new
    pair of the answer.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_change'

    @extend_schema(
        request=PasswordChangeSerializer,
        responses={
            200: TokenPairSerializer,
            400: OpenApiResponse(description=(
                '`current_password` incorrecta (o la cuenta no tiene contraseña), o `new_password` '
                'no cumple las reglas de contraseña.'
            )),
            401: UNAUTHORIZED,
            429: THROTTLED,
        },
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'user': request.user})
        serializer.is_valid(raise_exception=True)
        user = request.user
        with transaction.atomic():
            user.set_password(serializer.validated_data['new_password'])
            # User.save revokes the refresh tokens of the old password.
            user.save(update_fields=['password'])
            tokens = issue_token_pair(user)
        return Response(tokens)


class NotificationPreferencesView(APIView):
    """
    One email switch per notification type. The row is created by the first
    change, never by a read: a GET answers the defaults (all on) without
    writing, so two first reads at once cannot collide. Two first changes at
    once are handled by get_or_create, which reads the row again when its
    insert hits the other one.
    """

    permission_classes = [IsAuthenticated]

    def _preferences(self, user):
        preferences, _ = NotificationPreference.objects.get_or_create(user=user)
        return preferences

    @extend_schema(responses={200: NotificationPreferencesSerializer, 401: UNAUTHORIZED})
    def get(self, request):
        stored = NotificationPreference.objects.filter(user=request.user).first()
        serializer = NotificationPreferencesSerializer(stored or NotificationPreference(user=request.user))
        return Response(serializer.data)

    @extend_schema(
        request=NotificationPreferencesSerializer,
        responses={200: NotificationPreferencesSerializer, 400: OpenApiResponse(description='Un valor que no es booleano.'), 401: UNAUTHORIZED},
    )
    def patch(self, request):
        preferences = self._preferences(request.user)
        serializer = NotificationPreferencesSerializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


INVALID_LINK = 'El link no es válido o ya venció.'


OTHER_ACCOUNT = 'Este link es de otra cuenta. Cerrá la sesión y volvé a abrirlo.'
LOGIN_REQUIRED = 'Para confirmar el email nuevo, iniciá sesión con tu cuenta y volvé a abrir el link.'


class VerifyEmailView(APIView):
    """
    A3: the token of a verification link (apps.users.email_verification).

    The session is optional (the web app sends it when it has one) and never
    changes hands: a link of another account than the signed-in one answers
    403 and applies nothing, so a link cannot switch a browser into someone
    else's account. An email-change link needs the session of its account
    (403 without one): it moves `pending_email` to `email`. A signup link
    verifies the account's email and signs the user in. The answer is a pair
    of tokens of that same account.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=VerifyEmailSerializer,
        responses={
            200: TokenPairSerializer,
            400: OpenApiResponse(description='Link inválido, vencido, ya usado o cancelado, o el email lo tomó otra cuenta (error en `token`).'),
            401: UNAUTHORIZED,
            403: OpenApiResponse(description=(
                '`code: "other_account"`: hay una sesión de otra cuenta. `code: "login_required"`: '
                'el link de un cambio de email necesita la sesión de su cuenta. No se aplica nada.'
            )),
        },
    )
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        signed_in = request.user if request.user.is_authenticated else None
        try:
            with transaction.atomic():
                found = user_for_token(token, queryset=User.objects.select_for_update())
                if found is None:
                    raise ValidationError({'token': [INVALID_LINK]})
                user, purpose = found
                if signed_in is not None and signed_in.pk != user.pk:
                    return Response({'detail': OTHER_ACCOUNT, 'code': 'other_account'}, status=403)
                if purpose == EMAIL_CHANGE and signed_in is None:
                    return Response({'detail': LOGIN_REQUIRED, 'code': 'login_required'}, status=403)
                fields = ['is_email_verified']
                if purpose == EMAIL_CHANGE:
                    # Someone may have taken it since the link was sent. Two links
                    # confirmed at the same time end in the unique constraint instead.
                    if email_taken(user.pending_email, exclude_pk=user.pk):
                        raise ValidationError({'token': [EMAIL_TAKEN]})
                    user.email, user.pending_email = user.pending_email, None
                    fields += ['email', 'pending_email']
                user.is_email_verified = True
                user.save(update_fields=fields)
        except IntegrityError:
            raise ValidationError({'token': [EMAIL_TAKEN]})
        return Response(issue_token_pair(user))
