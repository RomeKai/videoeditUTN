"""
Social metadata dispatch (ADR-013): a ScheduledPost gets its caption and
hashtags generated right after it is created, once the clip and the platform
are known. Only for projects that asked (and paid) for SEO.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ScheduledPost

logger = logging.getLogger(__name__)


def seo_requested(project):
    """
    True when the project asked for SEO (``metadata['use_seo']``, charged by
    PricingEngine at creation) and the workspace plan includes it. No plan means
    no plan limits, as in PricingEngine.
    """
    if (project.metadata or {}).get('use_seo') is not True:
        return False
    plan = project.workspace.subscription_plan
    return plan is None or bool(plan.has_seo_optimization)


def queue_post_seo(post_id, regenerate=False):
    """
    Queues process_video_seo once the current transaction commits, so the task
    always finds the post. A broker error is logged and never undoes the post.
    """
    def _send():
        from .tasks import process_video_seo

        try:
            if regenerate:
                process_video_seo.apply_async(args=[str(post_id)], kwargs={'regenerate': True})
            else:
                process_video_seo.delay(str(post_id))
        except Exception as exc:
            logger.error(
                "seo.dispatch_failed post_id=%s error=%s", post_id, type(exc).__name__,
            )
            return
        logger.info("seo.dispatched post_id=%s regenerate=%s", post_id, regenerate)

    transaction.on_commit(_send)


@receiver(post_save, sender=ScheduledPost, dispatch_uid='videos.scheduled_post_seo')
def generate_seo_for_new_post(sender, instance, created, raw=False, **kwargs):
    if not created or raw:
        return  # edits never re-generate; fixtures never call the provider
    if instance.generated_caption and instance.generated_hashtags:
        return  # the caller already wrote them
    if instance.status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED):
        return
    if not seo_requested(instance.video_clip.project):
        return
    queue_post_seo(instance.pk)
