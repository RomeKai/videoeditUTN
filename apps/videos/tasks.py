import os
import uuid
from celery import shared_task
from django.conf import settings
from django.core.files import File

from moviepy import VideoFileClip

# Importamos Modelos y el Nuevo Servicio
from apps.videos.models import VideoProject
from apps.payments.models import Transaction
from apps.videos.services.ai_engine import AIEngine # <--- IMPORTANTE

# ... (Tu función download_from_youtube sigue igual aquí) ...
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
# ... ------------------------------------------------ ...

@shared_task
def process_video_pipeline(project_id, transaction_id=None):
    print(f"🎬 [TASK START] Procesando Proyecto {project_id}")
    
    try:
        project = VideoProject.objects.get(id=project_id)
        if transaction_id:
            tx = Transaction.objects.get(id=transaction_id)
        
        project.status = VideoProject.Status.PROCESSING
        project.save()

        # --- FASE 1: OBTENCIÓN (Descarga) ---
        if not project.source_file and project.video_url:
            print(f"⬇️ Descargando: {project.video_url}")
            download_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'raw', 'downloads')
            os.makedirs(download_dir, exist_ok=True)
            local_path = download_from_youtube(project.video_url, download_dir)
            
            with open(local_path, 'rb') as f:
                project.source_file.save(os.path.basename(local_path), File(f), save=True)
            
            if os.path.exists(local_path): os.remove(local_path)

        if not project.source_file:
            raise Exception("No video source found")

        video_path = project.source_file.path

        # --- FASE 2: INTELIGENCIA ARTIFICIAL (La parte nueva) ---
        print("🧠 Iniciando análisis de IA...")
        
        # A. Extraer Audio
        audio_path = AIEngine.extract_audio(video_path)
        
        # B. Transcribir
        try:
            transcription = AIEngine.transcribe_audio(audio_path)
            
            # C. Guardar resultados
            project.metadata['language'] = transcription['language']
            project.metadata['full_text'] = transcription['full_text']
            # Guardamos los segmentos (timestamps) para cortar después
            project.metadata['segments'] = transcription['segments']
            
        finally:
            # Limpiamos el mp3 temporal
            if os.path.exists(audio_path):
                os.remove(audio_path)

        # --- FASE 3: DATOS TÉCNICOS ---
        clip = VideoFileClip(video_path)
        project.metadata['duration'] = clip.duration
        project.metadata['resolution'] = clip.size
        clip.close()

        # --- FIN ---
        if transaction_id:
            tx.status = Transaction.Status.CONFIRMED
            tx.save()

        project.status = VideoProject.Status.READY
        project.save()
        
        print(f"✅ [TASK END] Éxito. Idioma detectado: {transcription['language']}")
        return f"Success {project_id}"

    except Exception as e:
        print(f"❌ [ERROR] {e}")
        # (Aquí tu lógica de manejo de errores fallidos que ya tenías)
        return f"Failed: {e}"