import uuid

from django.conf import settings
from django.db import models


class Notification(models.Model):
    """
    One in-app notification (the bell, J2) of a user in a workspace. Created
    only by ``apps.notifications.services``; the table keeps the newest
    ``services.KEPT_PER_USER`` of each user in each workspace.
    """

    class Kind(models.TextChoices):
        # The five cases of map 1.11 (the same names as NotificationPreference).
        PROJECT_READY = 'project_ready', 'Proyecto listo para revisar'
        RENDER_FINISHED = 'render_finished', 'Render terminado'
        PROJECT_FAILED = 'project_failed', 'Proyecto fallido'
        POST_FAILED = 'post_failed', 'Publicación fallida'
        LOW_BALANCE = 'low_balance', 'Saldo bajo'
        # Only in the app: there is no email for these.
        PAYMENT_APPROVED = 'payment_approved', 'Pago aprobado'
        PAYMENT_REJECTED = 'payment_rejected', 'Pago rechazado'

    # ``reason`` of a ``render_finished`` that is of one clip's render that gave
    # nothing (services.notify_clip_render_finished): its email does not say a
    # render ended (emails._content).
    CLIP_RENDER_FAILED = 'clip_render_failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # No index of its own: every read is by user and workspace (the index below starts with the user).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications', db_index=False,
    )
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=300)
    body = models.CharField(max_length=300, blank=True, default='')
    # Which case of its kind it is, when the kind has more than one text for its
    # email: a code this app writes (CLIP_RENDER_FAILED), never text. Empty: the
    # kind's own case. Not sent to the web app: the bell shows title and body.
    reason = models.CharField(max_length=32, blank=True, default='')
    # Where it leads: the web app builds the path from the kind and these ids.
    project = models.ForeignKey('videos.VideoProject', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    post = models.ForeignKey('videos.ScheduledPost', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'workspace', '-created_at'], name='notif_user_ws_created'),
        ]

    def __str__(self):
        return f'{self.kind} {self.pk}'
