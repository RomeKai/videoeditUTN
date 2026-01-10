import os
import uuid
from celery import shared_task
from django.conf import settings
from django.core.files import File
from moviepy import VideoFileClip

# Importamos Modelos
from apps.videos.models import VideoProject, VideoClip
from apps.payments.models import Transaction

# Importamos Servicios de IA
from apps.videos.services.ai_engine import AIEngine
from apps.videos.services.selection_engine import SelectionEngine

def download_from_youtube(url, output_folder):
    """
    Descarga usando yt-dlp con importación lazy para evitar crasheos en web.
    """
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
            
            if os.path.exists(local_path):
                os.remove(local_path)

        if not project.source_file:
            raise Exception("No video source found")

        video_path = project.source_file.path

        # --- FASE 1.5: ANÁLISIS TÉCNICO (MOVIDO ARRIBA) ---
        # Calculamos la duración AHORA para usarla en la estrategia de selección
        print("📏 Calculando duración y resolución...")
        duration = 0
        try:
            clip = VideoFileClip(video_path)
            duration = clip.duration
            project.metadata['duration'] = duration
            project.metadata['resolution'] = clip.size
            clip.close() # Cerramos rápido para liberar memoria
        except Exception as e:
            print(f"⚠️ No se pudo leer metadata técnica: {e}")

        # --- FASE 2: TRANSCRIPCIÓN IA (Whisper) ---
        print("🧠 Iniciando transcripción con Whisper...")
        
        # A. Extraer Audio
        audio_path = AIEngine.extract_audio(video_path)
        
        # B. Transcribir
        try:
            transcription = AIEngine.transcribe_audio(audio_path)
            
            # C. Guardar resultados crudos
            project.metadata['language'] = transcription['language']
            project.metadata['full_text'] = transcription['full_text']
            # Guardamos toda la estructura para usos futuros
            project.metadata['transcription'] = transcription 
            
        finally:
            # Limpieza obligatoria del audio temporal
            if os.path.exists(audio_path):
                os.remove(audio_path)

        # --- FASE 3: SELECCIÓN INTELIGENTE (GPT Strategy) ---
        print(f"🤖 Consultando Motor de Selección (Nivel: {project.intelligence_level})...")
        
        try:
            # Usamos el SelectionEngine con la estrategia configurada
            ai_suggestions = SelectionEngine.select_viral_clips(
                transcription_data=project.metadata,
                project_title=project.title,
                editing_style=project.editing_style,
                duration=duration,
                intelligence_level=project.intelligence_level
            )
            
            print(f"💾 Guardando {len(ai_suggestions)} clips sugeridos en DB...")
            
            for clip_data in ai_suggestions:
                VideoClip.objects.create(
                    project=project,
                    title=clip_data.get('title', 'Clip sugerido'),
                    start_time=clip_data.get('start', 0.0),
                    end_time=clip_data.get('end', 10.0),
                    virality_score=clip_data.get('virality_score', 0),
                    ai_reasoning=clip_data.get('reasoning', ''),
                    status=VideoClip.Status.DRAFT
                )
                
            project.metadata['ai_selection_done'] = True

        except Exception as e:
            print(f"⚠️ Error en Selección de IA: {e}")
            # No hacemos raise para no fallar todo el proyecto.
            # El usuario tendrá el video y la transcripción, aunque sin sugerencias automáticas.

        # --- FIN ---
        if transaction_id:
            tx.status = Transaction.Status.CONFIRMED
            tx.save()

        project.status = VideoProject.Status.READY
        project.save()
        
        print(f"✅ [TASK END] Éxito total. Idioma: {transcription.get('language', '?')}")
        return f"Success {project_id}"

    except Exception as e:
        print(f"❌ [ERROR CRÍTICO] {e}")
        # Actualizamos estado a FAILED si algo crítico rompió el proceso
        try:
            project = VideoProject.objects.get(id=project_id)
            project.status = VideoProject.Status.FAILED
            project.save()
        except:
            pass
        return f"Failed: {e}"