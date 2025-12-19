from rest_framework import serializers
from .models import VideoProject, VideoClip, ClipLayer, BrandKit

class BrandKitSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandKit
        fields = '__all__'
        read_only_fields = ('id', 'user', 'workspace') # Workspace se asigna en vista

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
    
    # Declaramos explícitamente para que no sean requeridos obligatoriamente
    source_file = serializers.FileField(required=False)
    video_url = serializers.URLField(required=False)

    class Meta:
        model = VideoProject
        fields = '__all__'
        # IMPORTANTÍSIMO: 'workspace' debe ser read_only para evitar el error de validación
        read_only_fields = ('id', 'workspace', 'uploaded_by', 'status', 'created_at', 'metadata', 'clips')

    def validate(self, data):
        """Valida que envíen archivo O url, pero no ambos vacíos."""
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