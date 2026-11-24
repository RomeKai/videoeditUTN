from rest_framework import serializers
from .models import VideoProject, VideoClip, ClipLayer, BrandKit
from .services.ai.selection_criteria import (
    MAX_SELECTION_CRITERIA_CHARS,
    normalize_selection_criteria,
)

class BrandKitSerializer(serializers.ModelSerializer):
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
        fields = '__all__'
        read_only_fields = ('id', 'project', 'status', 's3_object_key', 'virality_score', 'ai_reasoning')

class VideoProjectSerializer(serializers.ModelSerializer):
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
            'ai_rationale_log', 'transcript_data', 'approved_segments'
        )

    def get_proxy_url(self, obj):
        if obj.proxy_r2_key:
            from apps.videos.services.storage_service import CloudflareR2Manager
            return CloudflareR2Manager.generate_presigned_url(obj.proxy_r2_key, expiration_seconds=3600)
        return None

    def validate(self, data):
        # En caso de creaciÃ³n (POST) o si se estÃ¡n enviando estos campos especÃ­ficamente en un PATCH
        if not self.instance or 'source_file' in data or 'video_url' in data:
            file = data.get('source_file')
            url = data.get('video_url')
            
            # Si es creaciÃ³n, exigimos uno de los dos
            if not self.instance:
                if not file and not url:
                    raise serializers.ValidationError("Debes proporcionar un 'source_file' o una 'video_url'.")
            
            # En cualquier caso, no permitimos ambos
            if file and url:
                raise serializers.ValidationError("No puedes enviar archivo y URL al mismo tiempo.")
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
        return data

    def validate_aspect_ratio(self, value):
        from .services.ai.contracts import LEGACY_ASPECT_RATIOS

        return LEGACY_ASPECT_RATIOS.get(value, value)

    def validate_source_file(self, value):
        if value:
            if not value.name.lower().endswith(('.mp4', '.mov', '.avi')):
                raise serializers.ValidationError("Solo se permiten archivos de video (.mp4, .mov, .avi)")
            if value.size > 500 * 1024 * 1024:
                raise serializers.ValidationError("El archivo es demasiado grande (MÃ¡x 500MB)")
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
        instance = super().update(instance, validated_data)
        source_media = getattr(self, '_source_media', None)
        if source_media is not None and 'source_file' in validated_data:
            # A replaced file: never keep what was measured for the previous one.
            instance.metadata = {**(instance.metadata or {}), 'source_media': source_media.as_metadata()}
            instance.save(update_fields=['metadata'])
        return instance

    def create(self, validated_data):
        criteria = normalize_selection_criteria(validated_data.pop('selection_criteria', None))
        use_seo = validated_data.pop('use_seo', None)
        render_resolution = validated_data.pop('render_resolution', None)
        # Creamos el proyecto normal
        project = super().create(validated_data)
        
        # INYECCIÃ“N DINÃMICA: Atrapamos los campos crudos del CURL
        raw = self.initial_data
        project.metadata = project.metadata or {}
        
        if 'max_clips' in raw: project.metadata['max_clips'] = int(raw['max_clips'])
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
        if 'render_layout' in raw: project.render_layout = raw['render_layout']
        elif 'layout' in raw: project.render_layout = raw['layout']
        
        # New Layout Controls
        if 'gameplay_position' in raw: project.gameplay_position = raw['gameplay_position']
        if ('camera_selection_position' in raw): project.camera_selection_position = raw['camera_selection_position']
        if ('manual_camera_x' in raw): project.manual_camera_x = float(raw['manual_camera_x'])
        if ('manual_camera_y' in raw): project.manual_camera_y = float(raw['manual_camera_y'])
        if ('manual_camera_zoom' in raw): project.manual_camera_zoom = float(raw['manual_camera_zoom'])
        if 'speaker_tracking' in raw: 
            project.speaker_tracking = str(raw['speaker_tracking']).lower() in ['true', '1', 't', 'y', 'yes']
        
        if 'auto_render_bypass' in raw:
            project.auto_render_bypass = str(raw['auto_render_bypass']).lower() in ['true', '1', 't', 'y', 'yes']

        # Subtitle Controls
        if 'add_subtitles' in raw:
            val = raw['add_subtitles']
            project.add_subtitles = str(val).lower() in ['true', '1', 't', 'y', 'yes']
        
        if 'subtitle_color' in raw: project.subtitle_color = raw['subtitle_color']
        if 'subtitle_scale_factor' in raw: project.subtitle_scale_factor = float(raw['subtitle_scale_factor'])
        if 'subtitle_words_per_segment' in raw: project.subtitle_words_per_segment = int(raw['subtitle_words_per_segment'])
            
        # Face Tracking injection
        if 'use_facetracking' in raw:
            val = raw['use_facetracking']
            project.use_facetracking = str(val).lower() in ['true', '1', 't', 'y', 'yes']

        if 'remove_silences' in raw:
            project.remove_silences = str(raw['remove_silences']).lower() in ['true', '1', 't', 'y', 'yes']

        # Read by the pricing right after this save, then by the SEO dispatch.
        if use_seo is not None: project.metadata['use_seo'] = use_seo
        # What the upload validation measured (CORE-05): the pricing reads the duration.
        source_media = getattr(self, '_source_media', None)
        if source_media is not None: project.metadata['source_media'] = source_media.as_metadata()

        project.save()
        return project
