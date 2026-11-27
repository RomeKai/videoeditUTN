"""
Workspace invitations (gap 13; contract: front/src/api/contracts/workspaces.ts).

An invitation is a WorkspaceMember row in PENDING: it takes a seat of the
plan from the moment it is sent (apps.users.seats). It belongs to an EMAIL
(`invited_email`, normalized), not to whoever holds the link: only the
account of that email accepts it, or the account the link creates with it.

The token of the link is random (32 bytes, urlsafe) and only its SHA-256 is
stored (`invitation_token_hash`): reading the database gives no working
link. It is issued by the task that sends the email
(apps.users.tasks.send_invitation_email), so it never travels in a Celery
message either. It works once, for INVITATION_MAX_AGE_SECONDS since it was
issued (`invitation_sent_at`). A resend clears the hash at once and the task
issues another; removing the pending member deletes the row.

The token never travels in a URL path or query, where web servers and
Django's request logger would write it down: the link of the email carries it
in the fragment (`/invitacion#token=…`, which a browser never sends), and
the web app sends it to the API in the body of a POST
(`/invitations/preview/`, `/invitations/accept/`).

The email carries nothing a user typed (no workspace name, no inviter name):
the address can be anyone's, and the official sender must not carry someone
else's text to it. The page of the link (A5) shows who invites and where.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.users.email_verification import _frontend_link
from apps.users.models import WorkspaceMember

PENDING = WorkspaceMember.Status.PENDING

# The web app's page of an invitation (front/src/app/(public)/invitacion): it reads `#token=`.
INVITATION_PATH = '/invitacion'
# What the page shows when the inviter has no name, or their account is gone. Never their email.
INVITER_FALLBACK = 'Alguien del equipo'


def new_token():
    """`(token, hash)`: the token goes in the email, the hash in the row."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token):
    # A plain SHA-256 is enough: the token has 256 random bits, there is nothing to guess.
    return hashlib.sha256(str(token).encode('utf-8', 'surrogatepass')).hexdigest()


def is_expired(member, now=None):
    sent_at = member.invitation_sent_at
    if sent_at is None:
        return True
    return (now or timezone.now()) - sent_at > timedelta(seconds=settings.INVITATION_MAX_AGE_SECONDS)


def pending_for_token(token, queryset=None):
    """
    The PENDING row a token opens, or None: a token that never existed, one
    already used, one replaced by a resend, one of a removed invitation and an
    expired one are all the same None.
    """
    if not isinstance(token, str) or not token or len(token) > 200:
        return None
    rows = queryset if queryset is not None else WorkspaceMember.objects.all()
    member = rows.filter(invitation_token_hash=token_hash(token), status=PENDING).first()
    if member is None or is_expired(member):
        return None
    return member


def invitation_link(token):
    """`/invitacion#token=…`: in the fragment, so it stays out of the web host's logs (like the reset link)."""
    return _frontend_link(INVITATION_PATH, {'token': token}, fragment=True)


def inviter_name(member):
    """The inviter's display name for the public page. Never an email."""
    user = member.invited_by
    name = f'{user.first_name} {user.last_name}'.strip() if user is not None else ''
    return name or INVITER_FALLBACK


def send_invitation(member, token):
    """Emails the link to `member.invited_email` (Django's email backend)."""
    days = max(1, settings.INVITATION_MAX_AGE_SECONDS // 86400)
    message = (
        'Hola:\n\n'
        'Te invitaron a sumarte a un espacio de trabajo de OneCreator. '
        'Abrí este link para ver quién te invita y aceptar:\n\n'
        f'{invitation_link(token)}\n\n'
        f'El link vence en {days} {"día" if days == 1 else "días"} y sirve una sola vez. '
        'Es personal: no lo reenvíes.\n\n'
        'Si no esperabas esta invitación, ignorá este mensaje: no cambia nada.\n'
    )
    send_mail(
        subject='Te invitaron a un espacio de trabajo de OneCreator',
        message=message,
        from_email=None,
        recipient_list=[member.invited_email],
    )
