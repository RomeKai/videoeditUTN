"""
ScheduledPost as the front contract (front/src/api/contracts/social.ts) shows it.

`caption`, `hashtags`, `seo_status`, `error_code` and `error_message` are derived:
the model keeps the AI text in `generated_*` and the failure in `error_log`, and
the writers of those columns are not changed by the API. The API writes only
what the user chose (`caption`, `hashtags`, `tiktok`, `publish_at`) and the
status of a post that has not gone out (services/scheduled_posts.py).
"""
import re

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
        from .services.scheduled_posts import seo_request_pending

        going_out = post.status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED)
        if not going_out and seo_request_pending(post):
            # Asked for (created, or "Regenerar") and not answered yet: the previous text stays meanwhile.
            return 'pending'
        if post.generated_caption and post.generated_hashtags is not None:
            return 'ready'
        # A draft keeps being a draft when its text fails (process_video_seo's _fail_seo_post).
        if post.status in (ScheduledPost.Status.FAILED, ScheduledPost.Status.DRAFT) and _seo_error_code(post):
            return 'failed'
        if going_out:
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
        from .services.clip_media import thumbnail_url

        clip = post.video_clip
        return {
            'id': str(clip.id),
            'title': clip.title,
            'project_id': str(clip.project_id),
            'project_title': clip.project.title,
            'duration_seconds': max(clip.end_time - clip.start_time, 0.0),
            # Temporary, as the clip's own (services/clip_media.py); None without a thumbnail.
            'thumbnail_url': thumbnail_url(clip, self.context),
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


# --- Writes (gap 8: contract CreatePostInput, UpdatePostRequest) ---

TIKTOK_PRIVACY = ('PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY')
TIKTOK_FLAGS = ('allow_comment', 'allow_duet', 'allow_stitch', 'brand_organic', 'branded_content')
TIKTOK_PRIVACY_MISSING = "Elegí quién puede ver el video."
HASHTAG_MAX_CHARS = 100
_HASHTAG = re.compile(r'^\w+$')


def clean_tiktok(value):
    """
    TikTokOptions as the post stores it, or a 400 with one sentence under
    `tiktok` (a nested error would not reach the web app's field errors).
    The flags left out are False: they start unchecked (map E1.4).
    """
    if not isinstance(value, dict) or value.get('privacy_level') not in TIKTOK_PRIVACY:
        raise serializers.ValidationError(TIKTOK_PRIVACY_MISSING)
    options = {'privacy_level': value['privacy_level']}
    for flag in TIKTOK_FLAGS:
        flag_value = value.get(flag, False)
        if not isinstance(flag_value, bool):
            raise serializers.ValidationError(f"`{flag}` tiene que ser verdadero o falso.")
        options[flag] = flag_value
    return options


def clean_hashtags(values):
    """Without '#', in order and without repeats (ignoring case); each one letters, digits or '_'."""
    kept, seen = [], set()
    for value in values:
        tag = value.strip().lstrip('#')
        if not tag:
            continue
        if not _HASHTAG.match(tag):
            raise serializers.ValidationError(
                f"«{tag[:40]}» no es un hashtag válido: usá letras, números o guion bajo, sin espacios."
            )
        if len(tag) > HASHTAG_MAX_CHARS:
            raise serializers.ValidationError(f"Cada hashtag puede tener hasta {HASHTAG_MAX_CHARS} caracteres.")
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            kept.append(tag)
    return kept


class _PostTextFields(serializers.Serializer):
    caption = serializers.CharField(required=False, allow_blank=True)
    hashtags = serializers.ListField(child=serializers.CharField(allow_blank=True), required=False)
    tiktok = serializers.JSONField(required=False, error_messages={'null': TIKTOK_PRIVACY_MISSING})

    def validate_hashtags(self, value):
        return clean_hashtags(value)

    def validate_tiktok(self, value):
        return clean_tiktok(value)


class CreatePostSerializer(_PostTextFields):
    """One item of POST /scheduled-posts/ and /bulk/. The rules that read the database are in services."""

    video_clip = serializers.UUIDField(error_messages={
        'required': "Elegí el clip que querés publicar.", 'invalid': "El clip no existe.", 'null': "Elegí el clip que querés publicar.",
    })
    platform = serializers.ChoiceField(choices=ScheduledPost.Platform.choices, error_messages={
        'required': "Elegí la red.", 'invalid_choice': "Elegí TikTok, Instagram Reels o YouTube Shorts.",
    })
    publish_at = serializers.DateTimeField(required=False, allow_null=True, error_messages={
        'invalid': "La fecha no es válida.",
    })
    draft = serializers.BooleanField(required=False, default=False)


class UpdatePostSerializer(_PostTextFields):
    """PATCH /scheduled-posts/:id/: only what comes is changed."""

    publish_at = serializers.DateTimeField(required=False, allow_null=True, error_messages={
        'invalid': "La fecha no es válida.",
    })
