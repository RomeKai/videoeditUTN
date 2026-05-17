from rest_framework import serializers
from .models import VideoProject, VideoClip, ClipLayer, BrandKit

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
        read_only_fields = ('id', 'project', 'status', 'output_file', 'virality_score', 'ai_reasoning')

class VideoProjectSerializer(serializers.ModelSerializer):
    clips = VideoClipSerializer(many=True, read_only=True)
    brand_kit_name = serializers.CharField(source='brand_kit.name', read_only=True)
    proxy_url = serializers.SerializerMethodField()
    
    source_file = serializers.FileField(required=False)
    video_url = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = VideoProject
        fields = '__all__'
        read_only_fields = ('id', 'workspace', 'uploaded_by', 'status', 'created_at', 'metadata', 'clips', 'original_s3_key', 'proxy_s3_key', 'ai_rationale_log')

    def get_proxy_url(self, obj):
        if obj.proxy_s3_key:
            from apps.videos.services.s3_service import S3StorageManager
            return S3StorageManager.generate_presigned_url(obj.proxy_s3_key, expiration_seconds=3600)
        return None

    def validate(self, data):
        file = data.get('source_file')
        url = data.get('video_url')
        if not file and not url:
            raise serializers.ValidationError("Debes proporcionar un 'source_file' o una 'video_url'.")
        if file and url:
            raise serializers.ValidationError("No puedes enviar archivo y URL al mismo tiempo.")
        return data

    def validate_source_file(self, value):
        if value:
            if not value.name.lower().endswith(('.mp4', '.mov', '.avi')):
                raise serializers.ValidationError("Solo se permiten archivos de video (.mp4, .mov, .avi)")
            if value.size > 500 * 1024 * 1024:
                raise serializers.ValidationError("El archivo es demasiado grande (Máx 500MB)")
        return value

    def create(self, validated_data):
        # Creamos el proyecto normal
        project = super().create(validated_data)
        
        # INYECCIÓN DINÁMICA: Atrapamos los campos crudos del CURL
        raw = self.initial_data
        project.metadata = project.metadata or {}
        
        if 'max_clips' in raw: project.metadata['max_clips'] = int(raw['max_clips'])
        if 'subtitle_size' in raw: project.metadata['subtitle_size'] = raw['subtitle_size']
        if 'aspect_ratio' in raw: project.aspect_ratio = raw['aspect_ratio']
        if 'render_layout' in raw: project.render_layout = raw['render_layout']
        elif 'layout' in raw: project.render_layout = raw['layout']
        
        # New Layout Controls
        if 'gameplay_position' in raw: project.gameplay_position = raw['gameplay_position']
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
            
        # Inyección de FaceTracking
        if 'use_facetracking' in raw:
            val = raw['use_facetracking']
            project.use_facetracking = str(val).lower() in ['true', '1', 't', 'y', 'yes']
            
        project.save()
        return project
