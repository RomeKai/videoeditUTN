import math

from django.db import models, transaction
from rest_framework import serializers
from rest_framework.fields import empty
from rest_framework.exceptions import APIException
from .models import VideoProject, VideoClip, ClipLayer, BrandKit
from .services.ai.selection_criteria import (
    MAX_SELECTION_CRITERIA_CHARS,
    normalize_selection_criteria,
)

# What a raw boolean of the creation request may be to mean true (the same
# strings DRF's BooleanField takes, 'on' included).
_RAW_TRUE = {'true', '1', 't', 'y', 'yes', 'on'}


def _raw_bool(value):
    return str(value).strip().lower() in _RAW_TRUE


def _raw_bool_sent(raw, name):
    """Whether the creation request sent a raw boolean: an empty one (a form field left blank) was not."""
    return name in raw and str(raw[name]).strip() != ''


# Raw numeric fields of the creation request that are not serializer fields.
# The column ones (manual_camera_x, subtitle_words_per_segment...) are parsed
# by DRF like any other field.
_RAW_NUMBERS = {'max_clips': int, 'subtitle_scale_factor': float}


def _raw_number(raw, name, cast):
    """
    A raw numeric field of the creation request: None when it was not sent or
    is empty (a form field left blank); a 400 on the field when it is not a
    finite number.
    """
    value = raw.get(name) if hasattr(raw, 'get') else None
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    error = serializers.ValidationError({
        name: ["Tiene que ser un número entero." if cast is int else "Tiene que ser un número."],
    })
    if isinstance(value, bool):
        raise error
    try:
        number = cast(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError, OverflowError):
        raise error
    if not math.isfinite(number):
        raise error
    return number


class _NotSentBooleanField(serializers.BooleanField):
    """A boolean that a multipart form leaving it out did not send (DRF reads it as false)."""

    default_empty_html = empty


# A multipart form that leaves a boolean out reads as false in DRF (an
# unchecked checkbox): a PUT without the field turned it off, and a new object
# never got a default of true. With _NotSentBooleanField for every model
# boolean, missing means not sent: a new object takes the model default and an
# update keeps what it had.
_NOT_SENT_BOOLEANS = {
    **serializers.ModelSerializer.serializer_field_mapping,
    models.BooleanField: _NotSentBooleanField,
}


class BrandKitSerializer(serializers.ModelSerializer):
    serializer_field_mapping = _NOT_SENT_BOOLEANS

    class Meta:
        model = BrandKit
        fields = '__all__'
        read_only_fields = ('id', 'user', 'workspace')

class ClipLayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClipLayer
        fields = '__all__'
        read_only_fields = ('id', 'clip')

class VideoClipSerializer(serializers.ModelSerializer):
    layers = ClipLayerSerializer(many=True, read_only=True)
    class Meta:
        model = VideoClip
        # The render claim is internal bookkeeping of render_clip_task.
        exclude = ('render_claim_token', 'render_unfinished_starts')
        read_only_fields = ('id', 'project', 'status', 's3_object_key', 'virality_score', 'ai_reasoning')

class ProjectRenderingError(APIException):
    status_code = 409
    default_detail = "El proyecto se está renderizando: esperá a que termine para cambiarlo."
    default_code = 'project_rendering'


class VideoProjectSerializer(serializers.ModelSerializer):
    # Booleans left out are not sent (_NOT_SENT_BOOLEANS): a project created
    # without add_subtitles gets subtitles, and from-prompt can leave one out
    # so the prompt decides (it only counts as explicit when sent).
    serializer_field_mapping = _NOT_SENT_BOOLEANS

    clips = VideoClipSerializer(many=True, read_only=True)
    brand_kit_name = serializers.CharField(source='brand_kit.name', read_only=True)
    proxy_url = serializers.SerializerMethodField()
    
    source_file = serializers.FileField(required=False)
    video_url = serializers.CharField(required=False, allow_blank=True)
    # PTE-02: optional preference for the clip selector ("momentos de humor").
    # Stored in metadata at creation; blank means none.
    selection_criteria = serializers.CharField(
        required=False, allow_blank=True, write_only=True,
        max_length=MAX_SELECTION_CRITERIA_CHARS,
    )
    # Paid add-on (PricingEngine 'seo_optimization'): captions and hashtags are
    # generated for each ScheduledPost of the project (ADR-013). Creation only.
    use_seo = serializers.BooleanField(required=False, write_only=True)
    # Output height tier. Priced at creation (PricingEngine resolution multiplier)
    # and checked against the plan's max_resolution; it cannot change afterwards.
    # Without it the project renders and is charged at 720p.
    render_resolution = serializers.ChoiceField(
        choices=['720p', '1080p', '4K'], required=False, write_only=True,
    )
    # '1:Post' was the stored value of the square ratio until migration 0009;
    # clients that still send it get '1:1'.
    aspect_ratio = serializers.ChoiceField(
        choices=[*VideoProject.AspectRatio.choices, ('1:Post', 'Cuadrado (Post), valor anterior')],
        required=False,
    )

    class Meta:
        model = VideoProject
        fields = '__all__'
        read_only_fields = (
            'id', 'workspace', 'uploaded_by', 'status', 'created_at', 'metadata', 'clips', 
            'original_r2_key', 'proxy_r2_key', 'final_export_r2_key', 
            'ai_rationale_log', 'transcript_data', 'approved_segments',
            # Written by the pipeline only: the charge of a whole-video edit
            # depends on where the project ended.
            'pipeline_stage', 'pipeline_stage_status', 'pipeline_attempts',
            'pipeline_error_code', 'pipeline_error_at',
        )

    def get_fields(self):
        fields = super().get_fields()
        # The views pass the active workspace: a project can only use a brand
        # kit of its own workspace.
        workspace = self.context.get('workspace')
        if workspace is not None and 'brand_kit' in fields:
            fields['brand_kit'].queryset = BrandKit.objects.filter(workspace=workspace)
        return fields

    def get_proxy_url(self, obj):
        if obj.proxy_r2_key:
            from apps.videos.services.storage_service import CloudflareR2Manager
            return CloudflareR2Manager.generate_presigned_url(obj.proxy_r2_key, expiration_seconds=3600)
        return None

    def validate(self, data):
        # En caso de creación (POST) o si se están enviando estos campos específicamente en un PATCH
        if not self.instance or 'source_file' in data or 'video_url' in data:
            file = data.get('source_file')
            url = data.get('video_url')
            
            # Si es creación, exigimos uno de los dos
            if not self.instance:
                if not file and not url:
                    raise serializers.ValidationError("Debes proporcionar un 'source_file' o una 'video_url'.")
            
            # En cualquier caso, no permitimos ambos
            if file and url:
                raise serializers.ValidationError("No puedes enviar archivo y URL al mismo tiempo.")
        if (
            self.instance is not None
            and self.instance.project_type == VideoProject.Type.SINGLE_EDIT
            and ('source_file' in data or 'video_url' in data)
        ):
            # The whole video was priced at creation and is rendered as is.
            raise serializers.ValidationError({
                'source_file': "El video de un proyecto de video completo no se puede cambiar. Creá un proyecto nuevo.",
            })
        if (
            not self.instance
            and data.get('project_type') == VideoProject.Type.SINGLE_EDIT
            and not data.get('source_file')
        ):
            # The whole video is rendered with no review. A link has a flat price
            # that does not know its duration, so it is never checked against the
            # plan's duration limit nor priced for what is rendered.
            raise serializers.ValidationError({
                'project_type': "Para editar el video completo, subí el archivo. Los links sirven para sacar clips.",
            })
        if not self.instance and data.get('project_type') == VideoProject.Type.SINGLE_EDIT:
            from django.conf import settings

            from .services.media_validation import _format_duration

            # One clip is the whole video: its subtitles and frames are held by a
            # single render task, so a whole-video edit is for short videos.
            limit = getattr(settings, 'AI_SINGLE_EDIT_MAX_DURATION_SECONDS', 600)
            source_media = getattr(self, '_source_media', None)
            if source_media is not None and source_media.duration_s > limit:
                raise serializers.ValidationError({
                    'project_type': f"Para editar el video completo, el video puede durar hasta {_format_duration(limit)}.",
                }, code='too_long')
        if not self.instance:
            self._layout_alias = self._validate_layout_alias(self.initial_data)
            self._raw_numbers = {
                name: _raw_number(self.initial_data, name, cast) for name, cast in _RAW_NUMBERS.items()
            }
        return data

    @staticmethod
    def _validate_layout_alias(raw):
        """
        ``layout`` is a raw alias of ``render_layout`` (not a serializer field).
        Empty (blank or null) counts as not sent; anything else must be a layout.
        """
        value = raw.get('layout') if hasattr(raw, 'get') else None
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, str) or value not in VideoProject.Layout.values:
            raise serializers.ValidationError(
                {'layout': f"Layout inválido. Opciones: {', '.join(VideoProject.Layout.values)}."}
            )
        return value

    def validate_video_url(self, value):
        # Only the sites the worker downloads from, never an internal address
        # (services/link_validation.py): what is saved here is fetched later.
        if not value:
            return value
        from .services.link_validation import LinkRejected, validate_video_link

        try:
            return validate_video_link(value).url
        except LinkRejected as exc:
            raise serializers.ValidationError(exc.message, code=exc.code)

    def validate_aspect_ratio(self, value):
        from .services.ai.contracts import LEGACY_ASPECT_RATIOS

        return LEGACY_ASPECT_RATIOS.get(value, value)

    def validate_source_file(self, value):
        if value:
            if not value.name.lower().endswith(('.mp4', '.mov', '.avi')):
                raise serializers.ValidationError("Solo se permiten archivos de video (.mp4, .mov, .avi)")
            if value.size > 500 * 1024 * 1024:
                raise serializers.ValidationError("El archivo es demasiado grande (Máx 500MB)")
            # CORE-05: the bytes must be a video the pipeline can process.
            from .services.media_validation import InvalidUploadError, validate_upload

            try:
                self._source_media = validate_upload(value)
            except InvalidUploadError as exc:
                raise serializers.ValidationError(exc.message, code=exc.code)
        return value

    def update(self, instance, validated_data):
        # The criterion only applies when the project is created (ingestion starts then).
        validated_data.pop('selection_criteria', None)
        validated_data.pop('use_seo', None)  # charged at creation only
        validated_data.pop('render_resolution', None)  # priced at creation only
        # The pipeline and the price depend on it: a link project turned into a
        # whole-video edit would render it all without paying for it.
        validated_data.pop('project_type', None)
        with transaction.atomic():
            # The row as it is now, locked: an approval or the pipeline may have
            # written it since this request read it. Saving the stale instance
            # whole put the project back to awaiting_approval and dropped the
            # link to the render reservation the approval had just made.
            locked = VideoProject.objects.select_for_update().get(pk=instance.pk)
            if locked.status == VideoProject.Status.RENDERING and set(validated_data) - {'title'}:
                # What the render reads must not change under it.
                raise ProjectRenderingError()
            for attr, value in validated_data.items():
                setattr(locked, attr, value)
            fields = list(validated_data)
            source_media = getattr(self, '_source_media', None)
            if source_media is not None and 'source_file' in validated_data:
                # A replaced file: never keep what was measured for the previous one.
                locked.metadata = {**(locked.metadata or {}), 'source_media': source_media.as_metadata()}
                fields.append('metadata')
            if fields:
                locked.save(update_fields=fields)
        return locked

    def create(self, validated_data):
        # One transaction: a failure after the INSERT leaves no project behind
        # (the view reserves tokens only after this returns).
        with transaction.atomic():
            return self._create(validated_data)

    def _create(self, validated_data):
        criteria = normalize_selection_criteria(validated_data.pop('selection_criteria', None))
        use_seo = validated_data.pop('use_seo', None)
        render_resolution = validated_data.pop('render_resolution', None)
        # Creamos el proyecto normal
        project = super().create(validated_data)
        
        # INYECCIÓN DINÁMICA: Atrapamos los campos crudos del CURL
        raw = self.initial_data
        raw_numbers = getattr(self, '_raw_numbers', {})
        project.metadata = project.metadata or {}
        
        if raw_numbers.get('max_clips') is not None: project.metadata['max_clips'] = raw_numbers['max_clips']
        if criteria: project.metadata['selection_criteria'] = criteria
        if render_resolution: project.metadata['render_resolution'] = render_resolution
        # Prompt-to-Edit (PTE-01b): parsed at the start of ingestion; what the
        # request sets explicitly wins over the parsed config.
        prompt = self.context.get('pte_prompt')
        if prompt is not None:
            from .services.ai.prompt_validator import explicit_config_fields

            project.metadata['pte_prompt'] = prompt
            project.metadata['pte_explicit'] = explicit_config_fields(raw)
        if 'subtitle_size' in raw: project.metadata['subtitle_size'] = raw['subtitle_size']
        layout_alias = getattr(self, '_layout_alias', None)
        if 'render_layout' in raw: project.render_layout = raw['render_layout']
        elif layout_alias: project.render_layout = layout_alias
        
        # New Layout Controls
        if 'gameplay_position' in raw: project.gameplay_position = raw['gameplay_position']
        if ('camera_selection_position' in raw): project.camera_selection_position = raw['camera_selection_position']
        # manual_camera_x, manual_camera_y and manual_camera_zoom come parsed by
        # DRF (validated_data); an empty one in a form is not sent.
        if _raw_bool_sent(raw, 'speaker_tracking'):
            project.speaker_tracking = _raw_bool(raw['speaker_tracking'])
        # Following the speaker is the 'active' layout: without a layout of
        # its own, a project that asks for it gets that layout.
        if project.speaker_tracking and 'render_layout' not in raw and not layout_alias:
            project.render_layout = VideoProject.Layout.ACTIVE
            # That layout came from the request: a prompt layout must not replace it.
            if 'pte_explicit' in project.metadata:
                project.metadata['pte_explicit'] = sorted({*project.metadata['pte_explicit'], 'render_layout'})

        if _raw_bool_sent(raw, 'auto_render_bypass'):
            project.auto_render_bypass = _raw_bool(raw['auto_render_bypass'])

        # Subtitle Controls
        if _raw_bool_sent(raw, 'add_subtitles'):
            project.add_subtitles = _raw_bool(raw['add_subtitles'])
        
        if 'subtitle_color' in raw: project.subtitle_color = raw['subtitle_color']
        if raw_numbers.get('subtitle_scale_factor') is not None:
            project.subtitle_scale_factor = raw_numbers['subtitle_scale_factor']
        # subtitle_words_per_segment comes parsed by DRF, like the camera.
            
        # Face Tracking injection
        if _raw_bool_sent(raw, 'use_facetracking'):
            project.use_facetracking = _raw_bool(raw['use_facetracking'])

        if _raw_bool_sent(raw, 'remove_silences'):
            project.remove_silences = _raw_bool(raw['remove_silences'])

        # Read by the pricing right after this save, then by the SEO dispatch.
        if use_seo is not None: project.metadata['use_seo'] = use_seo
        # What the upload validation measured (CORE-05): the pricing reads the duration.
        source_media = getattr(self, '_source_media', None)
        if source_media is not None: project.metadata['source_media'] = source_media.as_metadata()

        project.save()
        return project
