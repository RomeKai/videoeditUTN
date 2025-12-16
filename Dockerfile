# --- ETAPA 1: BASE (Común) ---
FROM python:3.11-slim as base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# CAMBIO CRÍTICO AQUI: Agregamos 'ffmpeg' a la lista de instalaciones base.
# Ahora tanto la Web (para cobrar) como el Worker (para editar) tendrán FFmpeg.
RUN apt-get update && apt-get install -y \
    libpq-dev gcc netcat-openbsd libmagic1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Instalamos primero lo ligero (Web)
# Asegúrate de que 'moviepy' esté dentro de este base.txt
COPY ./requirements/base.txt /app/requirements/base.txt
RUN pip install --no-cache-dir -r /app/requirements/base.txt

# --- ETAPA 2: WEB (Ligera) ---
FROM base as web
# Copiamos el código
COPY . /app
# Exponemos el puerto
EXPOSE 8000
# Comando por defecto (Dev server)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

# --- ETAPA 3: WORKER (Pesada - IA) ---
FROM base as worker

# NOTA: Ya no instalamos ffmpeg aquí porque lo hereda de 'base'

# 2. Instalamos las librerías pesadas de IA (Torch, etc.)
COPY ./requirements/ia.txt /app/requirements/ia.txt
RUN pip install --no-cache-dir -r /app/requirements/ia.txt

# Copiamos el código
COPY . /app

# Comando por defecto (Celery Worker)
CMD ["celery", "-A", "backend", "worker", "-l", "info"]