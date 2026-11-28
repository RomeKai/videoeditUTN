# 🎬 OneCreator — All-in-One Viral Studio

[![CI](https://github.com/RomeKai/videoeditUTN/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/RomeKai/videoeditUTN/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/License-All%20Rights%20Reserved-red?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.x-092E20?style=flat-square&logo=django&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-37814A?style=flat-square&logo=celery&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat-square&logo=redis&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-Whisper%20%2B%20GPT--4o-412991?style=flat-square&logo=openai&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?style=flat-square&logo=ffmpeg&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![Cloudflare R2](https://img.shields.io/badge/Cloudflare-R2-F38020?style=flat-square&logo=cloudflare&logoColor=white)


**OneCreator** es una plataforma SaaS de edición automatizada de video que utiliza inteligencia artificial para transformar contenido de formato largo en clips virales optimizados para redes sociales.

El sistema analiza un video fuente mediante transcripción (Whisper), detecta momentos de alto impacto con modelos LLM (GPT-4o), aplica face tracking y dynamic cropping en tiempo real, genera subtítulos animados, y exporta clips listos para publicar en TikTok, Instagram Reels y YouTube Shorts.

---

## Tabla de Contenidos

- [Arquitectura del Sistema](#arquitectura-del-sistema)
- [Stack Tecnológico](#stack-tecnológico)
- [Requisitos Previos](#requisitos-previos)
- [Configuración del Entorno](#configuración-del-entorno)
- [Ejecución con Docker](#ejecución-con-docker)
- [Pipeline de Procesamiento](#pipeline-de-procesamiento)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [API Reference](#api-reference)
- [Documentación Técnica](#documentación-técnica)
- [Contribución](#contribución)
- [Licencia](#licencia)

---

## Arquitectura del Sistema

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│   Cliente    │────▶│   API Web   │────▶│   PostgreSQL     │
│  (Frontend)  │     │  (Django)   │     │  (Persistencia)  │
└─────────────┘     └──────┬──────┘     └──────────────────┘
                           │
                    Celery Task
                           │
                    ┌──────▼──────┐     ┌──────────────────┐
                    │   Worker    │────▶│      Redis       │
                    │    (IA)     │     │  (Broker/Cache)  │
                    └──────┬──────┘     └──────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Whisper  │ │  OpenAI  │ │  FFmpeg  │
        │(Transc.) │ │(Selecc.) │ │(Render)  │
        └──────────┘ └──────────┘ └──────────┘
                                        │
                                        ▼
                                ┌──────────────┐
                                │Cloudflare R2 │
                                │ (Storage)    │
                                └──────────────┘
```

El sistema sigue una arquitectura de **monolito modular** con separación clara entre la API web (liviana, responde HTTP) y el worker de IA (pesado, procesa video en background). Ambos comparten el mismo codebase Django pero ejecutan workloads diferentes.

---

## Stack Tecnológico

| Capa | Tecnología | Propósito |
|------|-----------|-----------|
| **Backend** | Django 5.x + DRF | API REST con JWT auth |
| **Task Queue** | Celery + Valkey (compatible con Redis) | Procesamiento asíncrono de video |
| **Base de Datos** | PostgreSQL 15 | Persistencia relacional |
| **Transcripción** | Groq Whisper (con `AI_CORE_V2_ENABLED=True`) o Whisper local | Speech-to-text con word-level timestamps |
| **IA / LLM** | Gemini vía LiteLLM; OpenAI Moderation | Selección de clips, Prompt-to-Edit, textos de las publicaciones y moderación |
| **Frontend** | Next.js 15 + React 19 + TypeScript | La app web (`front/`) |
| **Publicación** | Ayrshare | Conectar redes y publicar |
| **Video Processing** | FFmpeg + MoviePy 2.0 | Proxy generation, rendering, encoding |
| **Face Tracking** | MediaPipe | Detección facial para dynamic cropping |
| **Storage** | Cloudflare R2 (S3-compat.) | Almacenamiento de media con zero egress |
| **Contenedores** | Docker + Docker Compose | Entorno de desarrollo reproducible |
| **Auth** | SimpleJWT | Autenticación stateless por tokens |
| **API Docs** | drf-spectacular (Swagger) | Documentación OpenAPI auto-generada |

---

## Requisitos Previos

| Requisito | Versión mínima | Verificación |
|-----------|---------------|-------------|
| Docker | 24.x | `docker --version` |
| Docker Compose | 2.x (plugin) | `docker compose version` |
| Git | 2.x | `git --version` |

> **No es necesario instalar Python, Redis ni PostgreSQL localmente.** Todo corre dentro de los contenedores Docker.

### Requisitos opcionales (solo si se usa modo local sin Docker)

- Python 3.11+
- FFmpeg instalado en el sistema
- Redis server corriendo
- PostgreSQL 15+

---

## Configuración del Entorno

### 1. Clonar el repositorio

```bash
git clone https://github.com/RomeKai/videoeditUTN.git
cd videoeditUTN
```

### 2. Configurar variables de entorno

```bash
cp back/.env.example back/.env
```

Editar `back/.env` con los valores correspondientes:

```env
# ==========================================
# CORE DJANGO
# ==========================================
DEBUG=True
SECRET_KEY=<generar-una-clave-secreta-unica>

# ==========================================
# BASE DE DATOS (PostgreSQL Docker)
# ==========================================
DATABASE_URL=postgres://admin:admin@db:5432/backend_db

# ==========================================
# INTELIGENCIA ARTIFICIAL & LLMs
# ==========================================
OPENAI_API_KEY=<tu-api-key-de-openai>     # moderación del texto
GEMINI_API_KEY=<tu-api-key-de-gemini>     # elegir los clips y los textos
# GROQ_API_KEY=<tu-api-key-de-groq>       # solo con AI_CORE_V2_ENABLED=True

# ==========================================
# STORAGE
# ==========================================
USE_R2=False
# Completar solo si USE_R2=True:
# CLOUDFLARE_R2_ACCOUNT_ID=
# CLOUDFLARE_R2_ACCESS_KEY_ID=
# CLOUDFLARE_R2_SECRET_ACCESS_KEY=
# CLOUDFLARE_R2_BUCKET_NAME=
```

El archivo completo, con cada variable comentada, es [`back/.env.example`](back/.env.example). Qué hace cada una: [`documents/ia/07`](documents/ia/07-capacidad-costos-y-operacion.md#configuración).

> ⚠️ **Nunca commitear `back/.env`.** El `.gitignore` lo protege, pero verificá que no se filtre.

---

## Ejecución con Docker

> **Para la demo del producto** (backend, web en modo híbrido y datos sembrados en `http://localhost:8080`), seguí [`documents/runbooks/demo.md`](documents/runbooks/demo.md).

### Levantar todo el stack

```bash
# Build inicial (solo la primera vez, o si cambian las dependencias)
docker compose build

# Levantar los 5 servicios en background
docker compose up -d

# Verificar que todo está corriendo
docker compose ps
```

**Servicios esperados:**

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| `db` | 5432 (interno) | PostgreSQL 15 |
| `redis` | 6379 (interno) | Valkey 8, compatible con Redis (broker de Celery, locks y cache) |
| `web` | `localhost:8001` | Django API |
| `worker` | — | Celery worker (procesamiento IA y render) |
| `beat` | — | Celery beat (publicaciones programadas y limpiezas periódicas) |

### Post-instalación (solo primera vez)

```bash
# Aplicar migraciones de base de datos
docker compose exec web python manage.py migrate

# Crear superusuario para acceder al admin
docker compose exec web python manage.py createsuperuser

# (Opcional) Una cuenta con datos de ejemplo; imprime el usuario y la contraseña
docker compose exec web python manage.py seed_demo --with-projects
```

### Endpoints principales

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `http://localhost:8001/api/docs/` | GET | Swagger UI — documentación interactiva |
| `http://localhost:8001/api/token/` | POST | Login — obtener JWT |
| `http://localhost:8001/api/token/refresh/` | POST | Refresh token |
| `http://localhost:8001/api/v1/projects/` | GET/POST | CRUD de proyectos de video |
| `http://localhost:8001/admin/` | GET | Django Admin |

### Comandos de desarrollo frecuentes

```bash
# Ver logs del worker en tiempo real
docker compose logs worker --follow

# Ver logs de la API
docker compose logs web --follow

# Abrir shell de Django
docker compose exec web python manage.py shell

# Los tests corren fuera de Docker, con pytest: ver back/README.md

# Detener todos los servicios
docker compose down

# Detener y eliminar volúmenes (reset completo de DB)
docker compose down -v
```

---

## Pipeline de Procesamiento

Cuando un usuario sube un video, el sistema ejecuta la siguiente secuencia orquestada como una Celery task:

```
POST /api/v1/projects/ (upload)
        │
        ▼
┌─────────────────┐
│ 1. VALIDATION   │  Wallet check → Reserve tokens → Save file
└────────┬────────┘
         ▼
┌─────────────────┐
│ 2. STORAGE      │  Upload original a R2 (o bypass local si USE_R2=False)
└────────┬────────┘
         ▼
┌─────────────────┐
│ 3. PROXY        │  FFmpeg → 480p web proxy para preview rápido
└────────┬────────┘
         ▼
┌─────────────────┐
│ 4. TRANSCRIBE   │  Whisper (local, CPU) → word-level timestamps
└────────┬────────┘
         ▼
┌─────────────────┐
│ 5. AI SELECTION │  GPT-4o-mini → detect viral moments → JSON clips
└────────┬────────┘
         ▼
┌─────────────────┐
│ 6. APPROVAL     │  status = awaiting_approval (el usuario revisa clips)
└────────┬────────┘
         ▼ (usuario aprueba)
┌─────────────────┐
│ 7. RENDER       │  MoviePy 2.0 + FFmpeg → crop, subtitles, face tracking
└────────┬────────┘
         ▼
┌─────────────────┐
│ 8. EXPORT       │  Upload final a R2 → status = completed
└─────────────────┘
```

---

## Estructura del Proyecto

```
videoeditUTN/
├── LICENSE                          # Licencia restrictiva (All Rights Reserved)
├── CONTRIBUTING.md                  # Guía de contribución + CLA
├── README.md                        # ← Estás aquí
├── docker-compose.yml               # Orquestación: db, redis, web, worker
│
├── back/                            # Backend Django
│   ├── Dockerfile                   # Multi-stage: web + worker
│   ├── manage.py
│   ├── .env                         # Variables de entorno (NO commitear)
│   ├── requirements/
│   │   ├── base.txt                 # Django, DRF, Celery, boto3, MoviePy
│   │   ├── dev.txt                  # Debug toolbar, testing tools
│   │   └── ia.txt                   # Whisper, OpenAI, MediaPipe, OpenCV
│   │
│   ├── backend/                     # Configuración central Django
│   │   ├── settings/
│   │   │   ├── base.py              # Settings comunes (env vars)
│   │   │   ├── dev.py               # DEBUG=True, CORS permissive
│   │   │   └── prod.py              # DEBUG=False, security hardened
│   │   ├── celery.py                # Configuración de Celery + Redis
│   │   ├── urls.py                  # Router principal (JWT, API v1, Swagger)
│   │   └── wsgi.py / asgi.py
│   │
│   └── apps/                        # Módulos de dominio
│       ├── core/                    # Excepciones, permisos, pricing, mixins
│       ├── users/                   # User, Workspace, WorkspaceMember
│       ├── videos/                  # VideoProject, VideoClip, BrandKit
│       │   ├── services/
│       │   │   ├── transcription_engine.py   # Whisper (local)
│       │   │   ├── selection_engine.py       # GPT-4o clip selection
│       │   │   ├── render_engine.py          # MoviePy 2.0 render pipeline
│       │   │   ├── storage_service.py        # Cloudflare R2 (USE_R2 guard)
│       │   │   ├── face_tracker.py           # MediaPipe face detection
│       │   │   ├── subtitle_engine.py        # Dynamic subtitle generation
│       │   │   └── layouts/                  # Layout strategies (fill, split, PiP)
│       │   ├── tasks/                        # Celery tasks (names stay apps.videos.tasks.<name>)
│       │   │   ├── ingestion.py              # Ingestion pipeline + Prompt-to-Edit parsing
│       │   │   ├── render.py                 # Clip render and paper-edit render
│       │   │   ├── social.py                 # Publishing and AI social metadata (SEO)
│       │   │   └── shared.py                 # Retry policy, metadata, usage, AI capacity
│       │   └── utils/
│       │       └── ffmpeg_utils.py           # Proxy generation, codec ops
│       ├── payments/                # Wallet, Transaction, SubscriptionPlan
│       │   └── services/
│       │       ├── wallet_service.py         # Transactional reserve/commit/refund
│       │       ├── pricing_engine.py         # Token cost calculation
│       │       └── stripe_service.py         # Stripe billing integration
│       ├── ia/                      # Embeddings, VAD, audio features, scoring
│       └── integrations/            # Ayrshare, YouTube, Twitch APIs
│
└── documents/                       # Documentación técnica
    ├── ia/                          # Core de IA: un documento por flujo
    ├── producto/                    # Mapa de pantallas y sistema visual
    ├── integracion/                 # Guía para conectar la web con el backend
    ├── adrs/                        # Decisiones de arquitectura
    └── runbooks/                    # Operación del piloto y la demo del producto
```

---

## API Reference

La documentación interactiva de la API está disponible en:

- **Swagger UI:** [`http://localhost:8001/api/docs/`](http://localhost:8001/api/docs/)
- **ReDoc:** [`http://localhost:8001/api/redoc/`](http://localhost:8001/api/redoc/)
- **OpenAPI Schema:** [`http://localhost:8001/api/schema/`](http://localhost:8001/api/schema/)

### Ejemplo: Subir un video y procesar

```bash
# 1. Obtener token JWT
TOKEN=$(curl -s -X POST http://localhost:8001/api/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"tu@email.com","password":"tu-password"}' \
  | jq -r '.access')

# 2. Subir video para procesamiento
curl -X POST http://localhost:8001/api/v1/projects/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "source_file=@mi_video.mp4" \
  -F "title=Mi Primer Proyecto" \
  -F "intelligence_level=fast" \
  -F "aspect_ratio=9:16" \
  -F "add_subtitles=true"
```

---

## Documentación Técnica

La documentación detallada está en el directorio [`documents/`](./documents/):

| Documento | Descripción |
|-----------|-------------|
| [Índice de la documentación](documents/README.md) | Qué hay en `documents/` y dónde está cada tema |
| [Runbook de la demo](documents/runbooks/demo.md) | Levantar la demo, el recorrido, la lista de antes de presentar y qué hacer si algo falla |
| [IA en OneCreator](documents/ia/README.md) | Arquitectura del core de IA y un documento por flujo: creación de proyecto, ingesta, transcripción, moderación y selección, Prompt-to-Edit, SEO y publicación, render, capacidad y costos |
| [Mapa de pantallas](documents/producto/mapa-de-pantallas.md) | Qué es cada pantalla, roles, planes, errores y los huecos de backend |
| [Sistema visual](documents/producto/diseno/README.md) | Tokens y componentes con los que están dibujadas las pantallas |
| [Guía para conectar la web con el backend](documents/integracion/guia-conexion.md) | El contrato, las reglas del backend y la lista para dar un área por conectada |
| [Decisiones de arquitectura](documents/adrs/README.md) | Los ADRs |
| [Backend](back/README.md) y [frontend](front/README.md) | Cómo correr cada parte y sus tests |

---

## Contribución

Las contribuciones están reguladas por el [Acuerdo de Contribución (CLA)](./CONTRIBUTING.md). Leé el documento completo antes de enviar código.

**Resumen:** Todo código enviado mediante Pull Request se cede al autor original para uso comercial. Los contribuyentes conservan el derecho moral de autoría y reconocimiento académico.

Para guía detallada sobre workflow, convenciones y proceso de PR, consultá [`CONTRIBUTING.md`](./CONTRIBUTING.md).

---

## Licencia

**All Rights Reserved.** Este software es propiedad exclusiva de Romeo Lorenzo Monfroglio. No se permite la copia, modificación, distribución ni uso comercial sin autorización expresa por escrito. Ver [`LICENSE`](./LICENSE) para los términos completos.

---


> ⚠️ **Aviso de Propiedad Intelectual y Contexto Académico**
>
> Este repositorio es un entorno de desarrollo creado como trabajo práctico para la **Universidad Tecnológica Nacional (UTN)**. La arquitectura base, el modelo de negocio y el núcleo del motor de video son propiedad intelectual exclusiva de **Romeo Lorenzo Monfroglio**. Las contribuciones realizadas por el equipo de desarrollo durante este ciclo académico están sujetas a los términos detallados en [`CONTRIBUTING.md`](./CONTRIBUTING.md). Consultar [`LICENSE`](./LICENSE) para más información.

---

<div align="center">
  <sub>Desarrollado por <strong>Romeo Lorenzo Monfroglio</strong> — UTN 2026</sub>
</div>

