from django.contrib import admin
from .models import VideoProject, VideoClip, ClipLayer, BrandKit

# --- 1. BRAND KIT ---
@admin.register(BrandKit)
class BrandKitAdmin(admin.ModelAdmin):
    # CAMBIO: 'user' -> 'workspace'
    list_display = ('name', 'workspace', 'primary_color', 'font_family')
    # CAMBIO: Buscamos por nombre del workspace, no email de usuario
    search_fields = ('name', 'workspace__name')

# --- 2. LAYERS (Inline) ---
class ClipLayerInline(admin.TabularInline):
    model = ClipLayer
    extra = 0
    fields = ('layer_type', 'start_time', 'duration', 'text_content', 'file', 'is_ai_generated')

# --- 3. CLIPS (Inline y Admin propio) ---
class VideoClipInline(admin.TabularInline):
    model = VideoClip
    extra = 0
    fields = ('title', 'start_time', 'end_time', 'status', 'virality_score')
    show_change_link = True

@admin.register(VideoClip)
class VideoClipAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'start_time', 'end_time', 'status', 'virality_score')
    list_filter = ('status', 'project__project_type')
    search_fields = ('title', 'project__title')
    inlines = [ClipLayerInline]

# --- 4. PROYECTO (Padre) ---
@admin.register(VideoProject)
class VideoProjectAdmin(admin.ModelAdmin):
    # CAMBIO: 'user' -> 'workspace' y 'uploaded_by'
    list_display = ('title', 'workspace', 'uploaded_by', 'project_type', 'status', 'created_at')
    
    list_filter = ('status', 'project_type', 'editing_style')
    
    # CAMBIO: Buscamos por Workspace o por email del uploader
    search_fields = ('title', 'workspace__name', 'uploaded_by__email')
    
    inlines = [VideoClipInline]
    
    fieldsets = (
        ('Información Principal', {
            # CAMBIO: user -> workspace, uploaded_by
            'fields': ('title', 'workspace', 'uploaded_by', 'status')
        }),
        ('Configuración', {
            'fields': ('project_type', 'editing_style', 'brand_kit')
        }),
        ('Archivos', {
            'fields': ('source_file', 'metadata')
        }),
    )