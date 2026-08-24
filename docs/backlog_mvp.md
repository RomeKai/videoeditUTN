# 📋 OneCreator — MVP Backlog v2

> **Proyecto:** OneCreator — All-in-One Viral Studio
> **Equipo:** Dev 1 – Dev 5 | **Timeline:** 3 meses (~12 semanas)
> **Visión:** Competir directamente con Opus Clips — core robusto primero, frontend al final.
> **Principios:** Security-first, guardrails en toda IA, eficiencia en procesamiento.

---

## Leyenda

| Etiqueta | Significado |
|----------|-------------|
| 🟢 `exists` | Código funcional en el repo |
| 🟡 `partial` | Modelo/servicio existe pero incompleto |
| 🔴 `missing` | Hay que construirlo desde cero |
| `P0` | Must-have — sin esto no hay producto |
| `P1` | Should-have — diferenciador competitivo |
| `P2` | Nice-to-have — post-MVP |

---

## Roadmap por Fase

```
 MES 1 (Semanas 1-4)         MES 2 (Semanas 5-8)         MES 3 (Semanas 9-12)
┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
│ CORE ENGINE        │   │ PROMPT-TO-EDIT     │   │ DISTRIBUCIÓN +     │
│ • Face Tracking v2 │   │ • Motor completo   │   │   SOCIAL PUBLISH   │
│ • Layouts nuevos   │   │ • Guardrails IA    │   │ • Calendar engine  │
│ • Worker perf      │   │ • Preview pipeline │   │ • Multi-platform   │
│ • Security layer   │   │                    │   │ • Frontend MVP     │
│ • Tests pipeline   │   │ MONETIZACIÓN       │   │                    │
│                    │   │ • Stripe checkout  │   │ POLISH + DEPLOY    │
│                    │   │ • Watermark inject │   │ • CI/CD            │
└────────────────────┘   └────────────────────┘   └────────────────────┘
```

---

## Módulo 1 — Core Engine (Mes 1)

> Objetivo: que el pipeline de video edite a nivel producción — rápido, preciso, seguro.

---

### CORE-01: Face Tracking v2 — Multi-face + Smoothing Mejorado `P0` 🟡 `partial`

**Descripción:**
El `FaceTracker` actual usa MediaPipe Tasks con un modelo `.tflite` estático. Funciona para un solo rostro pero tiene limitaciones críticas:
- **Single-face only:** `max()` sobre detecciones descarta todas las caras excepto la de mayor score.
- **Sin interpolación temporal:** frames sin detección usan el `last_valid_point`, causando saltos.
- **Sampling a 3 fps fijo:** insuficiente para movimiento rápido, excesivo para talking heads.
- **Sin cache de modelo:** se reinicializa por cada clip renderizado.

**Tareas técnicas:**
1. Multi-face tracking: trackear N caras simultáneamente, asignar IDs persistentes entre frames (tracking por proximidad).
2. Interpolación temporal: cuando se pierde una detección, interpolar posición entre el último y siguiente punto válido (no repetir el último).
3. Smoothing adaptativo: reemplazar `MovingAverageSmoothing(window=5)` por Exponential Moving Average con factor configurable por estilo de video.
4. Adaptive sampling rate: 1-2 fps para podcast/talking-head, 5+ fps para gaming/acción rápida (derivar del `editing_style`).
5. Singleton del modelo: cargar `FaceDetector` una vez por worker process, no por clip.

**Archivos a modificar:**
- `services/face_tracker.py` — refactor completo
- `services/tracking_utils.py` — agregar EMA smoothing + interpolation
- `services/layouts/` — adaptar para recibir multi-face paths

**Criterios de Aceptación:**
- [ ] Trackea hasta 3 caras simultáneamente con IDs estables.
- [ ] Sin saltos visibles entre frames — transiciones suaves.
- [ ] Sampling rate varía según `editing_style` del proyecto.
- [ ] El modelo MediaPipe se carga una sola vez por worker (singleton).
- [ ] Test con video de 2+ personas que demuestre tracking estable.

---

### CORE-02: Speaker Detection Real — pyannote.audio `P1` 🔴 `missing`

**Descripción:**
`SpeakerTrackingEngine` es un stub: `detect_active_speaker_segment()` devuelve `[(0, clip.duration, 0)]` hardcodeado. Para el layout `active` (speaker dinámico) necesitamos detección real de quién habla.

**Tareas técnicas:**
1. Integrar `pyannote.audio` para voice activity detection + speaker diarization.
2. Correlacionar segments de audio con las caras detectadas por `FaceTracker` (proximity matching).
3. Generar timeline de speaker activo: `[(start, end, face_id), ...]`.
4. El layout `ActiveSpeakerLayout` consume este timeline para switchear entre caras.

**Criterios de Aceptación:**
- [ ] Diarización funcional: detecta al menos 2 speakers distintos.
- [ ] Correlación audio → cara con al menos 80% de precisión en videos de podcast.
- [ ] Fallback graceful: si la diarización falla, usar el face con mayor saliency.
- [ ] No bloquea el pipeline si `pyannote` no está disponible (degradación graceful).

---

### CORE-03: Nuevos Layouts — Podcast, Cinematic, Trending `P0` 🔴 `missing`

**Descripción:**
Actualmente hay 6 layouts (`fill`, `fit`, `blurred`, `split`, `versus`, `active`). `pip` está mapeado a `FitLayout` (placeholder). Para competir con Opus Clips faltan layouts que son estándar en la industria.

**Layouts nuevos a implementar:**

| Layout | Descripción | Referencia |
|--------|-------------|-----------|
| `podcast` | Split horizontal 50/50 con face tracking independiente por mitad | Riverside.fm |
| `cinematic` | Letterbox 2.35:1 con crop inteligente siguiendo la acción | Film look |
| `pip_real` | Picture-in-Picture real — video principal + cámara en esquina (configurable) | Loom/OBS |
| `trending` | Template rotativo: borde con gradiente + texto overlay + emoji | TikTok trends |
| `reaction` | Video original arriba, reacción abajo (layout YouTube Shorts) | React content |

**Archivos a crear/modificar:**
- `services/layouts/podcast.py` — nuevo
- `services/layouts/cinematic.py` — nuevo
- `services/layouts/pip_real.py` — nuevo (reemplazar el placeholder)
- `services/layouts/trending.py` — nuevo
- `services/layouts/reaction.py` — nuevo
- `services/layouts/__init__.py` — registrar en `LAYOUT_REGISTRY`
- `models.py` — agregar al enum `Layout.choices`

**Criterios de Aceptación:**
- [ ] Cada layout implementa la interfaz `LayoutStrategy` (método `apply(clip) → clip`).
- [ ] Todos respetan `target_w × target_h` y producen dimensiones pares (H.264 safe).
- [ ] `podcast` y `pip_real` usan multi-face tracking de CORE-01.
- [ ] `trending` acepta parámetros configurables (color de borde, texto overlay).
- [ ] Tests visuales: render de un clip de 5s con cada layout nuevo.

---

### CORE-04: Optimización de Performance del Worker `P0` 🟡 `partial`

**Descripción:**
El worker Celery actual procesa todo secuencialmente en un solo task. Un video de 763s tardó ~7 min solo en transcripción (CPU). Para competir con Opus Clips necesitamos resultados en <3 minutos para un video de 10 min.

**Tareas técnicas:**

1. **GPU acceleration para Whisper** (si disponible):
   - Detectar CUDA/MPS al iniciar el worker.
   - Usar modelo `small` con GPU vs `base` en CPU (mismo accuracy, 5x speed).

2. **Parallel task chains:**
   - Separar el pipeline monolítico en sub-tasks encadenadas con `celery.chain()`:
     ```
     upload_to_r2 | generate_proxy | transcribe | ai_select
     ```
   - `transcribe` y `generate_proxy` pueden correr en paralelo con `celery.group()`.

3. **Whisper model preloading:**
   - Precargar el modelo Whisper al iniciar el worker (signal `worker_init`), no al procesar cada video.
   - Cache en variable global del process.

4. **FFmpeg optimization:**
   - Proxy generation: usar `-preset ultrafast` en vez de default.
   - Render final: pool de threads para renderizar múltiples clips en paralelo.

5. **Memory management:**
   - Configurar `--max-memory-per-child` en Celery para reciclar workers que acumulan memoria.
   - Cerrar explícitamente clips MoviePy en `finally` blocks (ya parcial).

**Archivos a modificar:**
- `backend/celery.py` — worker signals, memory config
- `tasks.py` — refactorizar en sub-tasks + chain/group
- `services/transcription_engine.py` — GPU detect, model preload
- `utils/ffmpeg_utils.py` — ultrafast preset

**Criterios de Aceptación:**
- [ ] Video de 10 min procesado en <3 min con GPU, <6 min en CPU.
- [ ] Whisper model se carga una sola vez por worker process.
- [ ] Proxy generation y transcripción corren en paralelo.
- [ ] Workers se reciclan después de 50 tareas (`max_tasks_per_child`).
- [ ] Métricas de tiempo por etapa logueadas para benchmarking.

---

### CORE-05: Security Layer — AI Guardrails + Input Validation `P0` 🟡 `partial`

**Descripción:**
`AI_Security_Shield` existe en `core/security.py` (aislamiento de input para prompts). Pero faltan guardrails críticos:

**Tareas técnicas:**

1. **Validación de video input robusta:**
   - Verificar MIME type real con `python-magic` (ya está en requirements pero no se usa en el upload).
   - Limitar resolución máxima de input (4K = OK, 8K = rechazar).
   - Scan de duración máxima contra el plan del workspace ANTES de subir a R2.

2. **Output sanitization de IA:**
   - Validar que el JSON del `SelectionEngine` tenga timestamps dentro del rango del video.
   - Validar que `virality_score` esté entre 0-100.
   - Rechazar respuestas con código ejecutable o prompts inyectados.

3. **Rate limiting por workspace:**
   - Máximo N proyectos concurrentes por workspace (según plan).
   - Cooldown entre uploads (anti-abuse).

4. **Audit trail:**
   - Loguear todo prompt enviado a OpenAI en `ai_rationale_log`.
   - Loguear input/output de cada servicio de IA para debugging.

**Criterios de Aceptación:**
- [ ] Video con MIME falso (`.mp4` que es `.txt`) se rechaza con error claro.
- [ ] JSON de SelectionEngine con timestamps fuera de rango se descarta (no crashea).
- [ ] Rate limit configurable por plan: free=2 concurrent, pro=10 concurrent.
- [ ] Todo prompt enviado a OpenAI queda registrado en `ai_rationale_log`.

---

### CORE-06: Tests de Integración del Pipeline `P0` 🟡 `partial`

**Descripción:**
Tests existentes son mínimos. Para un core robusto necesitamos coverage del camino crítico.

**Criterios de Aceptación:**
- [ ] Test E2E: upload video 5s → transcription (Whisper tiny) → selection (mock OpenAI) → assert `awaiting_approval`.
- [ ] Test de `PricingEngine`: todos los multiplicadores, edge cases, planes free vs pro.
- [ ] Test de `WalletService`: reserve + commit, reserve + rollback, insufficient funds, concurrent reserve (race condition).
- [ ] Test de `CloudflareR2Manager`: `USE_S3=False` mode (local bypass).
- [ ] Test de cada `LayoutStrategy.apply()` con un clip sintético de 2s.
- [ ] Mock de OpenAI en todos los tests (no gastar créditos en CI).

---

## Módulo 2 — Prompt-to-Edit Engine (Mes 2)

> Objetivo: el usuario describe lo que quiere en lenguaje natural y la IA edita el video.

---

### PTE-01: Motor de Prompt-to-Edit — Diseño e Implementación `P0` 🔴 `missing`

**Descripción:**
Sistema nuevo donde el usuario escribe un prompt de edición (ej: "Cortá los mejores momentos de humor, poné subtítulos grandes amarillos, layout vertical con fondo borroso") y el sistema traduce eso a configuración de proyecto + selección de clips.

**Arquitectura propuesta:**

```
User Prompt
     │
     ▼
┌─────────────────┐
│ PromptParser     │  GPT-4o con Structured Outputs
│ (LLM → Config)  │  Input: prompt + video metadata (duration, transcript summary)
└────────┬────────┘  Output: ProjectConfig JSON (style, layout, subtitle config, etc.)
         │
         ▼
┌─────────────────┐
│ ConfigValidator  │  Valida contra el plan del user + limites del sistema
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Pipeline normal  │  Reutiliza SelectionEngine + RenderEngine con la config generada
└─────────────────┘
```

**Output del PromptParser (Structured Output):**
```json
{
  "editing_style": "dynamic",
  "aspect_ratio": "9:16",
  "render_layout": "blurred",
  "add_subtitles": true,
  "subtitle_color": "#FFFF00",
  "subtitle_size": "large",
  "max_clips": 3,
  "clip_selection_criteria": "humor and engagement peaks",
  "remove_silences": true,
  "use_facetracking": true
}
```

**Archivos a crear:**
- `services/prompt_parser.py` — LLM prompt → ProjectConfig
- `services/prompt_validator.py` — guardrails + plan validation
- `views.py` — nuevo endpoint `POST /api/v1/projects/from-prompt/`

**Criterios de Aceptación:**
- [ ] `POST /api/v1/projects/from-prompt/` acepta `{ "prompt": "...", "source_file": <video> }`.
- [ ] El LLM genera un `ProjectConfig` JSON válido usando Structured Outputs.
- [ ] Config se valida contra el plan del user (duración, resolución, features).
- [ ] El pipeline existente se reutiliza al 100% — no se duplica lógica.
- [ ] Prompts ambiguos generan config con defaults sensatos (no errores).
- [ ] Prompt injection attempts se neutralizan via `AI_Security_Shield`.

---

### PTE-02: Prompt-to-Edit — Selección Dirigida por Prompt `P1` 🔴 `missing`

**Descripción:**
Extender el `SelectionEngine` para que acepte criterios de selección del prompt del usuario (ej: "momentos de humor", "picos de emoción", "partes educativas") además de la detección genérica de viralidad.

**Criterios de Aceptación:**
- [ ] `SelectionEngine.select_viral_clips()` acepta un parámetro `selection_criteria: str` opcional.
- [ ] El criteria del usuario se inyecta en el prompt del LLM de forma segura (aislado).
- [ ] El `ai_reasoning` de cada clip refleja cómo el criteria influyó en la selección.
- [ ] Sin criteria explícito, el comportamiento default no cambia.

---

### PTE-03: Preview Pipeline — Pre-render Rápido `P1` 🔴 `missing`

**Descripción:**
Generar un preview de baja resolución (360p, sin subtítulos complejos) antes del render final para que el usuario vea qué va a obtener sin gastar tokens de render completo.

**Criterios de Aceptación:**
- [ ] `POST /api/v1/clips/{id}/preview/` genera un render 360p en <30s.
- [ ] El preview usa el layout seleccionado pero sin subtítulos animados (solo texto plano).
- [ ] El preview se almacena temporalmente (TTL 1h, auto-cleanup).
- [ ] No consume tokens del wallet (es gratuito).

---

## Módulo 3 — Monetización (Mes 2)

---

### PAY-01: Watermark Injection en Render (Plan Free) `P0` 🟡 `partial`

**Descripción:**
`SubscriptionPlan.has_watermark` existe. Falta la inyección física en `RenderEngine.render_clip()`.

**Criterios de Aceptación:**
- [ ] Si `plan.has_watermark == True`, el render superpone el logo de OneCreator.
- [ ] Watermark: semi-transparente (40% opacity), esquina inferior derecha, 15% del ancho del video.
- [ ] Asset del watermark en `back/assets/watermarks/onecreator_logo.png`.
- [ ] Server-side enforced — no bypasseable desde la API.
- [ ] Test: render con y sin watermark produce archivos diferentes.

---

### PAY-02: Stripe Checkout — Compra de Tokens `P1` 🔴 `missing`

**Descripción:**
`stripe_service.py` es un stub vacío. Implementar Stripe Checkout Session para compra de tokens.

**Criterios de Aceptación:**
- [ ] `POST /api/v1/payments/checkout/` crea un Stripe Checkout Session.
- [ ] Webhook `POST /api/v1/payments/webhook/` procesa `checkout.session.completed`.
- [ ] Tokens se acreditan en la Wallet tras pago exitoso (`Transaction.Type.DEPOSIT`).
- [ ] Webhook verificado con `stripe.Webhook.construct_event()` (signature validation).
- [ ] Idempotencia: mismo evento procesado 2 veces no duplica créditos.

---

### PAY-03: Endpoint de Planes Públicos `P0` 🟡 `partial`

**Descripción:**
El modelo `SubscriptionPlan` existe completo. Falta un endpoint público que liste los planes.

**Criterios de Aceptación:**
- [ ] `GET /api/v1/plans/` (público, sin auth) devuelve planes con pricing y features.
- [ ] Incluye: `name`, `max_video_duration_seconds`, `max_resolution`, features flags, `base_discount_rate`.
- [ ] Solo planes con `is_active=True`.

---

## Módulo 4 — Distribución Social (Mes 3)

> Objetivo: publicación directa multi-plataforma, no un wrapper de Ayrshare.

---

### DIST-01: Social Auth — OAuth Flows por Plataforma `P0` 🔴 `missing`

**Descripción:**
Para publicar en redes sin Ayrshare como intermediario, necesitamos OAuth directo con cada plataforma. El archivo `social_auth.py` existe pero hay que implementar los flows.

**Plataformas target (MVP):**

| Plataforma | API | OAuth | Dificultad |
|-----------|-----|-------|-----------|
| TikTok | Content Posting API | OAuth 2.0 | Media — requiere app review |
| YouTube | YouTube Data API v3 | OAuth 2.0 (Google) | Baja — bien documentado |
| Instagram | Instagram Graph API | OAuth via Facebook | Alta — requiere Business account + app review |

**Criterios de Aceptación:**
- [ ] Modelo `SocialAccount` con campos: `platform`, `access_token`, `refresh_token`, `expires_at`, `profile_data`.
- [ ] `GET /api/v1/social/connect/{platform}/` inicia el OAuth flow (redirect a la plataforma).
- [ ] `GET /api/v1/social/callback/{platform}/` procesa el callback y almacena tokens.
- [ ] Tokens se encriptan en DB (no plain text).
- [ ] Refresh automático de tokens expirados antes de publicar.

---

### DIST-02: Publishing Engine — Publicación Directa Multi-plataforma `P0` 🔴 `missing`

**Descripción:**
Motor de publicación que sube el video directamente a cada API de plataforma, sin depender de Ayrshare. Reemplaza `AyrshareClient.send_post()`.

**Criterios de Aceptación:**
- [ ] `PublishingEngine.publish(clip_id, platform, caption, hashtags)` sube el video directamente.
- [ ] Adapta el video a los requisitos de cada plataforma:
  - TikTok: max 10 min, aspect ratio 9:16, max 287 MB.
  - YouTube Shorts: max 60s, vertical, max 256 MB.
  - Instagram Reels: max 90s, min 3s, max 1 GB.
- [ ] Retry con backoff exponencial si la API falla.
- [ ] Status tracking: actualiza `ScheduledPost.status` en tiempo real.
- [ ] Fallback a Ayrshare si la publicación directa falla y Ayrshare está configurado.

---

### DIST-03: Scheduling Engine — Celery Beat + Calendar `P0` 🟡 `partial`

**Descripción:**
`ScheduledPost` model y `dispatch_scheduled_posts_batch` task existen. Falta Celery Beat y endpoints REST.

**Criterios de Aceptación:**
- [ ] Servicio `beat` en `docker-compose.yml`.
- [ ] `dispatch_scheduled_posts_batch` corre cada 5 min vía `CELERY_BEAT_SCHEDULE`.
- [ ] CRUD completo: `POST/GET/PATCH/DELETE /api/v1/scheduled-posts/`.
- [ ] `GET /api/v1/scheduled-posts/calendar/?from=...&to=...` devuelve posts por día.
- [ ] SEO metadata auto-generado al crear un scheduled post (via `SEOOptimizationService`).

---

### DIST-04: Adaptación de Contenido por Plataforma `P1` 🟡 `partial`

**Descripción:**
`SEOOptimizationService` genera `platform_tweaks`. Extender para que la adaptación sea más profunda: re-encode del video si es necesario (ej: YouTube Shorts requiere ≤60s).

**Criterios de Aceptación:**
- [ ] Si el clip excede la duración máxima de la plataforma, ofrecer trimming automático.
- [ ] Caption y hashtags adaptados por plataforma (límites: TikTok 2200 chars, IG 2200, YT 5000).
- [ ] El usuario puede override el caption generado antes de confirmar.

---

## Módulo 5 — Frontend (Mes 3, últimas semanas)

> Prioridad baja. MVP funcional mínimo que demuestre el core.

---

### FE-01: Setup + Auth + Dashboard `P0` 🔴 `missing`

**Criterios de Aceptación:**
- [ ] App React/Next.js inicializada con design system base.
- [ ] Login/registro con JWT.
- [ ] Dashboard con lista de proyectos y balance del wallet.

---

### FE-02: Upload + Configuración de Proyecto `P0` 🔴 `missing`

**Criterios de Aceptación:**
- [ ] Drag & drop de video o input de URL.
- [ ] Wizard de configuración: estilo, layout, subtítulos.
- [ ] Campo de prompt para Prompt-to-Edit (si PTE-01 está implementado).
- [ ] Feedback visual de progreso del pipeline.

---

### FE-03: Revisión de Clips + Publish `P1` 🔴 `missing`

**Criterios de Aceptación:**
- [ ] Vista de clips propuestos con score, reasoning, preview.
- [ ] Aprobar/rechazar clips.
- [ ] Programar publicación desde la vista del clip.

---

## Módulo 6 — Infraestructura y DevOps

---

### INFRA-01: CI/CD — GitHub Actions `P1` 🔴 `missing`

**Criterios de Aceptación:**
- [ ] Workflow en cada PR: `manage.py check`, `makemigrations --check`, `test`.
- [ ] Build de imágenes Docker para verificar Dockerfile.
- [ ] Branch protection: merge bloqueado si checks fallan.

---

### INFRA-02: Monitoring y Logging Estructurado `P2` 🔴 `missing`

**Criterios de Aceptación:**
- [ ] Logs JSON estructurados (no plain text) para búsqueda en producción.
- [ ] Métricas de tiempo por etapa del pipeline (dashboard).
- [ ] Alertas cuando un task falla N veces consecutivas.

---

## Asignación Sugerida por Dev

| Dev | Foco Principal | Issues Asignadas |
|-----|---------------|-----------------|
| **Dev 1** | Core Engine — Face Tracking + Layouts | CORE-01, CORE-02, CORE-03 |
| **Dev 2** | Core Engine — Performance + Worker | CORE-04, CORE-06, INFRA-01 |
| **Dev 3** | Prompt-to-Edit + IA | PTE-01, PTE-02, PTE-03, CORE-05 |
| **Dev 4** | Distribución Social + Publishing | DIST-01, DIST-02, DIST-03, DIST-04 |
| **Dev 5** | Monetización + Frontend | PAY-01, PAY-02, PAY-03, FE-01, FE-02, FE-03 |

---

## Resumen Ejecutivo

| Prioridad | Issues | Core/Backend | Frontend |
|-----------|--------|-------------|----------|
| **P0** | 16 | 13 | 3 |
| **P1** | 7 | 5 | 2 |
| **P2** | 1 | 1 | 0 |
| **Total** | **24** | **19** | **5** |

### Diferenciadores vs Opus Clips

| Feature | Opus Clips | OneCreator (Target) |
|---------|-----------|-------------------|
| Clip selection AI | GPT-4 | GPT-4o/4o-mini (configurable) |
| Face tracking | Básico | Multi-face + speaker detection |
| Layouts | ~5 | 11+ (con podcast, cinematic, trending) |
| Prompt-to-Edit | No | Sí — lenguaje natural → config |
| Social publishing | Integración limitada | Directo via OAuth (sin intermediarios) |
| Watermark (free) | Sí | Sí |
| Subtítulos | Estáticos | Word-level animados + traducción |
| Transparencia IA | No | Sí — rationale log explicable |
