# --- ETAPA 1: BASE (Común) ---
FROM python:3.11-slim as base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# CAMBIO APLICADO: Agregamos librerías gráficas para MediaPipe/OpenCV
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    netcat-openbsd \
    libmagic1 \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    imagemagick \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Configuración de seguridad de ImageMagick (a veces necesaria para permitir texto)
RUN find /etc/ImageMagick*/ -name "policy.xml" -exec sed -i 's/none/read,write/g' {} \;

COPY ./requirements/base.txt /app/requirements/base.txt
COPY ./requirements/ia.txt /app/requirements/ia.txt
COPY ./requirements/constraints-ci.txt /app/requirements/constraints-ci.txt

RUN pip install --no-cache-dir --upgrade \
    --constraint /app/requirements/constraints-ci.txt \
    pip setuptools wheel

# 1. INSTALACIÓN CRÍTICA: PyTorch CPU en base para consistencia
RUN pip install --no-cache-dir \
    --constraint /app/requirements/constraints-ci.txt \
    torch torchaudio torchvision \
    --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    --constraint /app/requirements/constraints-ci.txt \
    --requirement /app/requirements/base.txt
RUN pip install --no-cache-dir \
    --constraint /app/requirements/constraints-ci.txt \
    --requirement /app/requirements/ia.txt

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
# Ya tiene todo lo de base instalado
# Copiamos el código
COPY . /app

# Comando por defecto
CMD ["celery", "-A", "backend", "worker", "-l", "info"]