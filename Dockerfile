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

# ... (Etapas base y web siguen igual) ...

# --- ETAPA 3: WORKER (Pesada - IA) ---
FROM base as worker

# 1. INSTALACIÓN CRÍTICA: PyTorch CPU
# Lo hacemos manual y PRIMERO para asegurar que sea la versión ligera (~200MB)
# y no la versión GPU de PyPI (~3GB).
RUN pip install --no-cache-dir torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu

# 2. Instalamos el resto de librerías de IA (Whisper, MoviePy, etc.)
COPY ./requirements/ia.txt /app/requirements/ia.txt
RUN pip install --no-cache-dir -r /app/requirements/ia.txt

# Copiamos el código
COPY . /app

# Comando por defecto
CMD ["celery", "-A", "backend", "worker", "-l", "info"]