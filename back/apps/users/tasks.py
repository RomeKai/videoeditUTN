"""Celery tasks of users."""
import logging

from celery import shared_task
from django.core.management import call_command
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

logger = logging.getLogger(__name__)


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
