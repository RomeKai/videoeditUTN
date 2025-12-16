#SIMULACION DE TASKS SOLAMENTE PARA MOCKEAR NO FINAL 

import time
from celery import shared_task
from django.conf import settings

# Modelos
from apps.videos.models import VideoProject
from apps.payments.models import Transaction

@shared_task
def process_video_pipeline(project_id, transaction_id):
    """
    Tarea en segundo plano que orquesta la creación de clips.
    Por ahora es un MVP que simula el proceso.
    """
    print(f"🎬 [TASK START] Iniciando pipeline para Proyecto {project_id}")
    
    try:
        # 1. Recuperar objetos de la DB
        project = VideoProject.objects.get(id=project_id)
        transaction = Transaction.objects.get(id=transaction_id)
        
        # 2. Actualizar estado a PROCESSING
        project.status = 'processing'
        project.save()
        
        # --- AQUÍ IRÁ LA MAGIA DE IA (MoviePy v2.0) ---
        # Por ahora, simulamos que tarda 5 segundos en "ver" el video
        print("🤖 IA Analizando video...")
        time.sleep(5) 
        
        # Simulamos que generamos clips dummy
        # (En el futuro aquí llamaremos a tu servicio de IA)
        
        # ----------------------------------------------

        # 3. Finalización Exitosa
        project.status = 'completed'
        project.save()
        
        # 4. Confirmar la Transacción (El dinero se descuenta oficialmente)
        transaction.status = 'completed'
        transaction.save()
        
        print(f"✅ [TASK END] Proyecto {project_id} terminado correctamente.")
        return f"Done project {project_id}"

    except Exception as e:
        print(f"❌ [TASK ERROR] Falló el proyecto {project_id}: {e}")
        
        # Si falla, marcamos error
        if 'project' in locals():
            project.status = 'failed'
            project.save()
        
        # (Opcional) Aquí podrías implementar lógica de reembolso automático
        if 'transaction' in locals():
            transaction.status = 'failed'
            transaction.save()
            
        return f"Error: {e}"