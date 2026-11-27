"""
Workspace invitations (gap 13; contract: front/src/api/contracts/workspaces.ts,
rules: apps.users.invitations).

- `POST /workspaces/<id>/invitations/` and `.../<memberId>/resend/`: owner and
  admins (`members.manage`), with the workspace's row lock first, like every
  members endpoint (views_workspaces).
- `POST /invitations/preview/` and `POST /invitations/accept/`: the link of
  the email, without a session or with one. The token travels in the BODY
  (`{token}`), never in the path: Django's request logger writes the path of
  every 4xx and the web server's access log writes every path, so a token
  there would be a working link in the logs.

A token that never existed, an expired one, a used one, a cancelled one and
a body without one get the same answer (404 to read, 410 to accept). Nothing
here logs an email or a token, no URL of the API carries one, and the task
only gets the id of the row.
"""
import logging
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from kombu.exceptions import OperationalError as BrokerError
from rest_framework.exceptions import NotAuthenticated, NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import require_workspace_role
from apps.users.invitations import inviter_name, pending_for_token
from apps.users.models import User, Workspace, WorkspaceMember
from apps.users.seats import check_seat_for_accept, check_seat_limit
from apps.users.serializers import check_new_password
from apps.users.serializers_workspaces import (
    ALREADY_MEMBER,
    AcceptedInvitationSerializer,
    AcceptInvitationSerializer,
    InvitationPreviewSerializer,
    InvitationTokenSerializer,
    InvitedSerializer,
    InviteSerializer,
    MemberSerializer,
)
from apps.users.tasks import send_invitation_email
from apps.users.throttles import AttemptThrottle, SpanishThrottleMixin, email_ident
from apps.users.tokens import issue_token_pair
from apps.users.views import THROTTLED, UNAUTHORIZED
from apps.users.views_auth import EMAIL_NOT_QUEUED, NO_CONTENT, EmailNotQueued, PublicAccountView, _workspace_name
from apps.users.views_workspaces import FORBIDDEN, NOT_FOUND, _error, _member_or_404, _member_payload

logger = logging.getLogger(__name__)

ACTIVE = WorkspaceMember.Status.ACTIVE
PENDING = WorkspaceMember.Status.PENDING

INVITATION_NOT_PENDING = 'La invitación ya no está pendiente.'
INVITATION_NOT_FOUND = 'Esta invitación no existe o ya no sirve.'
INVITATION_GONE = 'La invitación venció, ya se usó o se canceló. Pedile a quien te invitó que te mande una nueva.'
SIGN_IN_FIRST = 'Ya existe una cuenta con el email de la invitación. Iniciá sesión con ese email para aceptarla.'
OTHER_ACCOUNT = 'Esta invitación es para otro email. Cerrá sesión y entrá con la cuenta de ese email para aceptarla.'

GONE = OpenApiResponse(description=(
    '`{error, code: "invitation_invalid"}`: el token no existe, venció, ya se usó o la invitación se canceló. '
    'La misma respuesta para todos los casos.'
))
SEATS = '`{error, code: "seats_exceeded"}`: no quedan lugares en el plan.'


class WorkspaceSendThrottle(AttemptThrottle):
    """
    Invitations sent from one workspace (invite and resend together), whoever
    sends them. Checked by the view once the role is known: a request from
    someone who cannot invite there does not count against the workspace.
    """

    scope_attr = 'workspace_throttle_scope'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': f'workspace:{view.throttled_workspace_id}'}


class RecipientThrottle(AttemptThrottle):
    """
    One window of the invitation emails (invite and resend together) to one
    address, keyed by the HMAC of the address (throttles.email_ident, any
    spelling), never the address. See check_recipients.
    """

    def __init__(self, scope, ident):
        self.scope = scope
        self.ident = ident
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)

    def get_cache_key(self, request=None, view=None):
        return self.cache_format % {'scope': self.scope, 'ident': self.ident}

    def has_room(self):
        """Whether one more email fits in the window. Counts nothing (``record`` does)."""
        self.key = self.get_cache_key()
        self.now = self.timer()
        self.history = [moment for moment in self.cache.get(self.key, []) if moment > self.now - self.duration]
        return len(self.history) < self.num_requests

    def record(self):
        self.history.insert(0, self.now)
        self.cache.set(self.key, self.history, self.duration)


def check_recipients(request, view, workspace_id, emails):
    """
    The limits of the invitation emails per address, for invite and resend:

    - `invitation_recipient_workspace`, per workspace and address: cancelling
      an invitation and inviting the address again counts like a resend;
    - `invitation_recipient`, per address from any workspace, looser: several
      workspaces cannot fill a mailbox either, and a workspace that spends its
      own limit does not lock out another workspace's emails to that address.

    The limits per user and per workspace still let one workspace send all of
    its emails to one address. Called with the workspace locked, before any row
    changes: every address is checked first and only then counted, so a
    refused request (the 429) counts nothing and changes nothing.
    """
    throttles = []
    for email in emails:
        ident = email_ident(email)
        throttles.append(RecipientThrottle('invitation_recipient_workspace', f'workspace:{workspace_id}:{ident}'))
        throttles.append(RecipientThrottle('invitation_recipient', ident))
    full = [throttle for throttle in throttles if not throttle.has_room()]
    if full:
        waits = [throttle.wait() for throttle in full]
        view.throttled(request, None if None in waits else max(waits))
    for throttle in throttles:
        throttle.record()


def _token_of(request):
    """The token of the body, or None: a body without one is answered like a token nobody has."""
    data = request.data
    token = data.get('token') if hasattr(data, 'get') else None
    return token if isinstance(token, str) else None


def _is_email_taken(error):
    """Whether an IntegrityError is the unique constraint of User.email (an account of that email exists)."""
    constraint = getattr(getattr(error.__cause__, 'diag', None), 'constraint_name', None)
    return constraint in EMAIL_CONSTRAINTS


# User.email is unique as stored (the column) and without case (User.Meta).
EMAIL_CONSTRAINTS = frozenset({'users_user_email_key', 'users_user_email_upper_unique'})


class IpAttemptThrottle(AttemptThrottle):
    """Per IP also with a session: the link is the secret, whoever tries it."""

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


def _queue(member_id):
    send_invitation_email.delay(str(member_id))


class InvitationSendView(SpanishThrottleMixin, APIView):
    """
    What invite and resend share: the session, the limit per user, the one per
    workspace and the ones per address (check_recipients).
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [AttemptThrottle]
    throttle_scope = 'invitation_send'
    workspace_throttle_scope = 'invitation_send_workspace'

    def check_workspace_throttle(self, request, workspace_id):
        """After the role check (without the lock): 404 and 403 come first and do not count."""
        workspace = require_workspace_role(request.user, workspace_id, 'members.manage').workspace
        self.throttled_workspace_id = workspace.pk
        throttle = WorkspaceSendThrottle()
        if not throttle.allow_request(request, self):
            self.throttled(request, throttle.wait())


class WorkspaceInvitationsView(InvitationSendView):
    @extend_schema(
        request=InviteSerializer,
        responses={
            201: InvitedSerializer,
            400: OpenApiResponse(description=(
                '`emails`: una lista plana con una oración (falta, hay uno inválido, son demasiados, o alguno ya es '
                'miembro o tiene una invitación pendiente). `role`: no es admin, editor ni viewer.'
            )),
            401: UNAUTHORIZED,
            403: OpenApiResponse(description=f'{SEATS} O el rol no administra miembros: solo `detail`.'),
            404: NOT_FOUND,
            429: THROTTLED,
        },
    )
    def post(self, request, workspace_id):
        """
        Invites one or several emails with a role: owner and admins. All of
        them or none. Each one is a PENDING row that takes a seat of the plan
        from now (seats.check_seat_limit, with the workspace locked), and gets
        an email with its link, which counts in the limits per address
        (check_recipients). The answer never says whether an email has an
        account: `user` is null in every pending row.
        """
        self.check_workspace_throttle(request, workspace_id)
        serializer = InviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        emails, role = serializer.validated_data['emails'], serializer.validated_data['role']
        with transaction.atomic():
            workspace = require_workspace_role(request.user, workspace_id, 'members.manage', lock=True).workspace
            taken = {
                (row['user__email'] or row['invited_email']).upper()
                for row in WorkspaceMember.objects.filter(workspace=workspace).values('user__email', 'invited_email')
            }
            # The owner is a member even without a row of their own.
            taken.add(User.objects.values_list('email', flat=True).get(pk=workspace.owner_id).upper())
            repeated = next((email for email in emails if email.upper() in taken), None)
            if repeated is not None:
                raise ValidationError({'emails': [ALREADY_MEMBER.format(email=repeated)]})
            try:
                check_seat_limit(workspace, [None] * len(emails))
            except DjangoValidationError as exc:
                return _error(403, exc.messages[0], 'seats_exceeded')
            # Before any row: a refused invitation (429) leaves nothing pending.
            check_recipients(request, self, workspace.pk, emails)
            members = [
                WorkspaceMember.objects.create(
                    workspace=workspace, role=role, status=PENDING, invited_email=email, invited_by=request.user,
                )
                for email in emails
            ]
        for member in members:
            # Once the rows are committed. A queue that is down does not undo the invitation:
            # the row stays pending without a link, and «Reenviar» sends it.
            try:
                _queue(member.pk)
            except (BrokerError, OSError) as exc:
                logger.warning('invitation.not_queued member_id=%s error=%s', member.pk, type(exc).__name__)
        logger.info(
            'invitation.created workspace_id=%s user_id=%s count=%d member_ids=%s',
            workspace.pk, request.user.pk, len(members), ','.join(str(member.pk) for member in members),
        )
        body = InvitedSerializer({'invited': [_member_payload(member, workspace) for member in members]})
        return Response(body.data, status=201)


class WorkspaceInvitationResendView(InvitationSendView):
    @extend_schema(
        request=None,
        responses={
            204: NO_CONTENT,
            401: UNAUTHORIZED,
            403: FORBIDDEN,
            404: OpenApiResponse(description='El workspace no existe para este usuario, o la invitación ya se aceptó o se canceló.'),
            429: THROTTLED,
            502: EMAIL_NOT_QUEUED,
        },
    )
    def post(self, request, workspace_id, member_id):
        """
        Sends the link again: owner and admins. The link sent before stops
        working at once, and the new one starts its own expiry.
        """
        self.check_workspace_throttle(request, workspace_id)
        with transaction.atomic():
            workspace = require_workspace_role(request.user, workspace_id, 'members.manage', lock=True).workspace
            try:
                member = _member_or_404(workspace, member_id, lock=True)
            except NotFound:
                raise NotFound(INVITATION_NOT_PENDING)
            if member.status != PENDING:
                raise NotFound(INVITATION_NOT_PENDING)
            if not member.invited_email:
                # A pending row of before the invitations (made in the admin): its account's email.
                member.invited_email = User.objects.values_list('email', flat=True).get(pk=member.user_id)
            # Before the old link is touched: a refused resend changes nothing.
            check_recipients(request, self, workspace.pk, [member.invited_email])
            member.invitation_token_hash = None
            member.invitation_sent_at = None
            member.save(update_fields=['invited_email', 'invitation_token_hash', 'invitation_sent_at'])
        try:
            _queue(member.pk)
        except (BrokerError, OSError) as exc:
            logger.warning('invitation.not_queued member_id=%s error=%s', member.pk, type(exc).__name__)
            raise EmailNotQueued()
        logger.info('invitation.resent workspace_id=%s member_id=%s user_id=%s', workspace.pk, member.pk, request.user.pk)
        return Response(status=204)


class InvitationView(PublicAccountView):
    """
    A5: what the link shows. A POST with the token in the body, so it is in
    no path. No session is read. Every token that does not open a pending
    invitation right now (and a body without one) is the same 404, so the
    answer does not tell a made-up token from one that expired, was used or
    was cancelled.
    """

    throttle_classes = [AttemptThrottle]
    throttle_scope = 'invitation_read'

    @extend_schema(
        request=InvitationTokenSerializer,
        responses={
            200: InvitationPreviewSerializer,
            404: OpenApiResponse(description='El token no abre una invitación pendiente: falta, no existe, venció, ya se usó o se canceló.'),
            429: THROTTLED,
        },
    )
    def post(self, request):
        member = pending_for_token(_token_of(request), WorkspaceMember.objects.select_related('workspace', 'invited_by'))
        if member is None:
            raise NotFound(INVITATION_NOT_FOUND)
        body = InvitationPreviewSerializer({
            'workspace_id': member.workspace_id,
            'workspace_name': member.workspace.name,
            'inviter_name': inviter_name(member),
            'role': member.role,
            'email': member.invited_email,
            'status': PENDING,
            'has_account': User.objects.filter(email__iexact=member.invited_email).exists(),
        })
        return Response(body.data)


class InvitationAcceptView(SpanishThrottleMixin, APIView):
    """
    A5: accepts the invitation of the link.

    - With a session: only the account of the invited email (403
      `invitation_email_mismatch` for any other). Only `token` is read. The
      link reached that mailbox, so the account's email is verified from now.
    - Without one: creates the account of the invited email with `name` and
      `password` (the rules of the sign-up), already verified (the link
      reached that mailbox), and answers its tokens. If the email already has
      an account it is a 401: sign in first.

    Everything happens in one transaction with the workspace's row locked
    (then the invitation's): the account, its own workspace (the post_save
    signal of User) and the membership are saved together or not at all, two
    requests with the same link run one after the other (the second gets the
    410), and the seats are checked again (seats.check_seat_for_accept).
    """

    permission_classes = [AllowAny]
    throttle_classes = [IpAttemptThrottle]
    throttle_scope = 'invitation_accept'

    @extend_schema(
        request=AcceptInvitationSerializer,
        responses={
            200: AcceptedInvitationSerializer,
            400: OpenApiResponse(description='El token va en el cuerpo (`token`). Sin sesión: falta `name` o `password`, la contraseña no cumple las reglas, o `terms_accepted` no es `true`.'),
            401: OpenApiResponse(description='El email de la invitación ya tiene cuenta y no hay sesión (`detail`), o el token de la sesión no sirve.'),
            403: OpenApiResponse(description=(
                '`{error, code: "invitation_email_mismatch"}`: la sesión es de una cuenta con otro email. ' + SEATS
            )),
            410: GONE,
            429: THROTTLED,
        },
    )
    def post(self, request):
        token = _token_of(request)
        session_user = request.user if request.user.is_authenticated else None
        try:
            with transaction.atomic():
                found = pending_for_token(token)
                # The workspace first, like every members endpoint; then the invitation, read again.
                workspace = (
                    Workspace.objects.select_for_update().filter(pk=found.workspace_id).first() if found is not None else None
                )
                member = (
                    pending_for_token(token, WorkspaceMember.objects.select_for_update().filter(workspace=workspace))
                    if workspace is not None else None
                )
                if member is None:
                    return _error(410, INVITATION_GONE, 'invitation_invalid')
                if session_user is not None:
                    # Compared in the database, like the login finds an account (`iexact`).
                    if not User.objects.filter(pk=session_user.pk, email__iexact=member.invited_email).exists():
                        logger.warning(
                            'invitation.email_mismatch member_id=%s workspace_id=%s user_id=%s',
                            member.pk, workspace.pk, session_user.pk,
                        )
                        return _error(403, OTHER_ACCOUNT, 'invitation_email_mismatch')
                    user, created = session_user, False
                    if not user.is_email_verified:
                        # The link reached the mailbox of this account: that is what verifying is.
                        user.is_email_verified = True
                        user.save(update_fields=['is_email_verified'])
                else:
                    if User.objects.filter(email__iexact=member.invited_email).exists():
                        raise NotAuthenticated(SIGN_IN_FIRST)
                    user, created = self._create_account(request, member), True
                member.user = user
                try:
                    check_seat_for_accept(workspace, member)
                except DjangoValidationError as exc:
                    transaction.set_rollback(True)
                    return _error(403, exc.messages[0], 'seats_exceeded')
                other = WorkspaceMember.objects.filter(workspace=workspace, user=user).exclude(pk=member.pk).first()
                if other is not None:
                    # Already in (a row made in the admin): the invitation is spent and its seat is free.
                    member_id = member.pk
                    member.delete()
                    if other.status != ACTIVE:
                        other.status = ACTIVE
                        other.save(update_fields=['status'])
                else:
                    member_id = member.pk
                    member.status = ACTIVE
                    member.invitation_token_hash = None
                    member.invitation_sent_at = None
                    # An active member is its account: the invited address is not kept.
                    member.invited_email = ''
                    member.joined_at = timezone.now()
                    member.save(update_fields=[
                        'user', 'status', 'invitation_token_hash', 'invitation_sent_at', 'invited_email', 'joined_at',
                    ])
        except IntegrityError as exc:
            # Only the email that got its account between the check and the insert (a sign-up at
            # the same time), and only when this request was creating it. Anything else is a bug.
            if session_user is not None or not _is_email_taken(exc):
                raise
            raise NotAuthenticated(SIGN_IN_FIRST)
        logger.info(
            'invitation.accepted workspace_id=%s member_id=%s user_id=%s account_created=%s',
            workspace.pk, member_id, user.pk, created,
        )
        body = AcceptedInvitationSerializer({
            'workspace_id': workspace.pk,
            'tokens': issue_token_pair(user) if created else None,
        })
        return Response(body.data)

    @staticmethod
    def _create_account(request, member):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        first_name, _, last_name = data['name'].partition(' ')
        last_name = last_name.strip()
        try:
            check_new_password(data['password'], User(email=member.invited_email, first_name=first_name, last_name=last_name))
        except ValidationError as exc:
            raise ValidationError({'password': exc.detail})
        user = User.objects.create_user(
            # Required and unique in AbstractUser; the account signs in with its email.
            username=uuid.uuid4().hex,
            email=member.invited_email,
            password=data['password'],
            first_name=first_name,
            last_name=last_name,
            terms_accepted_at=timezone.now(),
            # The link reached this mailbox: nothing else to verify.
            is_email_verified=True,
        )
        # The signal names it after the username; here there is a name.
        Workspace.objects.filter(owner=user).update(name=_workspace_name(first_name))
        return user
