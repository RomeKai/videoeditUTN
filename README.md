# Backend SaaS - Django + PostgreSQL Dockerized

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Django](https://img.shields.io/badge/Django-4.2-green)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-blue)
![Docker](https://img.shields.io/badge/Docker-Compose-orange)

## Descripción

Este proyecto es un backend **Django** para un sistema SaaS, completamente dockerizado.  
Incluye **PostgreSQL** como base de datos y está configurado para facilitar el desarrollo local y la iteración rápida.

---

## Tecnologías

- Python 3.11
- Django 4.x
- PostgreSQL 15
- Docker / Docker Compose
- Bash (entrypoint script)

---

## Comandos principales

# Construir imágenes y levantar contenedores
docker compose up --build

# Levantar contenedores en segundo plano
docker compose up -d

# Parar contenedores
docker compose down

# Acceder al contenedor del backend
docker compose exec backend bash

# Aplicar migraciones manualmente
docker compose exec backend python manage.py migrate

# Crear superusuario de Django
docker compose exec backend python manage.py createsuperuser

# Reconstruir solo el backend
docker compose build backend

# Desarrollo rápido (los cambios se reflejan automáticamente)
docker compose up


## Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto con las siguientes variables:

```env
POSTGRES_DB=mi_base_de_datos
POSTGRES_USER=usuario
POSTGRES_PASSWORD=contraseña
POSTGRES_HOST=db
POSTGRES_PORT=5432
