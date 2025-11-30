# --- ETAPA 1: BASE (Común) ---
FROM python:3.11-slim as base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalamos dependencias del sistema (incluyendo libmagic para validación de archivos)
RUN apt-get update && apt-get install -y \
    libpq-dev gcc netcat-openbsd libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Instalamos primero lo ligero (Web)
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

# 1. Instalamos FFmpeg (Vital para video)
RUN apt-get update && apt-get install -y ffmpeg

# 2. Instalamos las librerías pesadas de IA
COPY ./requirements/ia.txt /app/requirements/ia.txt
RUN pip install --no-cache-dir -r /app/requirements/ia.txt

# Copiamos el código
COPY . /app

# Comando por defecto (Celery Worker)
# IMPORTANTE: Ajustamos '-A backend' porque ahí está tu celery.py
CMD ["celery", "-A", "backend", "worker", "-l", "info"]