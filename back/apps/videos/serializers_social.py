"""
ScheduledPost as the front contract (front/src/api/contracts/social.ts) shows it.

`caption`, `hashtags`, `seo_status`, `error_code` and `error_message` are derived:
the model keeps the AI text in `generated_*` and the failure in `error_log`, and
the writers of those columns are not changed by the API.
"""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import ScheduledPost
from .signals import seo_requested

# Codes process_video_seo writes to error_log when the generation fails.
SEO_ERROR_MESSAGES = {
    'seo_no_clip_transcript': "No pudimos generar el texto: el clip no tiene audio para transcribir. Escribilo vos.",
    'content_blocked': "No pudimos generar el texto de este clip. Escribilo vos.",
    'generated_content_blocked': "El texto generado no pasó la revisión. Escribilo vos.",
    'capacity_timeout': "La generación del texto tardó demasiado. Probá de nuevo en unos minutos.",
}
SEO_ERROR_FALLBACK = "No pudimos generar el texto. Escribilo vos."
PUBLISH_ERROR_CODE = 'publish_failed'
PUBLISH_ERROR_MESSAGE = "No pudimos publicar el post. Probá de nuevo en unos minutos."


def _seo_error_code(post):
    """The SEO failure code the post carries, or None (an `error_log` can also hold publish text)."""
    log = post.error_log or ''
    if log in SEO_ERROR_MESSAGES or log.startswith(('seo_', 'moderation_')):
        return log
    return None


class ScheduledPostSerializer(serializers.ModelSerializer):
    caption = serializers.SerializerMethodField()
    hashtags = serializers.SerializerMethodField()
    seo_status = serializers.SerializerMethodField()
    clip = serializers.SerializerMethodField()
    error_code = serializers.SerializerMethodField()
    error_message = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledPost
        fields = (
            'id', 'video_clip', 'platform', 'publish_at', 'status', 'error_log', 'retry_count',
            'generated_caption', 'generated_hashtags', 'created_at', 'updated_at',
            'caption', 'hashtags', 'seo_status', 'clip', 'post_url',
            'error_message', 'error_code', 'tiktok',
        )
        read_only_fields = fields

    def get_caption(self, post) -> str:
        return post.caption or post.generated_caption or ''

    def get_hashtags(self, post) -> list[str]:
        return post.hashtags or post.generated_hashtags or []

    @extend_schema_field({'type': 'string', 'enum': ['none', 'pending', 'ready', 'failed']})
    def get_seo_status(self, post):
        if post.generated_caption and post.generated_hashtags is not None:
            return 'ready'
        if post.status == ScheduledPost.Status.FAILED and _seo_error_code(post):
            return 'failed'
        if post.status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED):
            return 'none'  # the signal never generates for a post already going out
        return 'pending' if seo_requested(post.video_clip.project) else 'none'

    @extend_schema_field({
        'type': 'object',
        'properties': {
            'id': {'type': 'string', 'format': 'uuid'},
            'title': {'type': 'string'},
            'project_id': {'type': 'string', 'format': 'uuid'},
            'project_title': {'type': 'string'},
            'duration_seconds': {'type': 'number'},
            'thumbnail_url': {'type': 'string', 'format': 'uri', 'nullable': True},
        },
    })
    def get_clip(self, post):
        clip = post.video_clip
        return {
            'id': str(clip.id),
            'title': clip.title,
            'project_id': str(clip.project_id),
            'project_title': clip.project.title,
            'duration_seconds': max(clip.end_time - clip.start_time, 0.0),
            # Clips have no thumbnail yet; the field is nullable in the contract.
            'thumbnail_url': None,
        }

    def get_error_code(self, post) -> str | None:
        if post.status != ScheduledPost.Status.FAILED:
            return None
        return _seo_error_code(post) or PUBLISH_ERROR_CODE

    def get_error_message(self, post) -> str | None:
        code = self.get_error_code(post)
        if code is None:
            return None
        if code == PUBLISH_ERROR_CODE:
            return PUBLISH_ERROR_MESSAGE
        return SEO_ERROR_MESSAGES.get(code, SEO_ERROR_FALLBACK)


class PostsQuerySerializer(serializers.Serializer):
    """Query of GET /scheduled-posts/ (PostsQuery in the contract)."""

    status = serializers.ChoiceField(choices=ScheduledPost.Status.choices, required=False)
    platform = serializers.ChoiceField(choices=ScheduledPost.Platform.choices, required=False)
    # Range over publish_at; `from` is a Python keyword, so it is added in get_fields.
    to =serializers.DateTimeField(required=False)
    project = serializers.UUIDField(required=False)
    clip = serializers.UUIDField(required=False)

    def get_fields(self):
        fields = super().get_fields()
        fields['from'] = serializers.DateTimeField(required=False)
        return fields
