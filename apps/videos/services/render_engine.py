import os
import logging
from django.conf import settings
from django.core.files import File

# MoviePy v2 Import
from moviepy import VideoFileClip

# Importamos Modelos y Layouts
from apps.videos.models import VideoClip
from apps.videos.services.layouts import get_layout_strategy

# Servicios (Lazy loading de Whisper se mantiene, pero priorizamos reutilización)
from apps.videos.services.transcription_engine import TranscriptionEngine
from apps.videos.services.subtitle_engine import SubtitleEngine

logger = logging.getLogger(__name__)

class RenderEngine:
    """
    FACADE: Coordina la renderización física de los clips.
    Optimizado para reutilizar metadata existente.
    """
    
    @staticmethod
    def _get_target_resolution(aspect_ratio_str):
        target_h = 1080  
        if aspect_ratio_str == '9:16':
            target_w = int(target_h * (9/16)) 
        elif aspect_ratio_str == '1:1':
            target_w = target_h 
        elif aspect_ratio_str == '16:9':
            target_w = int(target_h * (16/9)) 
        else:
            target_w = int(target_h * (9/16)) 
        return target_w, target_h

    @staticmethod
    def _slice_segments(global_segments, clip_start, clip_end):
        """
        FILTRO INTELIGENTE:
        Toma la transcripción global y extrae solo lo que pertenece a este clip.
        Ajusta los timestamps para que empiecen en 0 relativa al nuevo clip.
        """
        clip_segments = []
        for seg in global_segments:
            # Verificamos si el segmento solapa con el rango del clip
            # Un segmento es relevante si termina después de que empieza el clip 
            # Y empieza antes de que termine el clip.
            if seg['end'] > clip_start and seg['start'] < clip_end:
                
                # Calcular nuevos tiempos relativos (Offset)
                # Ejemplo: Si el clip empieza en el seg 30, y la palabra está en el 32.
                # Nuevo tiempo = 32 - 30 = 2.
                new_start = max(0.0, seg['start'] - clip_start)
                new_end = min(clip_end - clip_start, seg['end'] - clip_start)
                
                # Solo agregamos si dura algo (evitar segmentos de 0s)
                if new_end > new_start:
                    clip_segments.append({
                        "text": seg['text'],
                        "start": new_start,
                        "end": new_end
                    })
        return clip_segments

    @staticmethod
    def render_clip(clip_id):
        clip_obj = None
        original_clip = None
        final_clip = None
        output_path = None
        temp_audio_path = None 

        try:
            # 1. Recuperar datos
            clip_obj = VideoClip.objects.get(id=clip_id)
            project = clip_obj.project
            
            logger.info(f"✂️ START Render: {clip_obj.title} [{project.aspect_ratio}]")
            
            clip_obj.status = VideoClip.Status.RENDERING
            clip_obj.save()

            # 2. Configurar rutas
            original_path = project.source_file.path
            filename = f"clip_{clip_obj.id}.mp4"
            temp_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'clips', 'temp')
            os.makedirs(temp_dir, exist_ok=True)
            output_path = os.path.join(temp_dir, filename)

            # 3. Cargar Video (Subclip)
            # subclipped(start, end) es eficiente, no carga todo el video en RAM
            original_clip = VideoFileClip(original_path).subclipped(clip_obj.start_time, clip_obj.end_time)
            
            # 4. Aplicar Layout
            target_w, target_h = RenderEngine._get_target_resolution(project.aspect_ratio)
            layout_strategy = get_layout_strategy(project.render_layout, target_w, target_h)
            video_layout_processed = layout_strategy.apply(original_clip)
            
            # 5. --- FASE DE SUBTITULADO OPTIMIZADA ---
            if getattr(project, 'add_subtitles', True): # Default True o campo del modelo
                
                segments = []
                
                # OPCIÓN A: Reutilización (La Correcta)
                # Verificamos si ya existe la transcripción en metadata (guardada por tasks.py)
                existing_transcription = project.metadata.get('transcription', [])
                
                if existing_transcription:
                    logger.info("♻️ Reutilizando transcripción global existente (Cero costo CPU).")
                    segments = RenderEngine._slice_segments(
                        existing_transcription, 
                        clip_obj.start_time, 
                        clip_obj.end_time
                    )
                
                # OPCIÓN B: Fallback (Plan de Emergencia)
                # Solo si NO hay metadata, corremos Whisper para este clip
                else:
                    logger.warning("⚠️ No se encontró metadata. Ejecutando Whisper de respaldo...")
                    temp_audio_path = os.path.join(temp_dir, f"audio_{clip_obj.id}.mp3")
                    original_clip.audio.write_audiofile(temp_audio_path, codec='mp3', logger=None)
                    
                    transcriber = TranscriptionEngine(model_size="tiny") 
                    segments = transcriber.transcribe(temp_audio_path)

                # Renderizar Texto
                if segments:
                    subtitler = SubtitleEngine(font_size=45, color="yellow")
                    final_clip = subtitler.add_subtitles(video_layout_processed, segments)
                else:
                    logger.info("ℹ️ No hay diálogos en este segmento.")
                    final_clip = video_layout_processed
                
            else:
                final_clip = video_layout_processed

            # 6. Renderizar Físicamente
            logger.info(f"🚀 Renderizando archivo físico: {output_path}")
            
            final_clip.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                fps=24,
                preset='ultrafast',
                threads=4,
                logger=None
            )
            
            # 7. Guardar y Limpiar
            with open(output_path, 'rb') as f:
                clip_obj.output_file.save(filename, File(f), save=True)
                
            clip_obj.status = VideoClip.Status.COMPLETED
            clip_obj.save()
            
            logger.info(f"✅ Render Exitoso: {clip_obj.output_file.url}")
            return True

        except Exception as e:
            logger.error(f"❌ Falló Render {clip_id}: {e}", exc_info=True)
            if clip_obj:
                clip_obj.status = VideoClip.Status.DRAFT # Volver a borrador si falla
                clip_obj.save()
            raise e
            
        finally:
            # 8. Limpieza de Recursos (Close handles)
            try:
                if original_clip: original_clip.close()
                if final_clip and final_clip != original_clip: final_clip.close()
                if output_path and os.path.exists(output_path): os.remove(output_path)
                if temp_audio_path and os.path.exists(temp_audio_path): os.remove(temp_audio_path)
            except:
                pass