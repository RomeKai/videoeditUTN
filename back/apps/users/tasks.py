"""Celery tasks of users."""
import logging
import smtplib

from celery import shared_task
from django.core.management import call_command
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

logger = logging.getLogger(__name__)

# The emails send_account_email knows.
RESEND_VERIFICATION = 'resend_verification'
PASSWORD_RESET = 'password_reset'


@shared_task(name='apps.users.tasks.flush_expired_tokens')
def flush_expired_tokens():
    """
    Deletes the refresh tokens that already expired (SimpleJWT's
    `flushexpiredtokens`). Every login and every rotation adds a row to
    OutstandingToken, and every rotation one to BlacklistedToken: without
    this, both tables grow for ever. An expired token is refused by its date,
    so its rows decide nothing. Runs daily from Celery Beat
    (CELERY_BEAT_SCHEDULE, 'flush-expired-tokens'); running it twice is harmless.
    """
    before = OutstandingToken.objects.count(), BlacklistedToken.objects.count()
    call_command('flushexpiredtokens')
    after = OutstandingToken.objects.count(), BlacklistedToken.objects.count()
    counts = {
        'outstanding_deleted': max(before[0] - after[0], 0),
        'blacklisted_deleted': max(before[1] - after[1], 0),
        'outstanding_left': after[0],
        'blacklisted_left': after[1],
    }
    logger.info(
        'tokens.expired_flushed outstanding_deleted=%d blacklisted_deleted=%d outstanding_left=%d blacklisted_left=%d',
        counts['outstanding_deleted'], counts['blacklisted_deleted'], counts['outstanding_left'], counts['blacklisted_left'],
    )
    return counts


@shared_task(name='apps.users.tasks.send_account_email', ignore_result=True)
def send_account_email(kind, user_id):
    """
    The email of resend the verification (RESEND_VERIFICATION) or of password
    reset (PASSWORD_RESET). apps.users.views_auth queues it for every request,
    with the id of the account of the address or None: the endpoint answers
    the same, in the same time, whether the address has an account or not,
    and sending (the slow part) happens here. Nothing is sent without an
    active account, and the verification only to one not verified yet. A
    failed send is logged with the account id and the error class; the user
    can ask again after 60 s.
    """
    from apps.users.email_verification import send_password_reset, send_signup_verification
    from apps.users.models import User

    user = User.objects.filter(pk=user_id, is_active=True).first() if user_id else None
    if user is None:
        logger.info('account_email.skipped kind=%s reason=no_account', kind)
        return
    if kind == RESEND_VERIFICATION:
        if user.is_email_verified:
            logger.info('account_email.skipped kind=%s reason=verified user_id=%s', kind, user.pk)
            return
        send = send_signup_verification
    elif kind == PASSWORD_RESET:
        send = send_password_reset
    else:
        logger.error('account_email.unknown_kind kind=%s', kind)
        return
    try:
        send(user)
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning('account_email.send_failed kind=%s user_id=%s error=%s', kind, user.pk, type(exc).__name__)
        return
    logger.info('account_email.sent kind=%s user_id=%s', kind, user.pk)
