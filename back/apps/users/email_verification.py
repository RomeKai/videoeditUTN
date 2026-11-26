"""
Verification link of an email (I1: changing the email; A3 reads the link).

`PATCH /api/v1/me/` with a new `email` keeps it in `User.pending_email` and
sends this link to it. `POST /api/v1/auth/verify-email/` with the link's token
moves it to `User.email`. The token is signed with SECRET_KEY, expires after
EMAIL_VERIFICATION_MAX_AGE_SECONDS and is bound to the state of the account
(its emails, whether it is verified, its password): once used, or once another
change replaces the pending email, the old link stops working.
"""
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.users.models import User

SALT = 'apps.users.email_verification'

# Where the link takes the user after verifying (the web app's profile, I1).
AFTER_VERIFY_PATH = '/perfil'


def _state(user):
    value = f'{user.pk}|{user.email}|{user.pending_email}|{user.is_email_verified}|{user.password}'
    return salted_hmac(SALT, value).hexdigest()[:32]


def make_token(user):
    return signing.dumps({'u': str(user.pk), 's': _state(user)}, salt=SALT)


def user_for_token(token, queryset=None):
    """The user the token was made for, while it is valid; else None."""
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE_SECONDS)
        user_id = uuid.UUID(str(data['u']))
        state = str(data['s'])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    user = (queryset if queryset is not None else User.objects).filter(pk=user_id).first()
    if user is None or not constant_time_compare(state, _state(user)):
        return None
    return user


def verification_link(user):
    query = urlencode({'token': make_token(user), 'siguiente': AFTER_VERIFY_PATH})
    return f"{settings.FRONTEND_URL.rstrip('/')}/verificar?{query}"


def send_email_change_verification(user):
    """Emails the link to `user.pending_email` (Django's email backend)."""
    hours = max(1, settings.EMAIL_VERIFICATION_MAX_AGE_SECONDS // 3600)
    greeting = f'Hola, {user.first_name}:' if user.first_name else 'Hola:'
    message = (
        f'{greeting}\n\n'
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
