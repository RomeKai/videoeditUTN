import uuid
from django.db import models
from django.conf import settings

# --- 1. BRAND KIT ---
class BrandKit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='brand_kits')
    name = models.CharField(max_length=100, default="Mi Marca Personal")
    description = models.TextField(
        blank=True, 
        null=True, 
        help_text="Detailed notes about the brand identity, tone of voice, and specific stylistic guidelines."
    )
    
    primary_color = models.CharField(max_length=7, default="#FF0000")
    secondary_color = models.CharField(max_length=7, default="#FFFFFF")
    font_family = models.CharField(max_length=50, default="Montserrat")
    
    logo = models.FileField(upload_to='brands/logos/', null=True, blank=True)
    intro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True)
    outro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True)
    
    ai_instructions = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} ({self.workspace_id})"


# --- 2. PROYECTO (Core) ---
class VideoProject(models.Model):
    # --- Enums de Configuración ---
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

    class IntelligenceLevel(models.TextChoices):
        FAST = 'fast', 'Rápido (GPT-4o-mini)'       # Para usuarios Free / Pruebas
        SMART = 'smart', 'Inteligente (GPT-4o)'     # Para usuarios Pro / Viralidad Máxima

    # --- NUEVOS ENUMS DE RENDERIZADO ---
    class AspectRatio(models.TextChoices):
        PORTRAIT_9_16 = '9:16', 'Vertical (TikTok/Reels)'
        SQUARE_1_1 = '1:1', 'Cuadrado (Post)'
        LANDSCAPE_16_9 = '16:9', 'Horizontal (YouTube)'

    class Layout(models.TextChoices):
        FILL = 'fill', 'Relleno (Crop Central)'       # Clásico
        FIT = 'fit', 'Ajustar (Bordes Negros)'        # Video completo pequeño
        BLURRED = 'blurred', 'Fondo Borroso'          # Estilo moderno
        SPLIT = 'split', 'Split Screen (Gaming)'      # Arriba/Abajo
        PIP = 'pip', 'Picture in Picture'             # Gamer en esquina
        VERSUS = 'versus', 'Versus (2 Personas)'      # Cara a Cara
        ACTIVE = 'active', 'Speaker Dinámico'         # Seguir al que habla

    class GameplayPosition(models.TextChoices):
        LEFT = 'left', 'Izquierda'
        CENTER = 'center', 'Centro'
        RIGHT = 'right', 'Derecha'

    # --- Campos del Modelo ---
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='projects')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='uploaded_videos')
    
    title = models.CharField(max_length=255, default="Nuevo Proyecto")
    
    # Configuración General
    project_type = models.CharField(max_length=20, choices=Type.choices, default=Type.REPURPOSE)
    editing_style = models.CharField(max_length=20, choices=EditingStyle.choices, default=EditingStyle.DYNAMIC)
    intelligence_level = models.CharField(max_length=10, choices=IntelligenceLevel.choices, default=IntelligenceLevel.FAST)
    brand_kit = models.ForeignKey(BrandKit, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Configuración de Renderizado (NUEVOS)
    aspect_ratio = models.CharField(max_length=10, choices=AspectRatio.choices, default=AspectRatio.PORTRAIT_9_16)
    render_layout = models.CharField(max_length=10, choices=Layout.choices, default=Layout.FILL)
    gameplay_position = models.CharField(max_length=10, choices=GameplayPosition.choices, default=GameplayPosition.CENTER)
    speaker_tracking = models.BooleanField(default=False) # NUEVO: Switch para seguir al que habla
    
    # Subtítulos Pro
    class SubtitleSize(models.TextChoices):
        SMALL = 'small', 'Pequeño'
        MEDIUM = 'medium', 'Mediano'
        LARGE = 'large', 'Grande'

    class SubtitlePosition(models.TextChoices):
        TOP = 'top', 'Arriba'
        CENTER = 'center', 'Centro'
        BOTTOM = 'bottom', 'Abajo'

    add_subtitles = models.BooleanField(default=True, help_text="¿Deseas agregar subtítulos automáticos?")
    subtitle_color = models.CharField(max_length=7, default="#FFFF00", help_text="Color hexadecimal")
    subtitle_with_emojis = models.BooleanField(default=False, help_text="¿Agregar emojis automáticos?")
    subtitle_words_per_segment = models.IntegerField(default=3, help_text="Máximo de palabras")
    subtitle_size = models.CharField(max_length=10, choices=SubtitleSize.choices, default=SubtitleSize.MEDIUM)
    subtitle_position = models.CharField(max_length=10, choices=SubtitlePosition.choices, default=SubtitlePosition.BOTTOM)

    # Opciones de IA en Renderizado
    use_facetracking = models.BooleanField(default=False, help_text="¿Deseas que la cámara siga automáticamente el rostro?")

    # Archivos Fuente
    source_file = models.FileField(upload_to='videos/raw/%Y/%m/', null=True, blank=True)
    video_url = models.URLField(max_length=500, null=True, blank=True, help_text="URL de YouTube/Vimeo si no se sube archivo")
    
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

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
    s3_object_key = models.CharField(max_length=1024, blank=True, null=True, verbose_name="S3 Key")
    
    start_time = models.FloatField()
    end_time = models.FloatField()
    
    virality_score = models.IntegerField(default=0)
    ai_reasoning = models.TextField(blank=True)
    ai_metadata = models.JSONField(default=dict, blank=True) 
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class ScheduledPost(models.Model):
    """
    State Machine for social media distribution.
    Orchestrates when and where a video clip is published.
    """
    class Platform(models.TextChoices):
        TIKTOK = 'TIKTOK', 'TikTok'
        INSTAGRAM_REELS = 'INSTAGRAM_REELS', 'Instagram Reels'
        YOUTUBE_SHORTS = 'YOUTUBE_SHORTS', 'YouTube Shorts'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Borrador'
        SCHEDULED = 'SCHEDULED', 'Programado'
        QUEUED = 'QUEUED', 'En Cola (ETA)'
        PROCESSING = 'PROCESSING', 'Publicando...'
        PUBLISHED = 'PUBLISHED', 'Publicado'
        FAILED = 'FAILED', 'Fallido'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video_clip = models.ForeignKey(VideoClip, on_delete=models.CASCADE, related_name='scheduled_posts')
    
    platform = models.CharField(max_length=20, choices=Platform.choices)
    publish_at = models.DateTimeField(verbose_name="Publish Date (UTC)")
    
    status = models.CharField(
        max_length=20, 
        choices=Status.choices, 
        default=Status.DRAFT
    )
    
    error_log = models.TextField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    
    # AI SEO Metadata (Generated)
    generated_caption = models.TextField(null=True, blank=True, verbose_name="AI Caption")
    generated_hashtags = models.JSONField(null=True, blank=True, verbose_name="AI Hashtags")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['publish_at']
        indexes = [
            models.Index(fields=['status', 'publish_at']),
        ]

    def __str__(self):
        return f"{self.platform} @ {self.publish_at}"


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