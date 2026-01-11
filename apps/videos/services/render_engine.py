import os
import logging
from django.conf import settings
from django.core.files import File

# IMPORTANTE: Importación correcta para MoviePy v2
from moviepy import VideoFileClip

# Importamos Modelos y Layouts
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.layouts import get_layout_strategy

logger = logging.getLogger(__name__)

class RenderEngine:
    """
    FACADE: Coordina la renderización física de los clips.
    Usa 'layouts.py' para decidir CÓMO se ve el video.
    """
    
    @staticmethod
    def _get_target_resolution(aspect_ratio_str):
        """Devuelve (ancho, alto) basado en el enum."""
        # Estandarizamos altura a 1080p (Full HD)
        target_h = 1080  
        
        if aspect_ratio_str == '9:16':
            target_w = int(target_h * (9/16)) # 608px
        elif aspect_ratio_str == '1:1':
            target_w = target_h # 1080px
        elif aspect_ratio_str == '16:9':
            target_w = int(target_h * (16/9)) # 1920px
        else:
            target_w = int(target_h * (9/16)) # Default vertical
            
        return target_w, target_h

    @staticmethod
    def render_clip(clip_id):
        """
        Proceso principal de renderizado.
        """
        clip_obj = None
        original_clip = None
        final_clip = None
        output_path = None
        
        try:
            # 1. Recuperar datos
            clip_obj = VideoClip.objects.get(id=clip_id)
            project = clip_obj.project
            
            logger.info(f"✂️ START Render: {clip_obj.title} [{project.aspect_ratio} - {project.render_layout}]")
            
            clip_obj.status = VideoClip.Status.RENDERING
            clip_obj.save()

            # 2. Configurar rutas
            original_path = project.source_file.path
            filename = f"clip_{clip_obj.id}.mp4"
            
            # Carpeta temporal local (dentro del contenedor)
            temp_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'clips', 'temp')
            os.makedirs(temp_dir, exist_ok=True)
            output_path = os.path.join(temp_dir, filename)

            # 3. Cargar Video (Optimizamos cargando solo el fragmento necesario)
            # CORRECCIÓN V2: Usamos .subclipped() en lugar de .subclip()
            original_clip = VideoFileClip(original_path).subclipped(clip_obj.start_time, clip_obj.end_time)
            
            # 4. Calcular Resolución Objetivo
            target_w, target_h = RenderEngine._get_target_resolution(project.aspect_ratio)
            
            # 5. ESTRATEGIA: Obtener y aplicar el Layout
            # Las estrategias en layouts/ también deben usar sintaxis v2 (resized, cropped, etc.)
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h)
            final_clip = layout_strategy.apply(original_clip)
            
            # 6. Renderizar Físicamente (FFmpeg)
            logger.info(f"🚀 Renderizando archivo físico: {output_path}")
            
            final_clip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                fps=24,              # Estandarizamos a 24fps cinemático/redes
                preset='ultrafast',  # 'ultrafast' para dev, 'medium' para prod
                threads=4,           # Usar núcleos de CPU disponibles
                logger=None          # Silenciamos output de moviepy en logs de celery
            )
            
            # 7. Guardar en Django (Storage)
            with open(output_path, 'rb') as f:
                clip_obj.output_file.save(filename, File(f), save=True)
                
            clip_obj.status = VideoClip.Status.COMPLETED
            clip_obj.save()
            
            logger.info(f"✅ Render Exitoso: {clip_obj.output_file.url}")
            return True

        except Exception as e:
            logger.error(f"❌ Falló Render {clip_id}: {e}")
            if clip_obj:
                clip_obj.status = VideoClip.Status.DRAFT # Revertir a borrador para reintentar
                clip_obj.save()
            raise e
            
        finally:
            # 8. Limpieza de Recursos
            # En v2, close() sigue siendo importante para liberar el proceso de FFmpeg
            if original_clip: 
                original_clip.close()
            if final_clip:
                final_clip.close()
            
            # Borrar archivo temporal del disco local del worker
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass