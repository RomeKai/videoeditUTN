"""Celery tasks of notifications."""
import logging

from backend.celery import app as celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name='apps.notifications.tasks.send_notification_email', ignore_result=True)
def send_notification_email(notification_id):
    """
    The email of one notification of the bell (``apps.notifications.emails``).
    ``services._write`` queues it once the notification's row is committed,
    with its id only: who it is for and what it says are read here.

    Nothing is sent when the row is gone (the bell keeps the newest 20), when
    its kind has no email, when the account is inactive, has no verified
    email (the address could be someone else's) or is no longer in the
    workspace, or when the user turned that kind off in Perfil ›
    Notificaciones. A failed send is logged with ids and the error class and
    is not tried again: the notification is still in the bell.
    """
    from .emails import EMAIL_KINDS, send, wants_email
    from .models import Notification
    from .services import recipients_of

    notification = (
        Notification.objects.select_related('user', 'user__notification_preferences', 'workspace')
        .filter(pk=notification_id).first()
    )
    if notification is None:
        logger.info('notification_email.skipped reason=no_notification notification_id=%s', notification_id)
        return
    user, kind = notification.user, notification.kind

    def skipped(reason):
        logger.info(
            'notification_email.skipped reason=%s kind=%s notification_id=%s user_id=%s',
            reason, kind, notification.pk, user.pk,
        )

    if kind not in EMAIL_KINDS:
        return skipped('no_email_for_kind')
    if not user.is_active or not user.is_email_verified or not user.email:
        return skipped('no_verified_account')
    if user.pk not in recipients_of(notification.workspace):
        return skipped('not_a_member')
    if not wants_email(user, kind):
        return skipped('turned_off')
    try:
        send(notification)
    except Exception as exc:
        logger.warning(
            'notification_email.send_failed kind=%s notification_id=%s user_id=%s error=%s',
            kind, notification.pk, user.pk, type(exc).__name__,
        )
        return
    logger.info('notification_email.sent kind=%s notification_id=%s user_id=%s', kind, notification.pk, user.pk)
