import uuid
from django.db import models
from django.conf import settings

# --- 1. BRAND KIT (Identidad de Marca) ---
class BrandKit(models.Model):
    """
    Define la identidad visual (logos, colores, fuentes).
    Pertenece a un Workspace (Agencia), accesible por todos sus editores.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # RELACIÓN SAAS: Pertenece al Espacio de Trabajo
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='brand_kits')
    
    name = models.CharField(max_length=100, default="Mi Marca Personal")
    
    # Identidad Visual
    primary_color = models.CharField(max_length=7, default="#FF0000", help_text="Color principal (Hex)")
    secondary_color = models.CharField(max_length=7, default="#FFFFFF", help_text="Color secundario (Hex)")
    font_family = models.CharField(max_length=50, default="Montserrat")
    
    # Assets Fijos
    logo = models.FileField(upload_to='brands/logos/', null=True, blank=True)
    intro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True, help_text="Intro genérica")
    outro_video = models.FileField(upload_to='brands/assets/', null=True, blank=True, help_text="Outro genérica")
    
    # Personalidad de la IA
    ai_instructions = models.TextField(
        blank=True, 
        help_text="Instrucciones globales. Ej: 'Usa 'vos' en vez de 'tú', sé sarcástico'."
    )

    def __str__(self):
        return f"{self.name} (Workspace: {self.workspace_id})"


# --- 2. PROYECTO (El Contenedor) ---
class VideoProject(models.Model):
    """
    El video "padre" (raw) que se sube.
    Pertenece al Workspace (quien paga), pero registramos quién lo subió.
    """
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
    
    # RELACIÓN SAAS CRÍTICA
    workspace = models.ForeignKey('users.Workspace', on_delete=models.CASCADE, related_name='projects')
    
    # AUDITORÍA: ¿Qué usuario del equipo subió esto?
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='uploaded_videos')
    
    title = models.CharField(max_length=255, default="Nuevo Proyecto")
    project_type = models.CharField(max_length=20, choices=Type.choices, default=Type.REPURPOSE)
    
    editing_style = models.CharField(max_length=20, choices=EditingStyle.choices, default=EditingStyle.DYNAMIC)
    brand_kit = models.ForeignKey(BrandKit, on_delete=models.SET_NULL, null=True, blank=True)
    
    source_file = models.FileField(upload_to='videos/raw/%Y/%m/')
    
    # Guardamos metadata técnica (duración, resolución, fps) y logs de error
    metadata = models.JSONField(default=dict, blank=True)
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.workspace_id})"


# --- 3. CLIP (El Resultado) ---
class VideoClip(models.Model):
    """
    Los clips generados por la IA a partir del proyecto.
    """
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        RENDERING = 'rendering', 'Renderizando'
        COMPLETED = 'completed', 'Listo para descargar'
        PUBLISHED = 'published', 'Publicado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(VideoProject, on_delete=models.CASCADE, related_name='clips')
    
    title = models.CharField(max_length=255)
    output_file = models.FileField(upload_to='videos/clips/%Y/%m/', null=True, blank=True)
    
    # Tiempos de corte
    start_time = models.FloatField()
    end_time = models.FloatField()
    
    # IA EXPLICABLE: ¿Por qué elegimos este clip?
    virality_score = models.IntegerField(default=0)
    ai_reasoning = models.TextField(blank=True, help_text="Explicación humana de por qué la IA eligió esto.")
    
    # Datos crudos para el frontend (ej: gráfico de emociones)
    ai_metadata = models.JSONField(default=dict, blank=True) 
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


# --- 4. LAYERS (Edición Manual) ---
class ClipLayer(models.Model):
    """
    Elementos superpuestos (texto, imágenes, b-roll) para el editor timeline.
    """
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
    
    # JSON para posición (x, y), rotación, escala, color, css styles
    position_data = models.JSONField(default=dict, blank=True)
    
    is_ai_generated = models.BooleanField(default=False)
    prompt_used = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.layer_type} @ {self.start_time}s"