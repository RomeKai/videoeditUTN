import uuid
from django.db import models
from django.conf import settings

# --- 1. BRAND KIT (La Identidad del Usuario) ---
# Aquí el usuario define su "yo digital".
class BrandKit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='brand_kits')
    name = models.CharField(max_length=100, default="Mi Marca Personal")
    
    # Identidad Visual
    primary_color = models.CharField(max_length=7, default="#FF0000", help_text="Color principal (Hex)")
    secondary_color = models.CharField(max_length=7, default="#FFFFFF", help_text="Color secundario (Hex)")
    font_family = models.CharField(max_length=50, default="Montserrat")
    
    # Assets Fijos (Lo que pediste: "saber que tiene tal imagen")
    logo = models.FileField(upload_to='brands/logos/', null=True, blank=True)
    intro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True, help_text="Intro genérica")
    outro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True, help_text="Outro genérica")
    
    # Personalidad de la IA
    ai_instructions = models.TextField(
        blank=True, 
        help_text="Instrucciones globales. Ej: 'Usa 'vos' en vez de 'tú', sé sarcástico'."
    )

    def __str__(self):
        return self.name


# --- 2. PROYECTO (El Contenedor) ---
class VideoProject(models.Model):
    # Tipos de flujo
    class Type(models.TextChoices):
        REPURPOSE = 'repurpose', 'Viralizar (Largo -> Cortos)'
        SINGLE_EDIT = 'single_edit', 'Edición (Clip -> Clip)'

    # Estilos de Edición (Tus Presets "Gaming", "Blog")
    class EditingStyle(models.TextChoices):
        DYNAMIC = 'dynamic', 'Dinámico / Gaming (Zooms, Cortes rápidos)'
        MINIMALIST = 'minimalist', 'Minimalista / Podcast (Sobrio)'
        VLOG = 'vlog', 'Vlog / Lifestyle'
        HORMOZI = 'hormozi', 'Estilo Hormozi (Subtítulos grandes)'
        CUSTOM = 'custom', 'Personalizado / Prompt Manual'

    class Status(models.TextChoices):
        PENDING = 'pending', 'En Cola'
        PROCESSING = 'processing', 'Analizando IA'
        READY = 'ready', 'Listo'
        FAILED = 'failed', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='projects')
    
    # Configuración de este proyecto
    title = models.CharField(max_length=255, default="Nuevo Proyecto")
    project_type = models.CharField(max_length=20, choices=Type.choices, default=Type.REPURPOSE)
    
    # MEZCLA: Estilo (Gaming) + Marca (Coca-Cola)
    editing_style = models.CharField(max_length=20, choices=EditingStyle.choices, default=EditingStyle.DYNAMIC)
    brand_kit = models.ForeignKey(BrandKit, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Archivo
    source_file = models.FileField(upload_to='videos/raw/%Y/%m/')
    
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.get_editing_style_display()})"


# --- 3. CLIP (El Resultado) ---
class VideoClip(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        RENDERING = 'rendering', 'Renderizando'
        COMPLETED = 'completed', 'Listo'
        PUBLISHED = 'published', 'Publicado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name='clips')
    
    title = models.CharField(max_length=255)
    output_file = models.FileField(upload_to='videos/clips/%Y/%m/', null=True, blank=True)
    
    start_time = models.FloatField()
    end_time = models.FloatField()
    
    virality_score = models.IntegerField(default=0)
    ai_reasoning = models.TextField(blank=True)
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


# --- 4. LAYERS (El Prompting y Edición Manual) ---
class ClipLayer(models.Model):
    class LayerType(models.TextChoices):
        IMAGE = 'image', 'Imagen'
        VIDEO = 'video', 'B-Roll'
        TEXT = 'text', 'Texto'
        SOUND = 'sound', 'Efecto de Sonido'
        SUBTITLE = 'subtitle', 'Línea de Subtítulo'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.ForeignKey(VideoClip, on_delete=models.CASCADE, related_name='layers')
    
    layer_type = models.CharField(max_length=20, choices=LayerType.choices)
    
    file = models.FileField(upload_to='assets/%Y/%m/', null=True, blank=True)
    text_content = models.CharField(max_length=255, blank=True)
    
    start_time = models.FloatField()
    duration = models.FloatField(default=2.0)
    
    # JSON para posición (x, y), rotación, escala, color específico de este layer
    position_data = models.JSONField(default=dict, blank=True)
    
    is_ai_generated = models.BooleanField(default=False)
    prompt_used = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.layer_type} @ {self.start_time}s"# videos models
