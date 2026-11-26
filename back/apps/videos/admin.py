from django.contrib import admin, messages
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
        Explicit regeneration (ADR-013): replaces the caption and hashtags. Only
        for projects that asked for SEO on a plan that includes it; posts already
        publishing or published are skipped. A failed regeneration keeps the
        previous caption.
        """
        from .signals import queue_post_seo, seo_requested

        skipped = (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED)
        candidates = queryset.exclude(status__in=skipped).select_related(
            'video_clip__project__workspace__subscription_plan',
        )
        posts = [post.pk for post in candidates if seo_requested(post.video_clip.project)]
        for post_id in posts:
            queue_post_seo(post_id, regenerate=True)
        ignored = queryset.count() - len(posts)
        message = f"Generación encolada para {len(posts)} publicaciones."
        if ignored:
            message += f" {ignored} se omitieron (ya publicadas o sin SEO contratado)."
        self.message_user(request, message)


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
        from apps.videos.tasks.ingestion import INGESTION_NOT_QUEUED, INGESTION_STUCK

        if project.pipeline_error_code == INGESTION_NOT_QUEUED:
            # Refunded at creation because its ingestion was never queued: the
            # pipeline never resumes it (_run_ingestion), so nothing to queue.
            self.message_user(
                request,
                "Override registrado. Este proyecto se devolvió al crearse porque no se pudo encolar "
                "su ingestión: no se re-encola. El usuario tiene que crearlo de nuevo.",
                level=messages.WARNING,
            )
        elif project.pipeline_stage == VideoProject.PipelineStage.FAILED:
            from apps.videos.tasks import process_initial_ingestion
            from apps.videos.tasks.ingestion import INGESTION_CHAIN_KEY, new_ingestion_chain
            from apps.videos.tasks.shared import _merge_metadata

            project_id = str(project.pk)
            # A chain of its own: only this message reopens the project, and a
            # copy of an older one that was only delayed runs nothing.
            chain = new_ingestion_chain()
            _merge_metadata(project_id, {INGESTION_CHAIN_KEY: chain})
            transaction.on_commit(lambda: process_initial_ingestion.delay(project_id, chain=chain))
            if project.pipeline_error_code == INGESTION_STUCK:
                self.message_user(request, (
                    "Override registrado: la ingestión se vuelve a encolar. Su reserva se había devuelto "
                    "porque se trabó (IngestionStuck): corre sin volver a cobrar."
                ), level=messages.WARNING)
            else:
                self.message_user(request, "Override registrado: la ingestión se vuelve a encolar.")
        else:
            self.message_user(request, "Override registrado. El proyecto no está en FAILED: no se re-encola.")
