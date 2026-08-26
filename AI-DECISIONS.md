# AI-DECISIONS.md — Registro de Decisiones Asistidas por IA

> Archivo de auditoría obligatorio para la cátedra de Ingeniería de Software en la Nube (UTN FRLP).
> Documenta cada instancia donde se utilizó IA generativa en el desarrollo de este proyecto.

---

# Fase 0 — Desarrollo Fundacional (Pre-Académico)

> Las siguientes entradas documentan el trabajo realizado por el autor original del proyecto utilizando asistentes de IA como herramienta de ingeniería, **previo** a la propuesta del repositorio como trabajo práctico de cátedra. El objetivo fue construir la arquitectura base, el pipeline de procesamiento de video y los motores de IA que constituyen el core del producto.

---

## Entrada #1 — Arquitectura Base y Modelos de Datos

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-05 (desarrollo inicial) |
| **Artefactos** | `models.py` (users, videos, payments), `settings/base.py`, `Dockerfile`, `docker-compose.yml` |
| **Herramienta IA** | Claude (Anthropic) + GPT-4 (OpenAI) |
| **Qué hizo la IA** | Asistió en el diseño de la arquitectura del monolito modular Django: estructura de apps por dominio (users, videos, payments, integrations, ia, core), diseño de modelos con UUIDs como PK, TextChoices para enums, y configuración multi-entorno (base/dev/prod). |
| **Qué hizo el desarrollador** | Definió los dominios de negocio, las relaciones entre entidades, los requerimientos funcionales del producto, y tomó todas las decisiones de diseño (multi-tenant con Workspaces, JWT stateless, Celery como task queue). |
| **Justificación** | Acelerar la traducción de decisiones arquitectónicas a código Django idiomático, reduciendo el tiempo de scaffolding sin delegar las decisiones de diseño. |

---

## Entrada #2 — Pipeline de Video: Transcripción con Whisper

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-06 |
| **Artefactos** | `services/transcription_engine.py`, `requirements/ia.txt` |
| **Herramienta IA** | Claude (Anthropic) |
| **Qué hizo la IA** | Asistió en la integración de OpenAI Whisper en modo local (CPU), con word-level timestamps y agrupación de palabras en segmentos configurables. |
| **Qué hizo el desarrollador** | Decidió usar Whisper local en vez de la API de OpenAI para eliminar costos por minuto de transcripción. Configuró el modelo `base` como default con fallback a `tiny` en tests. Definió la interfaz `group_words()` para alimentar el motor de subtítulos. |
| **Justificación** | La transcripción es el paso más crítico del pipeline y el más costoso si se usa vía API. La IA aceleró la comprensión de la API de Whisper y la implementación del word-level timestamping. |

---

## Entrada #3 — Pipeline de Video: SelectionEngine con Strategy Pattern

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-06 |
| **Artefactos** | `services/selection_engine.py` |
| **Herramienta IA** | Claude (Anthropic) |
| **Qué hizo la IA** | Asistió en la implementación del patrón Strategy para abstraer el modelo de IA (GPT-4o-mini vs GPT-4o) detrás de una interfaz `ClipSelectionStrategy`, con prompt engineering para detección de momentos virales y JSON Structured Outputs. |
| **Qué hizo el desarrollador** | Diseñó la arquitectura Strategy, definió los criterios de selección viral, escribió los prompts de sistema, y decidió usar `json_object` response format para garantizar parseo determinista. |
| **Justificación** | El patrón Strategy fue una decisión de diseño del desarrollador; la IA aceleró la implementación concreta de cada strategy y el boilerplate de la API de OpenAI. |

---

## Entrada #4 — Motor de Render y Layouts

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-06 – 2026-07 |
| **Artefactos** | `services/render_engine.py`, `services/layouts/` (standard, blur_pip, gaming, pro), `utils/moviepy_utils.py` |
| **Herramienta IA** | Claude (Anthropic) + Gentle-AI Factory |
| **Qué hizo la IA** | Asistió en la implementación de los 6 layouts de renderizado (fill, fit, blurred, split/gaming, versus, active speaker) usando MoviePy 2.0, incluyendo la corrección de dimensiones pares para H.264 y la integración con el face tracker. |
| **Qué hizo el desarrollador** | Definió qué layouts implementar, diseñó la interfaz `LayoutStrategy`, decidió la separación en archivos por layout, y realizó debugging extensivo de compatibilidad con MoviePy 2.0 (API breaking changes respecto a 1.x). |
| **Justificación** | MoviePy 2.0 tiene documentación escasa y cambios de API significativos. La IA aceleró la adaptación del código a la nueva API, pero el debugging de renders corruptos fue trabajo manual del desarrollador. |

---

## Entrada #5 — Face Tracking con MediaPipe

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-06 – 2026-07 |
| **Artefactos** | `services/face_tracker.py`, `services/tracking_utils.py`, `services/saliency_tracker.py` |
| **Herramienta IA** | Claude (Anthropic) |
| **Qué hizo la IA** | Asistió en la integración de MediaPipe Tasks API para detección facial, implementación del smoothing por media móvil, y el sistema de TrackingPath/TrackingPoint para suavizar la trayectoria del crop. |
| **Qué hizo el desarrollador** | Decidió usar MediaPipe sobre alternativas (dlib, YOLO-face) por su bajo peso y ejecución sin GPU. Diseñó el sistema de saliency para gaming layouts. Iteró manualmente sobre parámetros de zoom y centrado vertical hasta lograr framing aceptable para streamers. |
| **Justificación** | La calibración visual del face tracking requirió iteración humana que la IA no puede hacer (evaluar si el encuadre "se ve bien"). La IA aceleró el scaffolding de la integración con MediaPipe. |

---

## Entrada #6 — Motor de Subtítulos (SubtitleEngine)

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-07 |
| **Artefactos** | `services/subtitle_engine.py` |
| **Herramienta IA** | Claude (Anthropic) + Gentle-AI Factory |
| **Qué hizo la IA** | Asistió en la implementación del motor de subtítulos word-level con estilos configurables, soporte de emoji, word wrapping multi-línea, y migración del rendering de MoviePy TextClip a Pillow para mayor confiabilidad. Pasó por múltiples iteraciones (V1 con TextClip, V2 con Karaoke/Pop, versión final con Pillow). |
| **Qué hizo el desarrollador** | Definió los estilos de subtítulos (tamaño, color, posición), rechazó las primeras versiones por overflow visual, y dirigió la migración a Pillow cuando TextClip demostró ser inestable en Docker headless. |
| **Justificación** | El subtitle engine pasó por 5+ iteraciones. La IA generó cada versión; el desarrollador evaluó visualmente los renders y rechazó las que no cumplían el estándar de calidad. |

---

## Entrada #7 — Sistema Financiero: PricingEngine + WalletService

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-07 |
| **Artefactos** | `payments/services/pricing_engine.py`, `payments/services/wallet_service.py`, `payments/models.py` |
| **Herramienta IA** | Claude (Anthropic) |
| **Qué hizo la IA** | Asistió en la implementación del sistema transaccional: wallet con balance available/reserved, operaciones atómicas con `select_for_update()` para prevenir race conditions, PricingEngine V3 con multiplicadores por resolución y feature add-ons, y SubscriptionPlan con feature flags. |
| **Qué hizo el desarrollador** | Diseñó el modelo económico completo (unit economics: costo base por segundo a 720p, multiplicadores), definió los planes de suscripción y sus límites, y estableció el flujo RESERVE → COMMIT/ROLLBACK como patrón transaccional. |
| **Justificación** | El sistema financiero requiere integridad transaccional rigurosa. La IA implementó el patrón técnico (`select_for_update`, `@transaction.atomic`); el desarrollador definió las reglas de negocio y validó la integridad con tests de stress. |

---

## Entrada #8 — Storage: Migración de AWS S3 a Cloudflare R2

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-07 |
| **Artefactos** | `services/storage_service.py`, `settings/base.py` |
| **Herramienta IA** | Claude (Anthropic) |
| **Qué hizo la IA** | Asistió en la migración del cliente S3 (boto3) de AWS a Cloudflare R2, implementación del guard `USE_S3=False` para desarrollo local sin credenciales cloud, y refactorización de todos los uploads del pipeline para usar el nuevo `CloudflareR2Manager`. |
| **Qué hizo el desarrollador** | Decidió migrar a R2 por zero egress fees (decisión de costos operativos), configuró las variables de entorno, y validó que el bypass local funcionara end-to-end en el dry-run del pipeline. |
| **Justificación** | Decisión de infraestructura cloud con impacto directo en unit economics. La IA aceleró la re-implementación del cliente S3; la decisión estratégica fue del desarrollador. |

---

## Entrada #9 — Seguridad: AI Security Shield + SEO Engine

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-07 – 2026-08 |
| **Artefactos** | `core/security.py`, `services/seo_engine.py` |
| **Herramienta IA** | Claude (Anthropic) |
| **Qué hizo la IA** | Asistió en la implementación del `AI_Security_Shield` para aislamiento de input de usuario en prompts LLM (prevención de prompt injection), y del `SEOOptimizationService` con Structured Outputs de OpenAI para generación de metadata social (hashtags, captions, hora óptima de publicación). |
| **Qué hizo el desarrollador** | Identificó prompt injection como vector de ataque crítico para un SaaS que acepta input de usuario y lo pasa a LLMs. Definió la estrategia de aislamiento con tags `<user_input>`. Diseñó el schema Pydantic de `SocialMetadataResponse`. |
| **Justificación** | La seguridad de prompts no es una funcionalidad visible pero es crítica para un SaaS que procesa contenido de terceros. La IA implementó las técnicas; el desarrollador identificó los vectores de riesgo. |

---

## Entrada #10 — Sanitización del Repositorio y Preparación para Equipo

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-08-16 a 2026-08-22 |
| **Artefactos** | `.gitignore`, `docker-compose.yml`, `serializers.py`, `admin.py`, `views.py`, migraciones, `README.md`, `CONTRIBUTING.md`, `LICENSE`, `.env.example` |
| **Herramienta IA** | Claude Opus 4.6 (Anthropic) vía Antigravity IDE |
| **Qué hizo la IA** | Auditoría completa del repositorio: detección de secretos hardcodeados, imports rotos, endpoints inexistentes. Corrección de 5 bugs encontrados durante dry-run del pipeline con `clash.mp4`. Creación de documentación del repositorio (README, CONTRIBUTING con CLA, LICENSE). Generación de `.env.example` con placeholders seguros. |
| **Qué hizo el desarrollador** | Autorizó cada fix individualmente, tomó las decisiones sobre el endpoint `prompt_edit` (eliminar vs stub), decidió el esquema de licenciamiento (All Rights Reserved), definió los términos del CLA, y movió el disclaimer de IP al footer del README. |
| **Justificación** | El repositorio necesitaba ser seguro y profesional antes de compartirlo con un equipo de 4 desarrolladores. La IA realizó el scan exhaustivo y las correcciones mecánicas; el desarrollador tomó cada decisión de negocio y legal. |

---

# Fase 1 — Entregables Académicos

> Las siguientes entradas documentan el uso de IA para producir entregables de la cátedra de Ingeniería de Software en la Nube.

---

## Entrada #11 — One-Pager: Propuesta de Proyecto (Clase 1)

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-08-25 |
| **Entregable** | `documents/entregas/tp1.md` |
| **Herramienta IA** | Claude Opus 4.6 (Anthropic) vía Antigravity IDE |
| **Qué hizo la IA** | Generó el documento one-pager con las 5 secciones requeridas (nombre, problema, propuesta de valor, usuarios, stack). Primera versión fue excesivamente extensa (~214 líneas); se solicitó condensación a una página. El stack fue extraído del código fuente real del repositorio. |
| **Qué hizo el equipo** | Definió los requerimientos del entregable según la consigna de la cátedra, revisó que las tecnologías listadas coincidieran con el código real, y solicitó la versión condensada. |
| **Justificación** | La IA sintetizó información técnica dispersa en múltiples archivos del repositorio en un formato académico coherente. El equipo aportó la visión del producto y la verificación. |

---

## Entrada #12 — Backlog MVP v2

| Campo | Detalle |
|-------|---------|
| **Fecha** | 2026-08-22 a 2026-08-23 |
| **Entregable** | `documents/backlog_mvp.md` |
| **Herramienta IA** | Claude Opus 4.6 (Anthropic) vía Antigravity IDE |
| **Qué hizo la IA** | Analizó el código fuente existente (models.py, services/, tasks.py, views.py) para determinar el estado real de cada feature (exists/partial/missing). Generó 24 issues con criterios de aceptación, priorización P0/P1/P2, roadmap de 3 meses y asignación sugerida por dev. |
| **Qué hizo el equipo** | Definió las prioridades estratégicas (core antes que frontend), la decisión de construir publicación directa sin Ayrshare, y los requerimientos específicos de Prompt-to-Edit, nuevos layouts, y optimización de performance. |
| **Justificación** | La IA mapeó el gap entre código existente y funcionalidades requeridas. El equipo definió la estrategia de producto y las prioridades de negocio. |
