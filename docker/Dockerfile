# Imagen base
FROM python:3.11-slim

# Directorio de trabajo dentro del contenedor
WORKDIR /app

# Instalar dependencias de sistema necesarias (netcat para entrypoint)
RUN apt-get update && apt-get install -y netcat-openbsd && rm -rf /var/lib/apt/lists/*

# Copiar dependencias desde el directorio padre
COPY ../requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el código del proyecto (desde la raíz del repo)
COPY ../ .

# Exponer el puerto de Django
EXPOSE 8000

# Comando por defecto (entrypoint será sobreescrito por docker-compose)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
