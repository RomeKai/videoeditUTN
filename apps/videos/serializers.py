from rest_framework import serializers
from .models import VideoProject, VideoClip, ClipLayer, BrandKit

# --- 1. BRAND KIT SERIALIZER ---
class BrandKitSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandKit
        fields = '__all__'
        # El usuario no se envía en el JSON, lo sacamos del token (seguridad)
        read_only_fields = ('id', 'user')


# --- 2. CLIP LAYER (Detalles de edición) ---
class ClipLayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClipLayer
        fields = '__all__'
        read_only_fields = ('id', 'clip')


# --- 3. VIDEO CLIP (El resultado) ---
class VideoClipSerializer(serializers.ModelSerializer):
    # Incluimos los layers (imágenes, textos) dentro del clip
    layers = ClipLayerSerializer(many=True, read_only=True)

    class Meta:
        model = VideoClip
        fields = '__all__'
        read_only_fields = ('id', 'project', 'status', 'output_file', 'virality_score', 'ai_reasoning')


# --- 4. VIDEO PROJECT (El Padre) ---
class VideoProjectSerializer(serializers.ModelSerializer):
    # Nested Serializer: Cuando pidas un proyecto, verás sus clips automáticamente
    clips = VideoClipSerializer(many=True, read_only=True)
    
    # Campo extra para mostrar el nombre del BrandKit en lugar de solo el ID
    brand_kit_name = serializers.CharField(source='brand_kit.name', read_only=True)

    class Meta:
        model = VideoProject
        fields = '__all__'
        # Campos que el usuario NO puede modificar manualmente
        read_only_fields = ('id', 'user', 'status', 'created_at', 'metadata', 'clips')

    # Validación Estricta: Ejemplo de validación personalizada
    def validate_source_file(self, value):
        # Aquí validaremos que sea un video real y no un PDF o EXE
        if not value.name.lower().endswith(('.mp4', '.mov', '.avi')):
            raise serializers.ValidationError("Solo se permiten archivos de video (.mp4, .mov, .avi)")
        # Aquí podrías validar tamaño máximo (ej: 500MB)
        if value.size > 500 * 1024 * 1024:
            raise serializers.ValidationError("El archivo es demasiado grande (Máx 500MB)")
        return value