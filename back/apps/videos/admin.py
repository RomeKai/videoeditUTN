from django.contrib import admin
from .models import VideoProject, VideoClip, ClipLayer, BrandKit, ScheduledPost

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
