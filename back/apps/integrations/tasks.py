import logging
from celery import shared_task
from django.utils import timezone
from .models import SocialPost
from .ayrshare_api import AyrshareClient, AyrshareAPIError

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def publish_social_post(self, post_id):
    """
    Background task to publish a SocialPost via Ayrshare.
    """
    try:
        post = SocialPost.objects.get(pk=post_id)
    except SocialPost.DoesNotExist:
        logger.error(f"Post {post_id} not found.")
        return

    if post.status == SocialPost.Status.PUBLISHED:
        return

    post.status = SocialPost.Status.PUBLISHING
    post.save()

    # Determine media URL (using proxy or final output)
    # Note: In production, we should use a signed public URL from S3/R2
    media_url = post.project.output_file.url if post.project.output_file else None
    
    if not media_url:
        post.status = SocialPost.Status.FAILED
        post.error_log = "No output file found for this project."
        post.save()
        return

    try:
        # Dummy profile key for now, should be in Workspace settings
        profile_key = "DEFAULT_PROFILE_KEY" 
        
        result = AyrshareClient.send_post(
            profile_key=profile_key,
            s3_media_url=media_url,
            caption=post.caption,
            platforms=post.platforms
        )
        
        post.status = SocialPost.Status.PUBLISHED
        post.external_id = result.get("id")
        post.published_at = timezone.now()
        post.save()

    except AyrshareAPIError as e:
        post.status = SocialPost.Status.FAILED
        post.error_log = str(e)
        post.save()
        # Retry logic
        raise self.retry(exc=e, countdown=60 * 5)
