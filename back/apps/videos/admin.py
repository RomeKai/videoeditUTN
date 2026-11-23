from django.contrib import admin
from django.db import transaction

from .models import BrandKit, ClipLayer, ModerationOverride, ScheduledPost, VideoClip, VideoProject

# --- 1. BRAND KIT ---
@admin.register(BrandKit)
class BrandKitAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'primary_color', 'created_at')
    search_fields = ('name', 'workspace__name')

# --- 2. LAYERS ---
@admin.register(ClipLayer)
class ClipLayerAdmin(admin.ModelAdmin):
    list_display = ('layer_type', 'clip', 'start_time', 'end_time', 'is_ai_generated')
    list_filter = ('layer_type', 'is_ai_generated')

# --- 3. CLIPS ---
class VideoClipInline(admin.TabularInline):
    model = VideoClip
    extra = 0
    show_change_link = True

@admin.register(VideoClip)
class VideoClipAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'start_time', 'end_time', 'status', 'virality_score')
    list_filter = ('status', 'project__project_type')
    search_fields = ('title', 'project__title')

# --- 4. PROYECTO (Padre) ---
@admin.register(VideoProject)
class VideoProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'workspace', 'uploaded_by', 'status', 'created_at')
    list_filter = ('status', 'project_type', 'editing_style')
    search_fields = ('title', 'workspace__name', 'uploaded_by__email')
    
    inlines = [VideoClipInline]
    
    fieldsets = (
        ('Información Principal', {
            'fields': ('title', 'workspace', 'uploaded_by', 'status')
        }),
        ('Almacenamiento R2', {
            'fields': ('original_r2_key', 'proxy_r2_key', 'final_export_r2_key')
        }),
        ('Metadatos IA & Paper Edit', {
            'fields': ('ai_rationale_log', 'transcript_data', 'approved_segments', 'metadata')
        }),
        ('Configuración Técnica', {
            'fields': ('project_type', 'editing_style', 'brand_kit', 'aspect_ratio', 'render_layout', 'auto_render_bypass')
        }),
        ('Archivos Locales (Dev)', {
            'fields': ('source_file', 'video_url')
        }),
    )

@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):
    list_display = ('id', 'video_clip', 'platform', 'publish_at', 'status')
    list_filter = ('platform', 'status')
    search_fields = ('video_clip__title',)
    actions = ('regenerate_ai_metadata',)

    @admin.action(description="Regenerar caption y hashtags con IA")
    def regenerate_ai_metadata(self, request, queryset):
        """
        Explicit regeneration (ADR-013): replaces the caption and hashtags. Posts
        already publishing or published are skipped.
        """
        from .signals import queue_post_seo

        skipped = (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED)
        posts = list(queryset.exclude(status__in=skipped).values_list('pk', flat=True))
        for post_id in posts:
            queue_post_seo(post_id, regenerate=True)
        self.message_user(request, f"Generación encolada para {len(posts)} publicaciones.")


# --- 6. MODERACIÓN (ADR-011 D-M5) ---
@admin.register(ModerationOverride)
class ModerationOverrideAdmin(admin.ModelAdmin):
    """
    Lets an admin release a project blocked by moderation after a human review.
    Records are an audit trail: created with a mandatory reason, never edited or
    deleted. Saving one re-dispatches the ingestion of a FAILED project, which
    then passes the moderation stage without calling the API.
    """
    list_display = ('project', 'created_by', 'created_at')
    search_fields = ('project__title', 'project__id')
    raw_id_fields = ('project',)
    readonly_fields = ('created_by', 'created_at')

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        super().save_model(request, obj, form, change)
        project = obj.project
        if project.pipeline_stage == VideoProject.PipelineStage.FAILED:
            from apps.videos.tasks import process_initial_ingestion

            project_id = str(project.pk)
            transaction.on_commit(lambda: process_initial_ingestion.delay(project_id))
            self.message_user(request, "Override registrado: la ingestión se vuelve a encolar.")
        else:
            self.message_user(request, "Override registrado. El proyecto no está en FAILED: no se re-encola.")
