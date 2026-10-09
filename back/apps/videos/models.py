import uuid
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

# --- 1. BRAND KIT ---
class BrandKit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='brand_kits')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='brand_kits', null=True, blank=True)
    
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    
    # Fuentes y Colores
    primary_font = models.FileField(upload_to='assets/fonts/', null=True, blank=True)
    secondary_font = models.FileField(upload_to='assets/fonts/', null=True, blank=True)
    
    primary_color = models.CharField(max_length=7, default="#FFFFFF") # Hex
    secondary_color = models.CharField(max_length=7, default="#000000")
    accent_color = models.CharField(max_length=7, default="#FFFF00")

    # Assets GrÃ¡ficos
    watermark_logo = models.ImageField(upload_to='assets/logos/', null=True, blank=True)
    intro_video = models.FileField(upload_to='assets/videos/', null=True, blank=True)
    outro_video = models.FileField(upload_to='assets/videos/', null=True, blank=True)
    
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.workspace.name})"


# --- 2. PROYECTO (Core) ---
class VideoProject(models.Model):
    # --- Enums de ConfiguraciÃ³n ---
    class Type(models.TextChoices):
        REPURPOSE = 'repurpose', 'Viralizar (Largo -> Cortos)'
        SINGLE_EDIT = 'single_edit', 'EdiciÃ³n (Clip -> Clip)'

    class EditingStyle(models.TextChoices):
        DYNAMIC = 'dynamic', 'DinÃ¡mico / Gaming'
        MINIMALIST = 'minimalist', 'Minimalista / Podcast'
        VLOG = 'vlog', 'Vlog / Lifestyle'
        HORMOZI = 'hormozi', 'Estilo Hormozi'
        CUSTOM = 'custom', 'Personalizado'

    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Subido'
        INGESTING = 'ingesting', 'Ingesta / Analizando IA'
        AWAITING_APPROVAL = 'awaiting_approval', 'Esperando AprobaciÃ³n'
        RENDERING = 'rendering', 'Renderizando'
        COMPLETED = 'completed', 'Completado'
        PARTIAL = 'partial', 'Completado con errores'
        FAILED = 'failed', 'Error'

    class PipelineStage(models.TextChoices):
        """
        Last stage the ingestion pipeline finished (see services/ai/pipeline_state.py).
        Kept apart from ``Status`` (user-facing) so retries can resume precisely.
        """
        UPLOADED = 'uploaded', 'Subido'
        AUDIO_EXTRACTED = 'audio_extracted', 'Fuente preparada'
        TRANSCRIBED = 'transcribed', 'Transcrito'
        CLIPS_SELECTED = 'clips_selected', 'Clips seleccionados'
        RENDER_DISPATCHED = 'render_dispatched', 'Render despachado'
        COMPLETED = 'completed', 'Completado'
        PARTIAL = 'partial', 'Completado con errores'
        FAILED = 'failed', 'Error'

    class IntelligenceLevel(models.TextChoices):
        FAST = 'fast', 'RÃ¡pido (GPT-4o-mini)'
        SMART = 'smart', 'Inteligente (GPT-4o)'

    class AspectRatio(models.TextChoices):
        PORTRAIT_9_16 = '9:16', 'Vertical (TikTok/Reels)'
        SQUARE_1_1 = '1:Post', 'Cuadrado (Post)'
        LANDSCAPE_16_9 = '16:9', 'Horizontal (YouTube)'

    class Layout(models.TextChoices):
        FILL = 'fill', 'Relleno (Crop Central)'
        FIT = 'fit', 'Ajustar (Bordes Negros)'
        BLURRED = 'blurred', 'Fondo Borroso'
        SPLIT = 'split', 'Split Screen (Gaming)'
        PIP = 'pip', 'Picture in Picture'
        VERSUS = 'versus', 'Versus (2 Personas)'
        ACTIVE = 'active', 'Speaker DinÃ¡mico'

    class CameraSelectionPosition(models.TextChoices):
        LEFT = 'left', 'Izquierda'
        CENTER = 'center', 'Centro'
        RIGHT = 'right', 'Derecha'

    class GameplayPosition(models.TextChoices):
        LEFT = 'left', 'Izquierda'
        CENTER = 'center', 'Centro'
        RIGHT = 'right', 'Derecha'

    # --- Campos del Modelo ---
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='projects')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='uploaded_videos')
    
    title = models.CharField(max_length=255, default="Nuevo Proyecto")
    
    # Almacenamiento Cloudflare R2
    original_r2_key = models.CharField(max_length=1024, blank=True, null=True, verbose_name="Original R2 Key")
    proxy_r2_key = models.CharField(max_length=1024, blank=True, null=True, verbose_name="Proxy R2 Key")
    final_export_r2_key = models.CharField(max_length=1024, blank=True, null=True, verbose_name="Final Export R2 Key")

    # ConfiguraciÃ³n General
    project_type = models.CharField(max_length=20, choices=Type.choices, default=Type.REPURPOSE)
    editing_style = models.CharField(max_length=20, choices=EditingStyle.choices, default=EditingStyle.DYNAMIC)
    intelligence_level = models.CharField(max_length=10, choices=IntelligenceLevel.choices, default=IntelligenceLevel.FAST)
    auto_render_bypass = models.BooleanField(default=False, help_text="Si es True, salta el paso de aprobaciÃ³n y renderiza directo.")
    brand_kit = models.ForeignKey(BrandKit, on_delete=models.SET_NULL, null=True, blank=True)
    
    # ConfiguraciÃ³n de Renderizado
    aspect_ratio = models.CharField(max_length=10, choices=AspectRatio.choices, default=AspectRatio.PORTRAIT_9_16)
    render_layout = models.CharField(max_length=10, choices=Layout.choices, default=Layout.FILL)
    gameplay_position = models.CharField(max_length=10, choices=GameplayPosition.choices, default=GameplayPosition.CENTER)
    camera_selection_position = models.CharField(max_length=10, choices=CameraSelectionPosition.choices, default=CameraSelectionPosition.CENTER)
    # Precise Manual Crop (Normalized 0.0 - 1.0)
    manual_camera_x = models.FloatField(default=0.5)
    manual_camera_y = models.FloatField(default=0.2)
    manual_camera_zoom = models.FloatField(default=1.0)
    speaker_tracking = models.BooleanField(default=False)
    
    # SubtÃ­tulos Pro
    class SubtitleSize(models.TextChoices):
        SMALL = 'small', 'PequeÃ±o'
        MEDIUM = 'medium', 'Mediano'
        LARGE = 'large', 'Grande'

    class SubtitlePosition(models.TextChoices):
        TOP = 'top', 'Arriba'
        CENTER = 'center', 'Centro'
        BOTTOM = 'bottom', 'Abajo'

    add_subtitles = models.BooleanField(default=True, help_text="Â¿Deseas agregar subtÃ­tulos automÃ¡ticos?")
    subtitle_color = models.CharField(max_length=7, default="#FFFF00", help_text="Color hexadecimal")
    subtitle_with_emojis = models.BooleanField(default=False, help_text="Â¿Agregar emojis automÃ¡ticos?")
    subtitle_words_per_segment = models.IntegerField(default=3, help_text="MÃ¡ximo de palabras")
    subtitle_size = models.CharField(max_length=10, choices=SubtitleSize.choices, default=SubtitleSize.MEDIUM)
    subtitle_position = models.CharField(max_length=10, choices=SubtitlePosition.choices, default=SubtitlePosition.BOTTOM)

    # Opciones de IA en Renderizado
    use_facetracking = models.BooleanField(default=False, help_text="Â¿Deseas que la cÃ¡mara siga automÃ¡ticamente el rostro?")
    remove_silences = models.BooleanField(default=False, help_text='¿Deseas eliminar los silencios automáticamente para hacer el video más dinámico?')

    # Archivos Fuente (Opcional si ya estÃ¡ en R2)
    source_file = models.FileField(upload_to='videos/raw/%Y/%m/', null=True, blank=True)
    video_url = models.URLField(max_length=500, null=True, blank=True, help_text="URL de YouTube/Vimeo si no se sube archivo")
    
    # Metadatos IA y Paper Edit (Sprint 1 Specs)
    metadata = models.JSONField(default=dict, blank=True)
    ai_rationale_log = models.JSONField(default=dict, blank=True, verbose_name="AI Rationale")
    transcript_data = models.JSONField(null=True, blank=True, verbose_name="Transcript Data (JSON)")
    approved_segments = models.JSONField(null=True, blank=True, verbose_name="Approved Segments (Floats)")
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)

    # Recoverable ingestion pipeline state (AICORE-7). Dedicated columns instead of
    # ``metadata`` so concurrent metadata writers cannot clobber it.
    pipeline_stage = models.CharField(max_length=32, choices=PipelineStage.choices, default=PipelineStage.UPLOADED)
    pipeline_stage_status = models.JSONField(default=dict, blank=True, help_text="Per-stage pending/running/completed/failed.")
    pipeline_attempts = models.PositiveIntegerField(default=0)
    pipeline_error_code = models.CharField(max_length=100, blank=True, default="")
    pipeline_error_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.workspace.name})"


# --- 3. CLIP ---
class VideoClip(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        RENDERING = 'rendering', 'Renderizando'
        COMPLETED = 'completed', 'Listo para descargar'
        PUBLISHED = 'published', 'Publicado'
        FAILED = 'failed', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name='clips')
    
    title = models.CharField(max_length=255)
    # Mantener s3_object_key por compatibilidad con r2_key
    s3_object_key = models.CharField(max_length=1024, blank=True, null=True, verbose_name="R2/S3 Key")
    
    start_time = models.FloatField()
    end_time = models.FloatField()
    
    virality_score = models.IntegerField(default=0)
    ai_reasoning = models.TextField(blank=True)
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} @ {self.start_time}s"


# --- 4. CAPA / OVERLAY ---
class ClipLayer(models.Model):
    class LayerType(models.TextChoices):
        TEXT = 'text', 'Texto / SubtÃ­tulo'
        IMAGE = 'image', 'Imagen / Logo'
        VIDEO = 'video', 'Video Overlay'
        AUDIO = 'audio', 'Audio / MÃºsica'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.ForeignKey(VideoClip, on_delete=models.CASCADE, related_name='layers')
    
    layer_type = models.CharField(max_length=20, choices=LayerType.choices)
    start_time = models.FloatField(default=0.0)
    end_time = models.FloatField(null=True, blank=True)
    
    content = models.TextField(blank=True) 
    asset_file = models.FileField(upload_to='assets/layers/', null=True, blank=True)
    
    config = models.JSONField(default=dict, blank=True)
    is_ai_generated = models.BooleanField(default=False)
    prompt_used = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.layer_type} @ {self.start_time}s"


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


class AIUsageRecord(models.Model):
    """
    One row per AI call attempt (AICORE-8): source of the pilot metrics export.

    Privacy by construction: no free-text column exists. ``error_code`` is the
    CLASS NAME of the AIError (never ``str(exc)``) and ``resolved_model`` is a
    model name reported by the provider, so a title, transcript, prompt or
    provider message can never reach this table.
    """

    class Stage(models.TextChoices):
        TRANSCRIPTION = 'transcription', 'Transcription'
        SELECTION = 'selection', 'Selection'

    class PipelineVersion(models.TextChoices):
        LEGACY = 'legacy', 'Legacy'
        V2 = 'v2', 'AI Core V2'

    class Role(models.TextChoices):
        PRIMARY = 'primary', 'Primary'
        FALLBACK = 'fallback', 'Fallback'

    project = models.ForeignKey(
        VideoProject, on_delete=models.CASCADE, related_name='ai_usage_records', db_index=True
    )
    stage = models.CharField(max_length=16, choices=Stage.choices)
    pipeline_version = models.CharField(max_length=8, choices=PipelineVersion.choices)
    attempt = models.PositiveSmallIntegerField(help_text="VideoProject.pipeline_attempts when recorded.")
    role = models.CharField(max_length=8, choices=Role.choices)

    provider = models.CharField(max_length=64)
    model = models.CharField(max_length=64)
    resolved_model = models.CharField(max_length=64, null=True, blank=True)

    success = models.BooleanField()
    error_code = models.CharField(max_length=64, null=True, blank=True)

    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    audio_seconds = models.FloatField(null=True, blank=True)
    latency_seconds = models.FloatField()
    cached = models.BooleanField(default=False)

    # 9 decimals (the plan said 6): LLM costs per call are often below 1e-6 USD
    # and pricing.py rounds to 9 places, so 6 would truncate them to zero.
    estimated_cost_usd = models.DecimalField(max_digits=14, decimal_places=9, null=True, blank=True)
    pricing_version = models.CharField(max_length=16, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            # A Celery redelivery of the same task updates the row instead of duplicating it.
            models.UniqueConstraint(
                fields=['project', 'stage', 'attempt', 'role'],
                name='uniq_ai_usage_project_stage_attempt_role',
            ),
        ]
        indexes = [
            models.Index(fields=['created_at', 'project'], name='ai_usage_created_project_idx'),
        ]

    def __str__(self):
        return f"{self.stage}/{self.role} attempt={self.attempt} ({self.project_id})"
