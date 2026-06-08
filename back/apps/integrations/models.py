import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from apps.videos.models import VideoProject

class SocialPost(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        SCHEDULED = 'SCHEDULED', _('Scheduled')
        PUBLISHING = 'PUBLISHING', _('Publishing')
        PUBLISHED = 'PUBLISHED', _('Published')
        FAILED = 'FAILED', _('Failed')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name='social_posts')
    
    # Metadata
    caption = models.TextField(blank=True)
    platforms = models.JSONField(default=list, help_text="List of platforms e.g. ['youtube', 'instagram']")
    
    # Orchestration
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    
    # Provider data
    external_id = models.CharField(max_length=255, blank=True, null=True)
    error_log = models.TextField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Post {self.id} - {self.status}"
