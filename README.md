# 🎬 OneCreator — All-in-One Viral Studio

> ⚠️ **Aviso de Propiedad Intelectual y Contexto Académico**
>
> Este repositorio es un entorno de desarrollo creado como trabajo práctico para la **Universidad Tecnológica Nacional (UTN)**. La arquitectura base, el modelo de negocio y el núcleo del motor de video son propiedad intelectual exclusiva de **Romeo Lorenzo Monfroglio**. Las contribuciones realizadas por el equipo de desarrollo durante este ciclo académico están sujetas a los términos detallados en [`CONTRIBUTING.md`](./CONTRIBUTING.md). Consultar [`LICENSE`](./LICENSE) para más información.

---

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
| **Task Queue** | Celery + Redis | Procesamiento asíncrono de video |
| **Base de Datos** | PostgreSQL 15 | Persistencia relacional |
| **Transcripción** | OpenAI Whisper (local) | Speech-to-text con word-level timestamps |
| **IA / LLM** | OpenAI GPT-4o-mini/4o | Detección de momentos virales |
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
OPENAI_API_KEY=<tu-api-key-de-openai>

# ==========================================
# STORAGE
# ==========================================
USE_S3=False
# Completar solo si USE_S3=True:
# CLOUDFLARE_R2_ACCOUNT_ID=
# CLOUDFLARE_R2_ACCESS_KEY_ID=
# CLOUDFLARE_R2_SECRET_ACCESS_KEY=
# CLOUDFLARE_R2_BUCKET_NAME=
```

> ⚠️ **Nunca commitear `back/.env`.** El `.gitignore` lo protege, pero verificá que no se filtre.

---

## Ejecución con Docker

### Levantar todo el stack

```bash
# Build inicial (solo la primera vez, o si cambian las dependencias)
docker compose build

# Levantar los 4 servicios en background
docker compose up -d

# Verificar que todo está corriendo
docker compose ps
```

**Servicios esperados:**

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| `db` | 5432 (interno) | PostgreSQL 15 |
| `redis` | 6379 (interno) | Redis 7 (Celery broker) |
| `web` | `localhost:8001` | Django API |
| `worker` | — | Celery worker (procesamiento IA) |

### Post-instalación (solo primera vez)

```bash
# Aplicar migraciones de base de datos
docker compose exec web python manage.py migrate

# Crear superusuario para acceder al admin
docker compose exec web python manage.py createsuperuser

# (Opcional) Cargar datos de prueba
docker compose exec web python manage.py shell < scripts/seed_data.py
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

# Correr tests
docker compose exec web python manage.py test

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
│ 2. STORAGE      │  Upload original a R2 (o bypass local si USE_S3=False)
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
│       │   │   ├── storage_service.py        # Cloudflare R2 (USE_S3 guard)
│       │   │   ├── face_tracker.py           # MediaPipe face detection
│       │   │   ├── subtitle_engine.py        # Dynamic subtitle generation
│       │   │   └── layouts/                  # Layout strategies (fill, split, PiP)
│       │   ├── tasks.py                      # Celery task orchestration
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
└── docs/                            # Documentación técnica
    ├── architecture/overview.md     # Arquitectura del sistema
    ├── business/payments.md         # Modelo de pagos y wallet
    ├── ia/engines.md                # Engines de IA y scoring
    ├── video/render_engine.md       # Motor de render y layouts
    ├── deploy/infrastructure.md     # Infraestructura de deploy
    └── integrations/social_auth.md  # Integración con redes sociales
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

Documentación detallada disponible en el directorio [`docs/`](./docs/):

| Documento | Descripción |
|-----------|-------------|
| [Arquitectura del Sistema](docs/architecture/overview.md) | Componentes, flujo de datos, decisiones de diseño |
| [Motores de IA](docs/ia/engines.md) | Whisper, scoring models, VAD, embeddings |
| [Motor de Render](docs/video/render_engine.md) | Pipeline de render, layouts, subtítulos |
| [Pagos y Wallet](docs/business/payments.md) | Modelo transaccional, pricing, Stripe |
| [Infraestructura](docs/deploy/infrastructure.md) | Docker, PostgreSQL, Redis, R2 |
| [Integraciones](docs/integrations/social_auth.md) | Ayrshare, YouTube, Twitch |

---

## Contribución

Las contribuciones están reguladas por el [Acuerdo de Contribución (CLA)](./CONTRIBUTING.md). Leé el documento completo antes de enviar código.

**Resumen:** Todo código enviado mediante Pull Request se cede al autor original para uso comercial. Los contribuyentes conservan el derecho moral de autoría y reconocimiento académico.

Para guía detallada sobre workflow, convenciones y proceso de PR, consultá [`CONTRIBUTING.md`](./CONTRIBUTING.md).

---

## Licencia

**All Rights Reserved.** Este software es propiedad exclusiva de Romeo Lorenzo Monfroglio. No se permite la copia, modificación, distribución ni uso comercial sin autorización expresa por escrito. Ver [`LICENSE`](./LICENSE) para los términos completos.

---

<div align="center">
  <sub>Desarrollado por <strong>Romeo Lorenzo Monfroglio</strong> — UTN 2026</sub>
</div>
