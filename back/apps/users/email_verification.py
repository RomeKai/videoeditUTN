"""
The links the account emails carry (A3 and A4 read them).

Two purposes share `POST /api/v1/auth/verify-email/`:

- EMAIL_CHANGE (I1): `PATCH /api/v1/me/` with a new `email` keeps it in
  `User.pending_email` and sends this link to it. Opening it moves it to
  `User.email`, but only with the session of that same account: holding the
  inbox is not enough to take the account (a typo in the address would hand
  it to whoever owns the typo).
- SIGNUP (A2): `POST /api/v1/auth/register/` and
  `POST /api/v1/auth/verify-email/resend/` send it to the account's email.
  Opening it verifies that email and signs the user in, as the contract asks.

The token is signed with SECRET_KEY, says its purpose, expires after
EMAIL_VERIFICATION_MAX_AGE_SECONDS and is bound to the state of the account
(its emails, whether it is verified, its password): once used, once the
pending email is cancelled or replaced, or after a password change, the old
link stops working.

The password reset link (A4, `POST /api/v1/auth/password-reset/`) carries a
token of Django's PasswordResetTokenGenerator, with its own salt: it expires
after PASSWORD_RESET_TIMEOUT and is bound to the password, the email and the
last login, so it works once and stops working after any password change.

No email carries anything the user typed: the address can be anyone's, and
the official sender must not carry someone else's text to it.
"""
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
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


def _frontend_link(path, query):
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}?{urlencode(query)}"


def _hours(seconds):
    return max(1, seconds // 3600)


def verification_link(user):
    return _frontend_link('/verificar', {'token': make_token(user), 'siguiente': AFTER_VERIFY_PATH})


def signup_link(user):
    """No `siguiente`: A3 goes on to the page it remembered, or to the welcome (B1)."""
    return _frontend_link('/verificar', {'token': make_token(user, SIGNUP)})


def send_email_change_verification(user):
    """Emails the link to `user.pending_email` (Django's email backend)."""
    hours = _hours(settings.EMAIL_VERIFICATION_MAX_AGE_SECONDS)
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


def send_signup_verification(user):
    """Emails the link that verifies a new account to `user.email`."""
    hours = _hours(settings.EMAIL_VERIFICATION_MAX_AGE_SECONDS)
    message = (
        'Hola:\n\n'
        'Para terminar de crear tu cuenta de OneCreator, confirmá tu email con este link:\n\n'
        f'{signup_link(user)}\n\n'
        f'El link vence en {hours} horas. Al abrirlo entrás a tu cuenta.\n\n'
        'Si no creaste una cuenta, ignorá este mensaje.\n'
    )
    send_mail(
        subject='Confirmá tu email de OneCreator',
        message=message,
        from_email=None,
        recipient_list=[user.email],
    )


class PasswordResetTokens(PasswordResetTokenGenerator):
    """Django's generator with a salt of its own: no other token of the project passes for one of these."""

    key_salt = 'apps.users.email_verification.password_reset'


password_reset_tokens = PasswordResetTokens()


def make_password_reset_token(user):
    """`<id of the account>.<Django's token>`: the confirm endpoint only gets the token."""
    return f'{user.pk.hex}.{password_reset_tokens.make_token(user)}'


def user_for_password_reset_token(token, queryset=None):
    """The active account of a valid token; else None."""
    account, _, secret = (token or '').partition('.')
    try:
        user_id = uuid.UUID(hex=account)
    except ValueError:
        return None
    user = (queryset if queryset is not None else User.objects).filter(pk=user_id, is_active=True).first()
    if user is None or not password_reset_tokens.check_token(user, secret):
        return None
    return user


def password_reset_link(user):
    """`/recuperar?token=…&email=…` (contract): with the email, A4 signs in after saving."""
    return _frontend_link('/recuperar', {'token': make_password_reset_token(user), 'email': user.email})


def send_password_reset(user):
    """Emails the link to choose a new password to `user.email`."""
    hours = _hours(settings.PASSWORD_RESET_TIMEOUT)
    message = (
        'Hola:\n\n'
        'Pediste elegir una contraseña nueva para tu cuenta de OneCreator. Hacelo con este link:\n\n'
        f'{password_reset_link(user)}\n\n'
        f'El link vence en {hours} horas y sirve una sola vez.\n\n'
        'Si no lo pediste, ignorá este mensaje: tu contraseña no cambia.\n'
    )
    send_mail(
        subject='Elegí una contraseña nueva para OneCreator',
        message=message,
        from_email=None,
        recipient_list=[user.email],
    )
