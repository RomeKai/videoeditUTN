from django.contrib import admin
from .models import VideoProject, VideoClip, ClipLayer, BrandKit

# --- 1. BRAND KIT ---
@admin.register(BrandKit)
class BrandKitAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'primary_color', 'font_family')
    search_fields = ('name', 'user__email')

# --- 2. LAYERS (Inline) ---
# Esto permite ver las capas dentro del clip
class ClipLayerInline(admin.TabularInline):
    model = ClipLayer
    extra = 0 # No mostrar filas vacías extra
    fields = ('layer_type', 'start_time', 'duration', 'text_content', 'file', 'is_ai_generated')

# --- 3. CLIPS (Inline y Admin propio) ---
# Esto permite ver los clips dentro del proyecto padre
class VideoClipInline(admin.TabularInline):
    model = VideoClip
    extra = 0
    fields = ('title', 'start_time', 'end_time', 'status', 'virality_score')
    show_change_link = True # Botón para editar el clip en detalle

@admin.register(VideoClip)
class VideoClipAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'start_time', 'end_time', 'status', 'virality_score')
    list_filter = ('status', 'project__project_type')
    search_fields = ('title', 'project__title')
    inlines = [ClipLayerInline] # ¡Aquí ves las capas dentro del clip!

# --- 4. PROYECTO (Padre) ---
@admin.register(VideoProject)
class VideoProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'project_type', 'editing_style', 'status', 'created_at')
    list_filter = ('status', 'project_type', 'editing_style')
    search_fields = ('title', 'user__email')
    inlines = [VideoClipInline] # ¡Aquí ves los clips hijos!
    
    # Organizar campos bonito
    fieldsets = (
        ('Información Principal', {
            'fields': ('title', 'user', 'status')
        }),
        ('Configuración', {
            'fields': ('project_type', 'editing_style', 'brand_kit')
        }),
        ('Archivos', {
            'fields': ('source_file', 'metadata')
        }),
    )