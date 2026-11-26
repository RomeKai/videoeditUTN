from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.workspace import OWNER, ActiveWorkspace, resolve_active_workspace
from apps.payments.models import Wallet
from apps.users.models import NotificationPreference, User, Workspace, WorkspaceMember
from apps.users.serializers import (
    EMAIL_TAKEN,
    MeSerializer,
    NotificationPreferencesSerializer,
    PasswordChangeSerializer,
    UpdateMeSerializer,
    VerifyEmailSerializer,
)
from apps.users.email_verification import send_email_change_verification, user_for_token
from apps.users.throttles import EmailChangeThrottle, SpanishThrottleMixin
from apps.users.tokens import issue_token_pair, revoke_refresh_tokens


def _user_workspaces(user):
    """Every workspace the user owns or is an ACTIVE member of. Owning one wins over a membership row."""
    found = {}
    for workspace in Workspace.objects.filter(owner=user).select_related('subscription_plan'):
        found[workspace.pk] = ActiveWorkspace(workspace, OWNER)
    memberships = (
        WorkspaceMember.objects.filter(user=user, status=WorkspaceMember.Status.ACTIVE)
        .select_related('workspace__subscription_plan')
    )
    for membership in memberships:
        found.setdefault(membership.workspace_id, ActiveWorkspace(membership.workspace, membership.role))
    return list(found.values())


def _workspace_summary(active):
    workspace = active.workspace
    plan = workspace.subscription_plan
    wallet = Wallet.objects.filter(workspace=workspace).first()
    return {
        'id': workspace.pk,
        'name': workspace.name,
        'role': active.role,
        'plan': {'id': plan.pk, 'name': plan.name} if plan else None,
        'available_balance': wallet.available_balance if wallet else Decimal('0'),
    }


def _me_payload(request):
    user = request.user
    active = resolve_active_workspace(request)
    return MeSerializer({
        'id': user.pk,
        'email': user.email,
        'name': f'{user.first_name} {user.last_name}'.strip(),
        'is_email_verified': user.is_email_verified,
        'tour_completed': user.tour_completed,
        'terms_accepted_at': user.terms_accepted_at,
        'pending_email': user.pending_email,
        'onboarding_use_case': user.onboarding_use_case,
        'active_workspace_id': active.workspace.pk if active else None,
        'workspaces': [_workspace_summary(item) for item in _user_workspaces(user)],
    }).data


class MeView(SpanishThrottleMixin, APIView):
    """The signed-in user, their workspaces with their role in each, and the active one."""

    permission_classes = [IsAuthenticated]
    # Only the PATCHes that change the email count (EmailChangeThrottle).
    throttle_classes = [EmailChangeThrottle]
    throttle_scope = 'email_change'

    def get(self, request):
        return Response(_me_payload(request))

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
            # The current email keeps working until the new one is verified.
            user.pending_email = data['email']
            fields.append('pending_email')
        with transaction.atomic():
            if fields:
                user.save(update_fields=fields)
            if 'email' in data:
                # A failed send rolls the change back: no pending email without its link.
                send_email_change_verification(user)
        return Response(_me_payload(request))


class PasswordChangeView(SpanishThrottleMixin, APIView):
    """
    Changes the password and ends every other session: the tokens issued before
    stop working (apps.users.tokens). This client stays signed in with the new
    pair of the answer.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_change'

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'user': request.user})
        serializer.is_valid(raise_exception=True)
        user = request.user
        with transaction.atomic():
            user.set_password(serializer.validated_data['new_password'])
            user.save(update_fields=['password'])
            revoke_refresh_tokens(user)
            tokens = issue_token_pair(user)
        return Response(tokens)


class NotificationPreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    def _preferences(self, user):
        preferences, _ = NotificationPreference.objects.get_or_create(user=user)
        return preferences

    def get(self, request):
        serializer = NotificationPreferencesSerializer(self._preferences(request.user))
        return Response(serializer.data)

    def patch(self, request):
        preferences = self._preferences(request.user)
        serializer = NotificationPreferencesSerializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


INVALID_LINK = 'El link no es válido o ya venció.'


class VerifyEmailView(APIView):
    """
    A3: the token of the verification link (apps.users.email_verification).
    With a pending email, it becomes the account's email; without one, the
    current email is marked as verified. Either way the answer is a new
    session, so the link signs the user in.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        try:
            with transaction.atomic():
                user = user_for_token(token, queryset=User.objects.select_for_update())
                if user is None:
                    raise ValidationError({'token': [INVALID_LINK]})
                fields = ['is_email_verified']
                if user.pending_email:
                    # Someone may have taken it since the link was sent.
                    if User.objects.filter(email__iexact=user.pending_email).exclude(pk=user.pk).exists():
                        raise ValidationError({'token': [EMAIL_TAKEN]})
                    user.email, user.pending_email = user.pending_email, None
                    fields += ['email', 'pending_email']
                user.is_email_verified = True
                user.save(update_fields=fields)
        except IntegrityError:
            raise ValidationError({'token': [EMAIL_TAKEN]})
        return Response(issue_token_pair(user))
