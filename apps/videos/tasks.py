import os
import uuid
from celery import shared_task
from django.conf import settings
from django.core.files import File
import yt_dlp

# Modelos
from apps.videos.models import VideoProject
from apps.payments.models import Transaction

def download_from_youtube(url, output_folder):
    """
    Descarga video usando yt-dlp y devuelve la ruta del archivo.
    """
    # Nombre temporal único
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
    """
    1. Descarga video (si es URL).
    2. Analiza duración real.
    3. Confirma transacción.
    """
    print(f"🎬 [TASK START] Procesando Proyecto {project_id}")
    
    try:
        project = VideoProject.objects.get(id=project_id)
        # Si existe transacción, la buscamos
        tx = Transaction.objects.get(id=transaction_id) if transaction_id else None
        
        # Actualizamos estado a PROCESSING
        project.status = VideoProject.Status.PROCESSING
        project.save()

        # --- FASE 1: OBTENCIÓN DEL VIDEO ---
        if not project.source_file and project.video_url:
            print(f"⬇️ Descargando desde YouTube: {project.video_url}")
            
            # Definir dónde guardar (usamos la configuración de MEDIA_ROOT)
            # Asegúrate que la carpeta exista
            download_dir = os.path.join(settings.MEDIA_ROOT, 'videos', 'raw', 'downloads')
            os.makedirs(download_dir, exist_ok=True)
            
            try:
                # Descargamos
                local_path = download_from_youtube(project.video_url, download_dir)
                
                # Asignamos el archivo descargado al campo source_file del modelo
                # Esto es vital para que Django lo gestione de ahora en adelante
                with open(local_path, 'rb') as f:
                    project.source_file.save(os.path.basename(local_path), File(f), save=True)
                
                # Borramos el archivo temporal fuera de Django si es necesario, 
                # pero al usar .save() Django ya creó su copia/referencia.
                # En local dev suele ser el mismo, en S3 sería distinto.
                if os.path.exists(local_path):
                    os.remove(local_path)
                    
            except Exception as e:
                raise Exception(f"Error descargando video: {str(e)}")

        if not project.source_file:
            raise Exception("No hay archivo de video ni URL válida procesable.")

        # --- FASE 2: ANÁLISIS (Metadata Real) ---
        print("🤖 IA Analizando metadata del archivo...")
        
        # Aquí usamos MoviePy para sacar la duración exacta
        from moviepy import VideoFileClip
        
        # .path nos da la ruta absoluta en el disco
        clip = VideoFileClip(project.source_file.path)
        duration = clip.duration
        resolution = clip.size
        clip.close()

        # Guardamos la metadata real
        project.metadata = {
            "duration_seconds": duration,
            "resolution": resolution,
            "fps": getattr(clip, 'fps', 30),
            "original_source": project.video_url or "upload"
        }
        
        # --- FASE 3: CONFIRMACIÓN DE PAGO ---
        # (Aquí podrías recalcular el costo si el video resultó ser de 1 hora)
        if tx:
            # Confirmamos la transacción
            tx.status = Transaction.Status.CONFIRMED
            tx.save()

        # --- FIN ---
        project.status = VideoProject.Status.READY
        project.save()
        
        print(f"✅ [TASK END] Proyecto listo. Duración: {duration}s")
        return f"Success: {project_id}"

    except Exception as e:
        print(f"❌ [TASK ERROR] {e}")
        
        # Manejo de error seguro
        try:
            project = VideoProject.objects.get(id=project_id)
            project.status = VideoProject.Status.FAILED
            project.metadata['error_log'] = str(e)
            project.save()
            
            if transaction_id:
                tx = Transaction.objects.get(id=transaction_id)
                tx.status = Transaction.Status.FAILED
                tx.description += " [Error en procesamiento]"
                # Devolvemos el dinero
                tx.wallet.balance += abs(tx.amount) 
                tx.wallet.save()
                tx.save()
        except:
            pass # Si falla la conexión a DB aquí, no podemos hacer mucho más
            
        return f"Failed: {e}"