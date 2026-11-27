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

from apps.integrations.social.base import TIKTOK_FLAGS, TIKTOK_PRIVACY_LEVELS

from .models import ScheduledPost

# Codes process_video_seo writes to error_log when the generation fails.
SEO_ERROR_MESSAGES = {
    'seo_no_clip_transcript': "No pudimos generar el texto: el clip no tiene audio para transcribir. Escribilo vos.",
    'content_blocked': "No pudimos generar el texto de este clip. Escribilo vos.",
    'generated_content_blocked': "El texto generado no pasó la revisión. Escribilo vos.",
    'capacity_timeout': "La generación del texto tardó demasiado. Probá de nuevo en unos minutos.",
    'seo_rate_limited': "Este espacio pidió muchos textos con IA en la última hora. Escribilo vos, o regeneralo en un rato.",
}
SEO_ERROR_FALLBACK = "No pudimos generar el texto. Escribilo vos."
# Codes upload_to_social_network writes first in error_log ("<code>" or "<code> (<detail>)";
# apps/integrations/social/errors.py). `{network}` is the post's network. Any other failure of
# publishing is PUBLISH_ERROR_CODE, provider_not_configured included (ours to fix).
PUBLISH_ERROR_MESSAGES = {
    'account_not_connected': "La cuenta de {network} no está conectada. Conectala y reintentá.",
    'account_expired': "La cuenta de {network} venció. Reconectala y reintentá.",
    'quota_exceeded': "Se alcanzó el límite de publicaciones de este mes. Probá de nuevo más adelante.",
    'tiktok_options_missing': (
        "Faltó elegir quién puede ver el video en TikTok. Cancelá esta publicación y creala de nuevo."
    ),
    'duplicate_content': (
        "{network} ya tiene una publicación igual de los últimos dos días: puede que esta ya haya salido. "
        "Revisá la cuenta antes de reintentar."
    ),
    'provider_rejected': "{network} rechazó la publicación. Revisá el video y el texto, y reintentá.",
    'provider_unavailable': "{network} no respondió después de varios intentos. Probá de nuevo en unos minutos.",
    'outcome_unknown': (
        "No sabemos si se publicó: {network} no respondió a tiempo. Revisá la cuenta antes de reintentar."
    ),
}
# What the publisher wrote before the codes, in posts that failed then.
_LEGACY_PUBLISH_ERRORS = (
    ('Error: Ayrshare Profile Key not configured', 'account_not_connected'),
    ('Ayrshare did not answer in time', 'outcome_unknown'),
)
PUBLISH_ERROR_CODE = 'publish_failed'
PUBLISH_ERROR_MESSAGE = "No pudimos publicar el post. Probá de nuevo en unos minutos."


def _seo_error_code(post):
    """The SEO failure code the post carries, or None (an `error_log` can also hold publish text)."""
    log = post.error_log or ''
    if log in SEO_ERROR_MESSAGES or log.startswith(('seo_', 'moderation_')):
        return log
    return None


def _publish_error_code(post):
    """The code of a failure to publish: the first word of `error_log`, or PUBLISH_ERROR_CODE."""
    log = post.error_log or ''
    code = log.split(' ', 1)[0]
    if code in PUBLISH_ERROR_MESSAGES:
        return code
    for prefix, legacy in _LEGACY_PUBLISH_ERRORS:
        if log.startswith(prefix):
            return legacy
    return PUBLISH_ERROR_CODE


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
        # The same text the publisher sends (upload_to_social_network).
        return post.caption_to_publish()

    def get_hashtags(self, post) -> list[str]:
        return post.hashtags_to_publish()

    @extend_schema_field({'type': 'string', 'enum': ['none', 'pending', 'ready', 'failed']})
    def get_seo_status(self, post):
        from .services.scheduled_posts import seo_request_pending

        going_out = post.status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED)
        if going_out:
            # Its text is final: SEO never changes a post going out or out.
            return 'ready' if post.generated_caption and post.generated_hashtags is not None else 'none'
        if seo_request_pending(post):
            # Asked for (signals.queue_post_seo) and not answered yet: the previous text stays meanwhile.
            return 'pending'
        if post.generated_caption and post.generated_hashtags is not None:
            return 'ready'
        # A draft, or a post with the user's own text, keeps its status when its text fails
        # (process_video_seo's _fail_seo_post); a limited request leaves the code too.
        if _seo_error_code(post):
            return 'failed'
        return 'none'

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
        return _seo_error_code(post) or _publish_error_code(post)

    def get_error_message(self, post) -> str | None:
        code = self.get_error_code(post)
        if code is None:
            return None
        if code == PUBLISH_ERROR_CODE:
            return PUBLISH_ERROR_MESSAGE
        if code in PUBLISH_ERROR_MESSAGES:
            return PUBLISH_ERROR_MESSAGES[code].format(network=post.get_platform_display())
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

# The same values the publisher reads (apps.integrations.social.TikTokOptions).
TIKTOK_PRIVACY = TIKTOK_PRIVACY_LEVELS
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
