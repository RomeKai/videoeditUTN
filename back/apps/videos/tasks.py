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
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.google.com/',
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    return output_path

import time
import random
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.core.cache import cache
from apps.videos.models import VideoProject, VideoClip, ScheduledPost

from backend.celery import app as celery_app

logger = logging.getLogger(__name__)

class ThirdPartyAPIError(Exception):
    """Exception raised when a social media API fails."""
    pass

@celery_app.task
def dispatch_scheduled_posts_batch():
    """
    Orchestrator: Dispatches posts scheduled for the next hour.
    Runs every hour via Celery Beat.
    """
    now = timezone.now()
    window_end = now + timedelta(hours=1)
    
    # Batch query for scheduled posts in the next hour
    scheduled_posts = ScheduledPost.objects.filter(
        status=ScheduledPost.Status.SCHEDULED,
        publish_at__range=(now, window_end)
    )
    
    count = 0
    for post in scheduled_posts:
        with transaction.atomic():
            # Lock the row for update and check status again to prevent race conditions
            post_to_queue = ScheduledPost.objects.select_for_update().get(id=post.id)
            if post_to_queue.status == ScheduledPost.Status.SCHEDULED:
                post_to_queue.status = ScheduledPost.Status.QUEUED
                post_to_queue.save()
                
                # Send to worker with specific ETA
                upload_to_social_network.apply_async(
                    args=[post_to_queue.id],
                    eta=post_to_queue.publish_at
                )
                count += 1
    
    logger.info(f"🚀 [DISPATCHER] Queued {count} posts for distribution.")
    return f"Queued {count} posts."

@celery_app.task(bind=True, max_retries=3)
def upload_to_social_network(self, post_id):
    """
    Worker: Uploads a video clip to the specified social network.
    Uses Distributed Locking to prevent cross-site double posting.
    """
    lock_id = f"lock_post_publish_{post_id}"
    # 1. Distributed Lock (Timeout 5 minutes)
    # cache.add returns False if the key already exists
    if not cache.add(lock_id, "locked", 300):
        logger.warning(f"🔒 [WORKER] Aborting post {post_id}: Task already running or locked.")
        return "Locked"

    try:
        with transaction.atomic():
            post = ScheduledPost.objects.select_for_update().get(id=post_id)
            if post.status == ScheduledPost.Status.PUBLISHED:
                return "Already Published"
            
            post.status = ScheduledPost.Status.PROCESSING
            post.save()

        logger.info(f"📤 [WORKER] Publishing {post.platform} post {post_id}...")
        
        # 2. Simulation of Third-Party API Call
        time.sleep(2)
        
        # 20% failure rate for simulation
        if random.random() < 0.20:
            raise ThirdPartyAPIError("Connection timeout with social media API.")
        
        # 3. Success state
        post.status = ScheduledPost.Status.PUBLISHED
        post.error_log = None
        post.save()
        logger.info(f"✅ [WORKER] Post {post_id} successfully published on {post.platform}.")
        
        return f"Published to {post.platform}"

    except ThirdPartyAPIError as exc:
        # 4. Resilience: Exponential Backoff
        logger.error(f"⚠️ [WORKER] API Error for post {post_id}: {exc}")
        post.retry_count += 1
        post.error_log = str(exc)
        post.save()
        
        # Calculate countdown: 60s, 360s, 1200s...
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

    except Exception as e:
        logger.error(f"❌ [WORKER] Critical failure for post {post_id}: {e}")
        post.status = ScheduledPost.Status.FAILED
        post.error_log = str(e)
        post.save()
        return "Failed"

    finally:
        # 5. Cierre: Liberar el lock
        cache.delete(lock_id)

@shared_task
def render_clip_task(clip_id):
    """
    Task wrapper for the RenderEngine.render_clip method.
    Allows for asynchronous rendering of individual clips.
    """
    logger.info(f"🎬 [RENDER TASK START] Rendering Clip {clip_id}")
    from apps.videos.services.render_engine import RenderEngine
    try:
        RenderEngine.render_clip(clip_id)
        return f"Clip {clip_id} rendered successfully"
    except Exception as e:
        logger.error(f"❌ [RENDER TASK FAILED] {clip_id}: {e}")
        return f"Failed {clip_id}: {e}"

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
            # Soporte para archivos locales (para tests)
            if os.path.exists(project.video_url):
                logger.info(f"📂 Usando archivo local: {project.video_url}")
                with open(project.video_url, 'rb') as f:
                    project.source_file.save(os.path.basename(project.video_url), File(f), save=True)
            else:
                logger.info(f"⬇️ Descargando de URL: {project.video_url}")
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
        
        # Transcribimos con marcas de tiempo por palabra para mayor precisión en cortes
        segments = transcriber.transcribe(video_path, word_timestamps=True)
        
        # Reconstruimos el texto completo para el SelectionEngine
        full_text = " ".join([seg['text'] for seg in segments])
        
        # Guardamos en metadata
        project.metadata['language'] = 'detected' 
        project.metadata['full_text'] = full_text
        project.metadata['transcription'] = segments # Ahora son palabras individuales
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