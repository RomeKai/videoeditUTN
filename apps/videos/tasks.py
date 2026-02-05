import os
import uuid
import logging
from celery import shared_task
from django.conf import settings
from django.core.files import File

# Importamos Modelos
from apps.videos.models import VideoProject, VideoClip
from apps.payments.models import Transaction

# Importamos Servicios (SelectionEngine suele ser seguro, TranscriptionEngine lo cargaremos lazy por si acaso)
from apps.videos.services.selection_engine import SelectionEngine

logger = logging.getLogger(__name__)

def download_from_youtube(url, output_folder):
    import yt_dlp
    
    filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(output_folder, filename)
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    return output_path

@shared_task
def process_video_pipeline(project_id, transaction_id=None):
    logger.info(f"🎬 [TASK START] Procesando Proyecto {project_id}")
    
    try:
        project = VideoProject.objects.get(id=project_id)
        if transaction_id:
            try:
                tx = Transaction.objects.get(id=transaction_id)
            except Transaction.DoesNotExist:
                tx = None
        
        project.status = 'processing' # Asegúrate de que coincida con tus choices del modelo
        project.save()

        # --- FASE 1: OBTENCIÓN (Descarga) ---
        if not project.source_file and project.video_url:
            logger.info(f"⬇️ Descargando: {project.video_url}")
            download_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'raw', 'downloads')
            os.makedirs(download_dir, exist_ok=True)
            
            local_path = download_from_youtube(project.video_url, download_dir)
            
            # Guardamos en Django Storage
            with open(local_path, 'rb') as f:
                project.source_file.save(os.path.basename(local_path), File(f), save=True)
            
            # Limpieza local
            if os.path.exists(local_path):
                os.remove(local_path)

        if not project.source_file:
            raise Exception("No video source found (ni archivo ni URL)")

        video_path = project.source_file.path

        # --- FASE 1.5: DATOS TÉCNICOS ---
        logger.info("📏 Calculando duración y resolución...")
        duration = 0
        try:
            # 🔥 IMPORTACIÓN LAZY: MoviePy
            from moviepy.video.io.VideoFileClip import VideoFileClip
            
            with VideoFileClip(video_path) as clip:
                duration = clip.duration
                project.metadata['duration'] = duration
                # Guardamos resolución como lista/tupla simple para que sea serializable en JSON
                project.metadata['resolution'] = list(clip.size) 
        except Exception as e:
            logger.warning(f"⚠️ No se pudo leer metadata técnica: {e}")

        # --- FASE 2: INTELIGENCIA ARTIFICIAL (Transcipción con Nuevo Engine) ---
        logger.info("🧠 Iniciando transcripción con TranscriptionEngine...")
        
        # 🔥 IMPORTACIÓN LAZY: Whisper es pesado
        from apps.videos.services.transcription_engine import TranscriptionEngine
        
        # Instanciamos el motor (usa 'tiny' o 'base' según prefieras velocidad vs precisión)
        transcriber = TranscriptionEngine(model_size="base")
        
        # Transcribimos (Whisper lee el video directo, no hace falta extraer audio aparte)
        segments = transcriber.transcribe(video_path)
        
        # Reconstruimos el texto completo para el SelectionEngine
        full_text = " ".join([seg['text'] for seg in segments])
        
        # Guardamos en metadata
        project.metadata['language'] = 'detected' # Whisper lo detecta auto, podríamos mejorar el Engine para devolverlo
        project.metadata['full_text'] = full_text
        project.metadata['transcription'] = segments # Guardamos los segmentos con timestamps
        project.save()

        # --- FASE 3: SELECCIÓN INTELIGENTE (GPT) ---
        logger.info(f"🤖 Consultando Motor de Selección (Nivel: {getattr(project, 'intelligence_level', 'standard')})...")
        
        created_clips = [] 
        
        try:
            # SelectionEngine usa 'full_text' para entender el contexto
            ai_suggestions = SelectionEngine.select_viral_clips(
                transcription_data=project.metadata,
                project_title=project.title,
                editing_style=getattr(project, 'editing_style', 'dynamic'),
                duration=duration,
                intelligence_level=getattr(project, 'intelligence_level', 'standard')
            )
            
            logger.info(f"💾 Guardando {len(ai_suggestions)} clips sugeridos en DB...")
            
            for clip_data in ai_suggestions:
                clip = VideoClip.objects.create(
                    project=project,
                    title=clip_data.get('title', 'Clip sugerido'),
                    start_time=clip_data.get('start', 0.0),
                    end_time=clip_data.get('end', 10.0),
                    virality_score=clip_data.get('virality_score', 0),
                    ai_reasoning=clip_data.get('reasoning', ''),
                    status='draft' # o VideoClip.Status.DRAFT
                )
                created_clips.append(clip)
                
            project.metadata['ai_selection_done'] = True
            project.save()

        except Exception as e:
            logger.error(f"⚠️ Error en Selección de IA: {e}")
            # Fallback: Si falla la IA, podríamos crear un clip manual o dejarlo vacío
            pass

        # --- FASE 4: RENDERIZADO AUTOMÁTICO ---
        if created_clips:
            logger.info(f"✂️ Iniciando Renderizado de {len(created_clips)} clips...")
            
            # 🔥 IMPORTACIÓN LAZY CRÍTICA 🔥
            from apps.videos.services.render_engine import RenderEngine
            
            for clip in created_clips:
                try:
                    RenderEngine.render_clip(clip.id)
                except Exception as e:
                    logger.error(f"⚠️ Falló render del clip {clip.id}: {e}")
                    clip.status = 'failed'
                    clip.save()

        # --- FIN ---
        if transaction_id and tx:
            try:
                tx.status = 'confirmed' # Transaction.Status.CONFIRMED
                tx.save()
            except:
                pass

        project.status = 'completed' # VideoProject.Status.READY/COMPLETED
        project.save()
        
        logger.info(f"✅ [TASK END] Éxito total. Proyecto {project_id} finalizado.")
        return f"Success {project_id}"

    except Exception as e:
        logger.error(f"❌ [ERROR CRÍTICO] {e}", exc_info=True)
        try:
            project = VideoProject.objects.get(id=project_id)
            project.status = 'failed'
            project.save()
            
            if transaction_id:
                tx = Transaction.objects.get(id=transaction_id)
                tx.status = 'cancelled'
                tx.save()
        except:
            pass 
        return f"Failed: {e}"