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

# What an upload may be (validate_source_file), with its size cap
# (MAX_UPLOAD_BYTES, apps.core.upload_limits). GET /api/v1/pricing/ gives both
# to the browser, which checks a file before sending it.
UPLOAD_EXTENSIONS = ('.mp4', '.mov', '.avi')

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


LOGO_NOT_AN_IMAGE = "El logo no es una imagen válida. Subí una imagen PNG."


def _brand_kit_file_cap(field, what):
    """The validator of a file of the kit: a 400 on the field over its cap (apps.core.upload_limits)."""

    def validate(self, value):
        from apps.core.upload_limits import BRAND_KIT_FILE_MAX_BYTES, FILE_TOO_LARGE, format_bytes

        limit = BRAND_KIT_FILE_MAX_BYTES[field]
        if value and getattr(value, 'size', 0) > limit:
            # The text of the web app (features/brand-kits/kit.ts · FILE_RULES).
            raise serializers.ValidationError(f"{what} pesa más de {format_bytes(limit)}.", code=FILE_TOO_LARGE)
        return value

    return validate


class BrandKitSerializer(serializers.ModelSerializer):
    serializer_field_mapping = _NOT_SENT_BOOLEANS

    class Meta:
        model = BrandKit
        fields = '__all__'
        read_only_fields = ('id', 'user', 'workspace')
        extra_kwargs = {
            # A file that is not an image (or a broken one): DRF's text is in English. In the
            # words of the other file errors of the kit («El logo pesa más de 2 MB.»).
            'watermark_logo': {'error_messages': {'invalid_image': LOGO_NOT_AN_IMAGE}},
        }

    validate_primary_font = _brand_kit_file_cap('primary_font', 'La fuente')
    validate_secondary_font = _brand_kit_file_cap('secondary_font', 'La fuente')
    validate_watermark_logo = _brand_kit_file_cap('watermark_logo', 'El logo')
    validate_intro_video = _brand_kit_file_cap('intro_video', 'El video')
    validate_outro_video = _brand_kit_file_cap('outro_video', 'El video')

class ClipLayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClipLayer
        fields = '__all__'
        read_only_fields = ('id', 'clip')

class VideoClipSerializer(serializers.ModelSerializer):
    layers = ClipLayerSerializer(many=True, read_only=True)
    # A paid render (a retry, a render from the editor, a rerender, an approval,
    # an auto-render) puts its clips in DRAFT until a worker takes them: this
    # tells such a draft from a suggestion.
    render_queued = serializers.SerializerMethodField(help_text=(
        "True for a draft clip that a paid render will take: its project is at render_dispatched and a "
        "reservation still RESERVED pays for the clip. False for a suggestion and for any other status."
    ))

    preview_url = serializers.SerializerMethodField(help_text=(
        "Temporary URL that plays the rendered file of a completed or published clip (a presigned GET "
        "that lasts CLIP_DOWNLOAD_URL_TTL_SECONDS; with local files, the absolute /media/ URL). Null for "
        "any other clip."
    ))
    thumbnail_url = serializers.SerializerMethodField(help_text=(
        "Temporary URL of the clip's thumbnail (a JPEG frame of its render), as preview_url. Null when "
        "the clip has none: never rendered, or the thumbnail could not be made."
    ))

    class Meta:
        model = VideoClip
        # The render claim is internal bookkeeping of render_clip_task; the
        # thumbnail's storage key is sent as thumbnail_url.
        exclude = ('render_claim_token', 'render_unfinished_starts', 'thumbnail_object_key')
        read_only_fields = ('id', 'project', 'status', 's3_object_key', 'virality_score', 'ai_reasoning')

    # Both URLs are signed locally from the row: no query and no network call per clip.
    def get_preview_url(self, clip) -> str | None:
        from .services.clip_media import preview_url

        return preview_url(clip, self.context)

    def get_thumbnail_url(self, clip) -> str | None:
        from .services.clip_media import thumbnail_url

        return thumbnail_url(clip, self.context)

    def get_render_queued(self, clip) -> bool:
        if clip.status != VideoClip.Status.DRAFT:
            return False
        from .tasks.ingestion import render_queued_rules

        # Read once for every clip this serializer sends: the projects of the
        # response (a page of the list) together, in one query at most however
        # many of them render; a clip of any other response, its own project.
        # ``clip.project`` is the project already loaded when the clips come
        # from ``project.clips``.
        rules = self.context.setdefault('_render_queued_rules', {})
        if clip.project_id not in rules:
            listed = self.root.instance
            listed = [
                project for project in listed
                if isinstance(project, VideoProject) and project.pk not in rules
            ] if isinstance(listed, (list, tuple, models.QuerySet)) else []
            if not any(project.pk == clip.project_id for project in listed):
                listed = [clip.project]
            rules.update(render_queued_rules(listed))
        return rules[clip.project_id](clip.pk)


# What a row of the projects list leaves out: only the project's own screens
# read them, and with a long video they are most of the weight of a project.
PROJECT_LIST_OMITTED_FIELDS = ('transcript_data', 'ai_rationale_log')
PROJECT_LIST_OMITTED_METADATA = ('full_text',)


class ProjectsQuerySerializer(serializers.Serializer):
    """Query of GET /projects/ (ProjectsQuery in the contract); the page is the paginator's."""

    status_group = serializers.ChoiceField(
        choices=('all', 'in_progress', 'to_review', 'ready', 'failed'), required=False,
        help_text="Filter of the screen: en proceso, para revisar, listos (completed and partial) or con error.",
    )
    search = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Part of the title, whatever the case. Plain text: % and _ match themselves.",
    )
    ordering = serializers.ChoiceField(choices=('-created_at', 'created_at', 'title'), required=False)


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
    thumbnail_url = serializers.SerializerMethodField(help_text=(
        "The thumbnail of the project's first clip that has one (the same temporary URL), or null."
    ))
    uploaded_by_detail = serializers.SerializerMethodField(help_text=(
        "Who created the project: {id, name}, never the email. Null when that user is gone."
    ))
    cost = serializers.SerializerMethodField(help_text=(
        "The creation reservation (metadata.reservation_id): {amount, state: reserved|charged|refunded, "
        "transaction_id}. A partial charge shows what was charged. Null without a creation reservation."
    ))
    eta_seconds = serializers.SerializerMethodField(help_text=(
        "While the project is processing: seconds until it is ready for the review (the estimate for its "
        "duration minus what its finished stages took). Null otherwise, for a whole-video edit and without "
        "history to estimate from."
    ))
    stage_timings = serializers.SerializerMethodField(help_text=(
        "{stage: {started_at, finished_at, duration_s}} of the stages that finished (their last successful "
        "run). started_at and finished_at are null for stages that finished before they were recorded. A stage "
        "that started and did not finish (running, or where the project failed) has started_at, a null "
        "duration_s, and finished_at only for a failed project (pipeline_error_at)."
    ))

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
            return CloudflareR2Manager.generate_presigned_url(
                obj.proxy_r2_key, expiration_seconds=3600, request=self.context.get('request'),
            )
        return None

    def get_thumbnail_url(self, obj) -> str | None:
        return None  # filled by to_representation, from the clips it already serialized

    def get_uploaded_by_detail(self, obj) -> dict | None:
        from .services.project_summary import uploaded_by_detail
        return uploaded_by_detail(obj)

    def get_cost(self, obj) -> dict | None:
        from .services.project_summary import response_cost
        # The reservations of every project of the response (a page of the
        # list) are read once, in one query; each project then looks up its own.
        return response_cost(obj, self.root.instance, self.context)

    def get_eta_seconds(self, obj) -> int | None:
        from .services.project_summary import eta_seconds
        return eta_seconds(obj, self.context)

    def get_stage_timings(self, obj) -> dict:
        from .services.project_summary import stage_timings
        return stage_timings(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if 'thumbnail_url' in data:
            # Read from the serialized clips, not from the rows: no query of its own.
            data['thumbnail_url'] = next(
                (clip['thumbnail_url'] for clip in data.get('clips') or () if clip.get('thumbnail_url')), None,
            )
        return data

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
            if not value.name.lower().endswith(UPLOAD_EXTENSIONS):
                raise serializers.ValidationError("Solo se permiten archivos de video (.mp4, .mov, .avi)")
            from apps.core.upload_limits import UploadTooLarge, max_upload_bytes

            # A 413 with its code, before ffprobe, the storage and the reservation. A
            # body that big is refused before it is read (UploadSizeLimitMiddleware):
            # this is a file just over the cap, within the room left for the form.
            if value.size > max_upload_bytes():
                raise UploadTooLarge()
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
        # (the view reserves tokens only after this returns). The rollback does
        # not reach the storage, so the upload is saved first and deleted here
        # by name, even when the INSERT itself fails.
        upload = self._store_upload(validated_data)
        try:
            with transaction.atomic():
                return self._create(validated_data)
        except BaseException:
            from .services.storage_service import delete_upload

            delete_upload(upload)
            raise

    @staticmethod
    def _store_upload(validated_data):
        """
        Saves the uploaded ``source_file`` to storage, as ``FileField.pre_save``
        would at the INSERT, and leaves its stored name in ``validated_data``.
        Returns that name, or None without an upload.
        """
        upload = validated_data.get('source_file')
        if not upload:
            return None
        field = VideoProject._meta.get_field('source_file')
        name = field.storage.save(
            field.generate_filename(None, upload.name), upload, max_length=field.max_length,
        )
        validated_data['source_file'] = name
        return name

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


class VideoProjectListSerializer(VideoProjectSerializer):
    """
    A row of GET /projects/: the project without the transcript
    (``transcript_data`` and ``metadata['full_text']``) and without
    ``ai_rationale_log``. The list is asked for again every 5 s while a
    project is processing, and no list reads them; GET /projects/{id}/ sends
    them. The view does not load those two columns either.
    """

    class Meta(VideoProjectSerializer.Meta):
        fields = None
        exclude = PROJECT_LIST_OMITTED_FIELDS

    def to_representation(self, instance):
        data = super().to_representation(instance)
        metadata = data.get('metadata')
        if isinstance(metadata, dict) and any(key in metadata for key in PROJECT_LIST_OMITTED_METADATA):
            data['metadata'] = {key: value for key, value in metadata.items() if key not in PROJECT_LIST_OMITTED_METADATA}
        return data
