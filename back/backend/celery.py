from __future__ import annotations
import os  # <--- 1. Te faltaba importar os
from celery import Celery

# 2. ESTA LÍNEA ES LA QUE SOLUCIONA TU ERROR
# Le dice a Celery qué configuración usar por defecto si no se especifica otra.
# Al tener carpeta settings, apuntamos a 'dev' o 'base' (ajusta según cual usas localmente).
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings.dev')

app = Celery('backend')

# Forced Fix for Docker: Priority to Env Var
redis_url = os.getenv('REDIS_URL', 'redis://redis:6379/0')
app.conf.update(
    broker_url=redis_url,
    result_backend=redis_url,
)

# Lee la configuración desde el archivo de settings de Django
app.config_from_object('django.conf:settings', namespace='CELERY')

# Busca tareas en todas las apps instaladas
app.autodiscover_tasks()