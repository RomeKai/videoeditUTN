import uuid
from django.db import models
from django.conf import settings

# --- 1. BRAND KIT ---
class BrandKit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='brand_kits')
    name = models.CharField(max_length=100, default="Mi Marca Personal")
    
    primary_color = models.CharField(max_length=7, default="#FF0000")
    secondary_color = models.CharField(max_length=7, default="#FFFFFF")
    font_family = models.CharField(max_length=50, default="Montserrat")
    
    logo = models.FileField(upload_to='brands/logos/', null=True, blank=True)
    intro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True)
    outro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True)
    
    ai_instructions = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} ({self.workspace_id})"


# --- 2. PROYECTO (Híbrido: URL o Archivo) ---
class VideoProject(models.Model):
    class Type(models.TextChoices):
        REPURPOSE = 'repurpose', 'Viralizar (Largo -> Cortos)'
        SINGLE_EDIT = 'single_edit', 'Edición (Clip -> Clip)'

    class EditingStyle(models.TextChoices):
        DYNAMIC = 'dynamic', 'Dinámico / Gaming'
        MINIMALIST = 'minimalist', 'Minimalista / Podcast'
        VLOG = 'vlog', 'Vlog / Lifestyle'
        HORMOZI = 'hormozi', 'Estilo Hormozi'
        CUSTOM = 'custom', 'Personalizado'

    class Status(models.TextChoices):
        PENDING = 'pending', 'En Cola'
        PROCESSING = 'processing', 'Analizando IA'
        READY = 'ready', 'Listo'
        FAILED = 'failed', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='projects')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='uploaded_videos')
    
    title = models.CharField(max_length=255, default="Nuevo Proyecto")
    project_type = models.CharField(max_length=20, choices=Type.choices, default=Type.REPURPOSE)
    editing_style = models.CharField(max_length=20, choices=EditingStyle.choices, default=EditingStyle.DYNAMIC)
    brand_kit = models.ForeignKey(BrandKit, on_delete=models.SET_NULL, null=True, blank=True)
    
    # --- CAMBIO CLAVE: Soportar URL o Archivo ---
    source_file = models.FileField(upload_to='videos/raw/%Y/%m/', null=True, blank=True)
    video_url = models.URLField(max_length=500, null=True, blank=True, help_text="URL de YouTube/Vimeo si no se sube archivo")
    
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

   

    class IntelligenceLevel(models.TextChoices):
        FAST = 'fast', 'Rápido (GPT-4o-mini)'       # Para usuarios Free / Pruebas
        SMART = 'smart', 'Inteligente (GPT-4o)'     # Para usuarios Pro / Viralidad Máxima


    intelligence_level = models.CharField(
        max_length=10,
        choices=IntelligenceLevel.choices,
        default=IntelligenceLevel.FAST
    )

    def __str__(self):
        return f"{self.title} ({self.workspace_id})"


# --- 3. CLIP ---
class VideoClip(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        RENDERING = 'rendering', 'Renderizando'
        COMPLETED = 'completed', 'Listo para descargar'
        PUBLISHED = 'published', 'Publicado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name='clips')
    
    title = models.CharField(max_length=255)
    output_file = models.FileField(upload_to='videos/clips/%Y/%m/', null=True, blank=True)
    
    start_time = models.FloatField()
    end_time = models.FloatField()
    
    virality_score = models.IntegerField(default=0)
    ai_reasoning = models.TextField(blank=True)
    ai_metadata = models.JSONField(default=dict, blank=True) 
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


# --- 4. LAYERS ---
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
    position_data = models.JSONField(default=dict, blank=True)
    is_ai_generated = models.BooleanField(default=False)
    prompt_used = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.layer_type} @ {self.start_time}s"