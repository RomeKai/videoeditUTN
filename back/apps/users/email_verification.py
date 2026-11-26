"""
Verification links of an email (A3 reads them).

Two purposes share `POST /api/v1/auth/verify-email/`:

- EMAIL_CHANGE (I1): `PATCH /api/v1/me/` with a new `email` keeps it in
  `User.pending_email` and sends this link to it. Opening it moves it to
  `User.email`, but only with the session of that same account: holding the
  inbox is not enough to take the account (a typo in the address would hand
  it to whoever owns the typo).
- SIGNUP (A2, when registration exists): verifies the account's own email and
  signs the user in, as the contract asks.

The token is signed with SECRET_KEY, says its purpose, expires after
EMAIL_VERIFICATION_MAX_AGE_SECONDS and is bound to the state of the account
(its emails, whether it is verified, its password): once used, once the
pending email is cancelled or replaced, or after a password change, the old
link stops working.
"""
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.users.models import User

SALT = 'apps.users.email_verification'

EMAIL_CHANGE = 'email_change'
SIGNUP = 'signup'

# Where the link takes the user after verifying (the web app's profile, I1).
AFTER_VERIFY_PATH = '/perfil'


def _state(user):
    value = f'{user.pk}|{user.email}|{user.pending_email}|{user.is_email_verified}|{user.password}'
    return salted_hmac(SALT, value).hexdigest()[:32]


def make_token(user, purpose=EMAIL_CHANGE):
    return signing.dumps({'u': str(user.pk), 's': _state(user), 'p': purpose}, salt=SALT)


def user_for_token(token, queryset=None):
    """`(user, purpose)` of a valid token; else None."""
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE_SECONDS)
        user_id = uuid.UUID(str(data['u']))
        state = str(data['s'])
        purpose = data.get('p', EMAIL_CHANGE)
    except (signing.BadSignature, KeyError, TypeError, ValueError, AttributeError):
        return None
    if purpose not in (EMAIL_CHANGE, SIGNUP):
        return None
    user = (queryset if queryset is not None else User.objects).filter(pk=user_id, is_active=True).first()
    if user is None or not constant_time_compare(state, _state(user)):
        return None
    if purpose == EMAIL_CHANGE and not user.pending_email:
        return None
    return user, purpose


def verification_link(user):
    query = urlencode({'token': make_token(user), 'siguiente': AFTER_VERIFY_PATH})
    return f"{settings.FRONTEND_URL.rstrip('/')}/verificar?{query}"


def send_email_change_verification(user):
    """Emails the link to `user.pending_email` (Django's email backend)."""
    hours = max(1, settings.EMAIL_VERIFICATION_MAX_AGE_SECONDS // 3600)
    # Nothing the user typed goes in: the address can be anyone's, and the
    # official sender must not carry someone else's text to it.
    message = (
        'Hola:\n\n'
        'Pediste usar este email en tu cuenta de OneCreator. Para confirmarlo, abrí este link:\n\n'
        f'{verification_link(user)}\n\n'
        f'El link vence en {hours} horas. Hasta que lo abras, tu cuenta sigue con el email anterior.\n\n'
        'Si no fuiste vos, ignorá este mensaje: tu cuenta no cambia.\n'
    )
    send_mail(
        subject='Confirmá tu nuevo email de OneCreator',
        message=message,
        from_email=None,
        recipient_list=[user.pending_email],
    )
