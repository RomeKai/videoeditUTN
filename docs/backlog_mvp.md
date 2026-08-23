# 📋 OneCreator — MVP Backlog

> **Proyecto:** OneCreator — All-in-One Viral Studio
> **Equipo:** Dev 1 – Dev 5
> **Sprint Goal:** MVP funcional end-to-end para demostración académica (UTN)

---

## Leyenda

| Etiqueta | Significado |
|----------|-------------|
| 🟢 `exists` | Código funcional ya presente en el repositorio |
| 🟡 `partial` | Modelo/servicio existe pero incompleto o sin endpoint |
| 🔴 `missing` | No existe — hay que construirlo desde cero |
| `P0` | Must-have para el MVP |
| `P1` | Should-have (mejora significativa) |
| `P2` | Nice-to-have (post-MVP) |

---

## Módulo 1 — Edición e Inteligencia Artificial

### EDIT-01: Recorte Inteligente de Mejores Momentos `P0` 🟢 `exists`

**Descripción:**
El sistema analiza un video largo usando Whisper (transcripción) + GPT-4o-mini (selección) y propone clips virales con timestamps y justificación. El pipeline completo ya existe en `tasks.py` → `TranscriptionEngine` → `SelectionEngine`.

**Estado actual:** Pipeline funcional (validado con `clash.mp4`). Falla solo si no hay `OPENAI_API_KEY`.

**Criterios de Aceptación:**
- [x] El usuario sube un video vía `POST /api/v1/projects/`.
- [x] El worker transcribe con Whisper (local, sin API key).
- [x] GPT-4o-mini selecciona clips y devuelve JSON estructurado.
- [x] El proyecto pasa a `awaiting_approval` con clips propuestos.
- [ ] El usuario puede revisar, aceptar o rechazar cada clip propuesto.

---

### EDIT-02: Subtítulos Dinámicos con IA `P0` 🟢 `exists`

**Descripción:**
Generación de subtítulos animados word-level usando datos de Whisper. El `SubtitleEngine` existe y está integrado en el render pipeline.

**Estado actual:** Motor funcional en `subtitle_engine.py`. Configuración por proyecto: color, tamaño, posición, palabras por segmento.

**Criterios de Aceptación:**
- [x] Subtítulos generados automáticamente desde la transcripción Whisper.
- [x] Configurables por proyecto: `subtitle_color`, `subtitle_size`, `subtitle_position`, `subtitle_words_per_segment`.
- [ ] Preview visual de subtítulos antes del render final.

---

### EDIT-03: Traducción de Subtítulos con IA `P1` 🔴 `missing`

**Descripción:**
Traducir subtítulos generados a otros idiomas usando GPT-4o-mini. Extender `SubtitleEngine` para aceptar un `target_language` y traducir el transcript antes de renderizar.

**Criterios de Aceptación:**
- [ ] Endpoint para solicitar traducción a un idioma específico (es, en, pt, fr mínimo).
- [ ] La traducción preserva los timestamps word-level originales.
- [ ] El clip renderizado usa los subtítulos traducidos.
- [ ] Si la traducción falla, el sistema usa los subtítulos originales como fallback.

---

### EDIT-04: Edición Fija con Marca de Agua (Plan Gratuito) `P0` 🟡 `partial`

**Descripción:**
Los usuarios del plan gratuito pueden editar videos pero el resultado lleva una marca de agua del producto. El modelo `BrandKit.watermark_logo` existe, el `SubscriptionPlan.has_watermark` existe, pero la lógica de inyección de watermark en el render pipeline no está implementada.

**Criterios de Aceptación:**
- [ ] Si el plan del workspace tiene `has_watermark=True`, el render inyecta la marca de agua de OneCreator.
- [ ] El watermark es semi-transparente, posición esquina inferior derecha.
- [ ] Usuarios de planes pagos (`has_watermark=False`) no ven marca de agua.
- [ ] El watermark no se puede quitar manipulando la API (validación server-side).

---

### EDIT-05: Estilos de Edición Predefinidos `P0` 🟢 `exists`

**Descripción:**
Múltiples estilos de edición definidos como `EditingStyle` en el modelo (`dynamic`, `minimalist`, `vlog`, `hormozi`, `custom`). Cada estilo afecta el prompt que se envía al LLM para seleccionar clips.

**Estado actual:** Enums definidos. `SelectionEngine` los consume en el prompt.

**Criterios de Aceptación:**
- [x] El usuario selecciona un estilo al crear el proyecto.
- [x] El `SelectionEngine` adapta su prompt según el estilo elegido.
- [ ] Documentar qué produce cada estilo (diferencias observables en la selección de clips).

---

### EDIT-06: Personalizar Estilo de Edición Recurrente (BrandKit) `P1` 🟡 `partial`

**Descripción:**
Permitir al usuario guardar un `BrandKit` con colores, fuentes, logos y preferencias de edición que se apliquen automáticamente a cada proyecto. El modelo existe completo; faltan endpoints CRUD y la integración con el render engine.

**Criterios de Aceptación:**
- [ ] CRUD completo de BrandKit: `POST/GET/PATCH/DELETE /api/v1/brand-kits/`.
- [ ] Al crear un proyecto, se puede asociar un `brand_kit_id`.
- [ ] El render engine aplica `primary_color`, `watermark_logo`, `intro_video`, `outro_video` del BrandKit.
- [ ] Un BrandKit marcado `is_default=True` se aplica automáticamente si no se especifica otro.

---

### EDIT-07: IA Explicable — Rationale Log `P1` 🟡 `partial`

**Descripción:**
El campo `ai_rationale_log` existe en `VideoProject` (JSONField). El `SelectionEngine` devuelve `ai_reasoning` por clip. Falta exponerlo al frontend de manera legible.

**Criterios de Aceptación:**
- [ ] El endpoint `GET /api/v1/projects/{id}/` incluye `ai_rationale_log` con explicación por clip.
- [ ] Cada clip propuesto tiene: `reason` (por qué se seleccionó), `virality_score`, `timestamps`.
- [ ] El usuario puede dar feedback (thumbs up/down) por clip para retroalimentación futura.

---

### EDIT-08: Retroalimentación y Ajustes Finos del Usuario `P2` 🔴 `missing`

**Descripción:**
Permitir al usuario ajustar los timestamps de un clip propuesto (mover inicio/fin), agregar/quitar clips antes de renderizar. La aprobación de segmentos existe como campo (`approved_segments`) pero no hay endpoint para editarlos.

**Criterios de Aceptación:**
- [ ] Endpoint `PATCH /api/v1/projects/{id}/approve/` que acepta un array de segmentos editados.
- [ ] El usuario puede mover `start_time` / `end_time` de cada clip dentro de ±10s.
- [ ] El usuario puede descartar clips individuales de la propuesta.
- [ ] Solo clips aprobados avanzan al render.

---

### EDIT-09: Layouts de Video (Dynamic Cropping) `P0` 🟢 `exists`

**Descripción:**
Múltiples estrategias de layout implementadas en `services/layouts/`: `fill`, `blurred`, `split` (gaming), `pip` (picture-in-picture). Cada una tiene su propio renderer.

**Estado actual:** Funcional. Seleccionable por proyecto vía `render_layout`.

**Criterios de Aceptación:**
- [x] El usuario selecciona un layout al crear el proyecto.
- [x] Face tracker (`MediaPipe`) posiciona la cámara dinámicamente.
- [x] Soporte para `speaker_tracking` y posición manual del crop.
- [ ] Preview visual de cada layout disponible (mockup o sample frame).

---

## Módulo 2 — Distribución (Redes Sociales)

### DIST-01: Publicación en Redes Sociales `P0` 🟡 `partial`

**Descripción:**
El modelo `ScheduledPost` existe con state machine completa (`DRAFT` → `SCHEDULED` → `QUEUED` → `PROCESSING` → `PUBLISHED`). El `AyrshareClient` existe con `send_post()`. La task `upload_to_social_network` y `dispatch_scheduled_posts_batch` existen. Falta el endpoint REST para que el frontend cree y gestione scheduled posts.

**Criterios de Aceptación:**
- [ ] `POST /api/v1/clips/{clip_id}/schedule/` crea un `ScheduledPost`.
- [ ] `GET /api/v1/scheduled-posts/` lista todos los posts programados del workspace.
- [ ] `PATCH /api/v1/scheduled-posts/{id}/` permite modificar fecha y caption antes de publicar.
- [ ] `DELETE /api/v1/scheduled-posts/{id}/` cancela un post no publicado.
- [ ] La task `dispatch_scheduled_posts_batch` se ejecuta periódicamente (Celery Beat).

---

### DIST-02: Programación de Contenido (Calendario) `P1` 🔴 `missing`

**Descripción:**
Vista de calendario que muestra los posts programados por fecha. Requiere un endpoint que devuelva posts agrupados por fecha para un rango dado.

**Criterios de Aceptación:**
- [ ] `GET /api/v1/scheduled-posts/calendar/?from=2026-01-01&to=2026-01-31` devuelve posts agrupados por día.
- [ ] Cada entrada incluye: `id`, `platform`, `publish_at`, `status`, `clip_title`, `thumbnail_url`.
- [ ] El frontend puede renderizar un calendario mensual con esta data.

---

### DIST-03: Adaptación de Formato por Red Social `P1` 🟡 `partial`

**Descripción:**
El `SEOOptimizationService` ya genera `platform_tweaks` (variaciones por plataforma) usando GPT-4o-mini. La task `process_video_seo` existe. Falta integrar el resultado con la creación de `ScheduledPost`.

**Criterios de Aceptación:**
- [ ] Al programar un post, el sistema pre-genera caption y hashtags optimizados por plataforma.
- [ ] Los campos `generated_caption` y `generated_hashtags` del `ScheduledPost` se llenan automáticamente.
- [ ] El usuario puede editar el caption generado antes de confirmar la publicación.
- [ ] Los hashtags se adaptan al límite de cada plataforma (TikTok: 5, Instagram: 30, YouTube: 15).

---

### DIST-04: Optimización de Hashtags por Red Social `P1` 🟢 `exists`

**Descripción:**
El `SEOOptimizationService.generate_metadata()` ya devuelve hashtags optimizados y hora recomendada de publicación. Está implementado con Structured Outputs de OpenAI.

**Criterios de Aceptación:**
- [x] GPT-4o-mini genera 5-8 hashtags relevantes por clip.
- [x] Incluye `recommended_publish_hour_utc`.
- [ ] Endpoint que exponga estos datos: `GET /api/v1/clips/{id}/seo/`.

---

## Módulo 3 — Monetización y Planes

### PAY-01: Sistema de Wallet y Tokens `P0` 🟢 `exists`

**Descripción:**
Sistema transaccional completo: `Wallet` (balance available/reserved), `Transaction` (RESERVE → COMMIT/ROLLBACK), `PricingEngine` (costo por segundo × resolución × features). Integrado con el pipeline de video.

**Estado actual:** Funcional end-to-end. Validado en dry-run.

**Criterios de Aceptación:**
- [x] Al crear un proyecto, se reservan tokens según la duración y resolución.
- [x] Si el render es exitoso, se commitea la reserva.
- [x] Si el render falla, se hace rollback y se devuelven los tokens.
- [x] `PricingEngine` calcula costos con multiplicadores por resolución y add-ons.

---

### PAY-02: Planes de Suscripción (Feature Flags) `P0` 🟢 `exists`

**Descripción:**
Modelo `SubscriptionPlan` con feature flags: `max_video_duration_seconds`, `max_resolution`, `has_watermark`, `allow_scheduling`, `allow_crossposting`, `has_seo_optimization`, `has_thumbnail_engine`, `max_members`.

**Estado actual:** Modelo completo. `PricingEngine` valida contra el plan.

**Criterios de Aceptación:**
- [x] Modelo con todas las feature flags definidas.
- [x] `PricingEngine` valida duración, resolución y features contra el plan.
- [ ] `GET /api/v1/plans/` endpoint público que lista los planes disponibles con pricing.
- [ ] Asociar un `SubscriptionPlan` al `Workspace` (FK faltante en modelo actual).

---

### PAY-03: Compra de Tokens (Stripe Checkout) `P1` 🔴 `missing`

**Descripción:**
Permitir al usuario comprar tokens a través de Stripe Checkout. El archivo `stripe_service.py` existe como stub vacío.

**Criterios de Aceptación:**
- [ ] `POST /api/v1/payments/checkout/` crea una sesión de Stripe Checkout con el monto de tokens seleccionado.
- [ ] Webhook de Stripe (`/api/v1/payments/webhook/`) recibe `checkout.session.completed` y acredita tokens en la Wallet.
- [ ] Registro de la transacción como `Transaction.Type.DEPOSIT`.
- [ ] Manejo de errores: pagos duplicados, sesiones expiradas.

---

### PAY-04: Suscripción Mensual (Stripe Subscriptions) `P2` 🔴 `missing`

**Descripción:**
Planes mensuales que habilitan features de la plataforma. Requiere Stripe Subscriptions + webhooks para activar/desactivar el plan del workspace.

**Criterios de Aceptación:**
- [ ] `POST /api/v1/payments/subscribe/` crea una suscripción en Stripe.
- [ ] Webhooks manejan: `invoice.paid` (activar plan), `customer.subscription.deleted` (degradar a free).
- [ ] El workspace refleja el plan activo en tiempo real.
- [ ] Cancelación: el plan sigue activo hasta fin del período facturado.

---

## Módulo 4 — Frontend y Experiencia de Usuario

### FE-01: Setup del Proyecto Frontend `P0` 🔴 `missing`

**Descripción:**
Crear la aplicación frontend. Stack recomendado: Next.js 14+ (App Router) o Vite + React. Configurar routing, auth context, API client, y design system base.

**Criterios de Aceptación:**
- [ ] Proyecto inicializado con estructura de carpetas definida.
- [ ] Auth context con JWT (login, refresh, logout).
- [ ] API client centralizado con interceptors para token refresh.
- [ ] Design system base: tokens de color, tipografía, spacing.
- [ ] Layout principal con sidebar y header.

---

### FE-02: Pantalla de Login / Registro `P0` 🔴 `missing`

**Descripción:**
Formularios de login y registro que consumen los endpoints JWT existentes (`/api/token/`, `/api/v1/users/register/`).

**Criterios de Aceptación:**
- [ ] Formulario de login con email + password.
- [ ] Formulario de registro con nombre, email, password.
- [ ] Validación client-side + errores del server.
- [ ] Redirect a dashboard post-login.
- [ ] Persist token en `httpOnly` cookie o `localStorage` con refresh automático.

---

### FE-03: Dashboard Principal `P0` 🔴 `missing`

**Descripción:**
Vista principal post-login que muestra: proyectos recientes, estado del wallet, y acciones rápidas (subir video).

**Criterios de Aceptación:**
- [ ] Lista de proyectos del workspace con status, fecha, thumbnail.
- [ ] Widget de Wallet con balance actual.
- [ ] Botón "Nuevo Proyecto" que abre el flujo de upload.
- [ ] Empty state para workspaces sin proyectos.

---

### FE-04: Flujo de Upload y Configuración de Proyecto `P0` 🔴 `missing`

**Descripción:**
Wizard multi-step: (1) Subir video o pegar URL, (2) Configurar estilo/layout/subtítulos, (3) Confirmar y lanzar procesamiento.

**Criterios de Aceptación:**
- [ ] Step 1: Drag & drop de video o input de URL (YouTube/Twitch).
- [ ] Step 2: Selección de `editing_style`, `aspect_ratio`, `render_layout`, toggle subtítulos.
- [ ] Step 3: Resumen con costo estimado en tokens + botón "Procesar".
- [ ] Feedback visual del estado: subiendo → procesando → listo.
- [ ] Validaciones: tamaño máximo (500 MB), formatos permitidos (.mp4, .mov, .avi).

---

### FE-05: Vista de Revisión de Clips (Approval) `P0` 🔴 `missing`

**Descripción:**
Cuando el proyecto está en `awaiting_approval`, el usuario ve los clips propuestos con su transcripción, score y reasoning. Puede aprobar, rechazar o ajustar cada clip.

**Criterios de Aceptación:**
- [ ] Lista de clips propuestos con: título, `virality_score`, `ai_reasoning`, preview del segmento.
- [ ] Toggle aprobar/rechazar por clip.
- [ ] Botón "Renderizar seleccionados" que lanza el render de los clips aprobados.
- [ ] Mostrar `ai_rationale_log` de forma legible (transparencia de IA).

---

### FE-06: Vista de Distribución / Calendario `P1` 🔴 `missing`

**Descripción:**
Calendario mensual con los posts programados. Drag & drop para reprogramar. Formulario para crear un nuevo scheduled post desde un clip renderizado.

**Criterios de Aceptación:**
- [ ] Vista de calendario mensual con posts por día.
- [ ] Click en un día abre formulario de programación.
- [ ] Selección de plataforma(s): TikTok, Instagram Reels, YouTube Shorts.
- [ ] Caption auto-generado (editable) con hashtags.
- [ ] Status visual por post: draft, scheduled, published, failed.

---

### FE-07: Colaboración en Equipo (Workspace) `P2` 🟡 `partial`

**Descripción:**
El modelo `Workspace` + `WorkspaceMember` existe con roles (`admin`, `editor`, `viewer`). Falta la UI y los endpoints para invitar miembros, cambiar roles, y el flujo de aprobaciones.

**Criterios de Aceptación:**
- [ ] `POST /api/v1/workspaces/{id}/invite/` envía invitación por email.
- [ ] Panel de miembros: lista con nombre, rol, status.
- [ ] Admin puede cambiar roles y remover miembros.
- [ ] Flujo de aprobación: clips deben ser aprobados por un admin antes de renderizar (opcional, configurable).

---

## Módulo 5 — Infraestructura y DevOps

### INFRA-01: Celery Beat para Tareas Programadas `P0` 🔴 `missing`

**Descripción:**
Agregar `celery-beat` al `docker-compose.yml` para ejecutar la task `dispatch_scheduled_posts_batch` periódicamente (cada 5 minutos). Sin esto, los posts programados nunca se publican.

**Criterios de Aceptación:**
- [ ] Servicio `beat` en `docker-compose.yml` corriendo `celery -A backend beat`.
- [ ] `dispatch_scheduled_posts_batch` configurada en `CELERY_BEAT_SCHEDULE` (cada 5 min).
- [ ] Posts con `status=SCHEDULED` y `publish_at <= now()` se procesan automáticamente.

---

### INFRA-02: Tests de Integración del Pipeline `P1` 🟡 `partial`

**Descripción:**
Los tests unitarios existen en `tests/` pero faltan tests de integración del pipeline completo (upload → transcription → selection → render).

**Criterios de Aceptación:**
- [ ] Test que sube un video corto (5s) y verifica que el proyecto llega a `awaiting_approval`.
- [ ] Mock de OpenAI para el `SelectionEngine` (no gastar créditos en CI).
- [ ] Test de `PricingEngine` con todos los multiplicadores y edge cases.
- [ ] Test de `WalletService` con race conditions (concurrent reserve).

---

### INFRA-03: CI/CD con GitHub Actions `P2` 🔴 `missing`

**Descripción:**
Pipeline de CI que corre en cada PR: lint, tests, build de Docker image.

**Criterios de Aceptación:**
- [ ] GitHub Action que en cada PR ejecuta: `python manage.py check`, `makemigrations --check`, `test`.
- [ ] Build de las imágenes Docker para verificar que el Dockerfile no está roto.
- [ ] Bloqueo de merge si los checks fallan.

---

## Resumen de Prioridades

### P0 — Must-have para MVP (Sprint 1-2)

| ID | Issue | Status |
|----|-------|--------|
| EDIT-01 | Recorte Inteligente de Mejores Momentos | 🟢 Funcional |
| EDIT-02 | Subtítulos Dinámicos | 🟢 Funcional |
| EDIT-04 | Edición con Marca de Agua (Plan Free) | 🟡 Falta inyección en render |
| EDIT-05 | Estilos de Edición Predefinidos | 🟢 Funcional |
| EDIT-09 | Layouts de Video | 🟢 Funcional |
| DIST-01 | Publicación en Redes Sociales | 🟡 Falta endpoints REST |
| PAY-01 | Sistema de Wallet y Tokens | 🟢 Funcional |
| PAY-02 | Planes de Suscripción | 🟢 Modelo OK, falta endpoint |
| FE-01 | Setup Frontend | 🔴 Por construir |
| FE-02 | Login / Registro | 🔴 Por construir |
| FE-03 | Dashboard | 🔴 Por construir |
| FE-04 | Upload y Config de Proyecto | 🔴 Por construir |
| FE-05 | Revisión de Clips | 🔴 Por construir |
| INFRA-01 | Celery Beat | 🔴 Por construir |

### P1 — Should-have (Sprint 3-4)

| ID | Issue | Status |
|----|-------|--------|
| EDIT-03 | Traducción de Subtítulos | 🔴 Por construir |
| EDIT-06 | BrandKit CRUD | 🟡 Modelo OK, falta endpoints |
| EDIT-07 | IA Explicable — Rationale Log | 🟡 Data OK, falta exposición |
| DIST-02 | Calendario de Contenido | 🔴 Por construir |
| DIST-03 | Adaptación por Red Social | 🟡 Engine OK, falta integración |
| DIST-04 | Hashtags Optimizados | 🟢 Engine funcional, falta endpoint |
| PAY-03 | Compra de Tokens (Stripe) | 🔴 Por construir |
| FE-06 | Vista Calendario | 🔴 Por construir |
| INFRA-02 | Tests de Integración | 🟡 Parcial |

### P2 — Nice-to-have (Backlog)

| ID | Issue | Status |
|----|-------|--------|
| EDIT-08 | Feedback y Ajustes Finos | 🔴 Por construir |
| PAY-04 | Suscripción Mensual (Stripe) | 🔴 Por construir |
| FE-07 | Colaboración en Equipo | 🟡 Modelo OK, falta UI |
| INFRA-03 | CI/CD GitHub Actions | 🔴 Por construir |
