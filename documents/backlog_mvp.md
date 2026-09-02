# 📋 OneCreator — MVP Backlog v2

> \*\*Proyecto:\*\* OneCreator — All-in-One Viral Studio
> \*\*Equipo:\*\* Dev 1 – Dev 6 | \*\*Timeline:\*\* 3 meses (\~12 semanas)
> \*\*Modalidad:\*\* Pair coding — pares especializados por dominio
> \*\*Visión:\*\* Competir directamente con Opus Clips — core robusto primero, frontend al final.
> \*\*Principios:\*\* Security-first, guardrails en toda IA, eficiencia en procesamiento.

\---

## Leyenda

|Etiqueta|Significado|
|-|-|
|🟢 `exists`|Código funcional en el repo|
|🟡 `partial`|Modelo/servicio existe pero incompleto|
|🔴 `missing`|Hay que construirlo desde cero|
|`P0`|Must-have — sin esto no hay producto|
|`P1`|Should-have — diferenciador competitivo|
|`P2`|Nice-to-have — post-MVP|

\---

## Stack Tecnológico Justificado

> Cada tecnología fue elegida con criterio — no por moda. Los ADRs en [`documents/adrs/`](adrs/README.md) documentan las alternativas evaluadas y el porqué de cada decisión.

| Capa | Tecnología | Justificación | ADR |
|------|-----------|---------------|-----|
| **Framework** | Django 5 + DRF | ORM maduro, admin gratis, ecosystem amplio para SaaS. DRF + Spectacular para OpenAPI auto-generada. | — |
| **Auth** | SimpleJWT | Stateless auth para API REST. Access token corto (25min) + refresh (3 días) + rotación + blacklist. | — |
| **Base de datos** | PostgreSQL 15 | ACID, JSONB para datos semi-estructurados (transcripciones, AI rationale), extensiones (pg_trgm para búsqueda). | — |
| **Task Queue** | Celery + Valkey | Workers distribuidos para pipeline de video. Valkey (fork BSD-3 de Redis) como broker — zero riesgo de licencia. | [Valkey migration](../AI-DECISIONS.md#13) |
| **Transcripción** | Whisper Local | Word-level timestamps obligatorios para subtítulos animados — la API de OpenAI NO los soporta. $0/request. | [ADR-002](adrs/002-whisper-local-transcription.md) |
| **LLM (clip selection)** | **Por decidir** (OpenAI/Gemini) | Actualmente OpenAI GPT-4o + Gemini Flash como fallback. Evaluación de proveedor primario es tarea bloqueante IA-00. | [ADR-001](adrs/001-llm-provider-selection.md) |
| **Rendering** | MoviePy 2.0 + FFmpeg | MoviePy para composición frame-by-frame (subtítulos, layouts). FFmpeg directo para operaciones pesadas (re-encode, silence removal). | [ADR-003](adrs/003-rendering-stack.md) |
| **Face Tracking** | MediaPipe Tasks | Modelo `.tflite` local, sin API externa. Ligero, rápido, funciona en CPU. | — |
| **Storage** | Cloudflare R2 | $0 egress fees — crítico para SaaS de video. Compatible S3 API (boto3 funciona sin cambios). | [ADR-004](adrs/004-cloudflare-r2-storage.md) |
| **Publicación Social** | **Por decidir** (OAuth directo / Ayrshare) | Evaluación pendiente: OAuth directo vs intermediario. | [ADR-005](adrs/005-social-publishing-strategy.md) |
| **Deployment** | **Por decidir** | CI/CD en GitHub Actions. Target de producción pendiente evaluación. | [ADR-006](adrs/006-deployment-strategy.md) |
| **Containerización** | Docker + Docker Compose | 4 servicios: db, redis, web, worker. Multi-stage build (web vs worker targets). | — |

---

## Estrategia de Testing

| Nivel | Qué cubre | Herramienta | Responsable |
|-------|----------|-------------|-------------|
| **Unit** | Servicios individuales (PricingEngine, WalletService, TranscriptionEngine) | `pytest` + `pytest-django` | Cada par, su dominio |
| **Integración** | Pipeline completo: upload → transcripción → selección → render | `pytest` + mocks de APIs externas | Par Engine (CORE-06) |
| **Contract** | JSON schema de respuestas LLM (clip selection, SEO metadata) | `pydantic` validation + `pytest` | Par IA |
| **Visual** | Renders de cada layout con video sintético | Script manual + visual review | Par Engine |
| **E2E API** | Endpoints REST completos (auth, upload, CRUD) | `pytest` + DRF test client | Par Producto |
| **CI Gate** | `manage.py check --deploy` + `makemigrations --check` + `pytest` | GitHub Actions | Par Engine (INFRA-01) |

### Reglas de testing

1. **Todo LLM call se mockea en CI** — no gastar créditos en tests automatizados.
2. **Los tests de pipeline usan video sintético** de 2-5 segundos generado con MoviePy (no fixtures pesadas en el repo).
3. **Coverage target: 70%** del camino crítico (upload → render → download). No perseguir 100% — testear lo que importa.
4. **Todo ADR `proposed` requiere un PoC con tests** antes de aceptarse.

---

## Convenciones de Desarrollo

### Branching Strategy

```
main ─────────────────────────────────────────────► producción
  │
  └── develop ────────────────────────────────────► integración
        │
        ├── feat/IA-00-llm-provider-evaluation
        ├── feat/CORE-01-face-tracking-v2
        ├── feat/PAY-01-watermark-injection
        └── fix/CORE-04-memory-leak-whisper
```

- **`main`**: siempre deployable. Solo merges desde `develop` con PR aprobado.
- **`develop`**: integración. Cada par mergea features aquí.
- **`feat/<ID>-<descripción>`**: una rama por tarea del backlog.
- **`fix/<ID>-<descripción>`**: bugfixes.

### Commits

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat(selection-engine): add Gemini strategy for clip selection
fix(render): close MoviePy clips in finally block to prevent memory leak
docs(adr): add ADR-001 LLM provider evaluation
test(wallet): add concurrent reserve race condition test
chore(deps): bump mediapipe to 0.10.30
```

### Pull Requests

1. **Título**: `[<ID>] Descripción breve` — ej: `[CORE-01] Face tracking v2 with multi-face support`
2. **Reviewer**: el otro par (cross-review). Par IA revisa Par Engine y viceversa.
3. **Checklist antes de merge**:
   - [ ] Tests pasan en CI
   - [ ] No hay secretos hardcodeados
   - [ ] Si toca IA → mockear en tests
   - [ ] Si cambia API → actualizar Spectacular schema
   - [ ] Si es ADR → linked en el PR description

---

## Roadmap por Fase

```
 SEMANA 0-1               MES 1 (Semanas 2-5)         MES 2 (Semanas 6-9)         MES 3 (Semanas 10-12)
┌──────────────────┐   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
│ DECISIONES ARQ.  │   │ CORE ENGINE        │   │ PROMPT-TO-EDIT     │   │ DISTRIBUCIÓN +     │
│ ⛔ LLM Provider  │   │ • Face Tracking v2 │   │ • Motor completo   │   │   SOCIAL PUBLISH   │
│ • ADRs review    │   │ • Layouts nuevos   │   │ • Guardrails IA    │   │ • Calendar engine  │
│ • PoC comparativo│   │ • Worker perf      │   │ • Preview pipeline │   │ • Multi-platform   │
│                  │   │ • Security layer   │   │                    │   │ • Frontend MVP     │
│                  │   │ • Tests pipeline   │   │ MONETIZACIÓN       │   │                    │
│                  │   │                    │   │ • Stripe checkout  │   │ POLISH + DEPLOY    │
│                  │   │                    │   │ • Watermark inject │   │ • CI/CD            │
└──────────────────┘   └────────────────────┘   └────────────────────┘   └────────────────────┘
```

\---

## Módulo 0 — Decisiones Arquitectónicas (Semana 0-1)

> Objetivo: resolver decisiones tecnológicas bloqueantes ANTES de implementar. Sin esto, cada par construye sobre arena.

---

### IA-00: Evaluación y Selección de Proveedor LLM `P0` 🔴 `missing` ⛔ BLOQUEANTE

**Descripción:**
El codebase tiene 3 servicios acoplados a OpenAI: `SelectionEngine` (clip selection), `SEOOptimizationService` (metadata social), y `AI_Security_Shield` (content moderation). Antes de construir PTE-01 (Prompt-to-Edit) hay que decidir si OpenAI sigue siendo el proveedor primario o migramos a Gemini, Anthropic, u otro.

**¿Por qué es bloqueante?**
- PTE-01 y PTE-02 construyen el motor de IA central — si elegimos proveedor después, hay que reescribir
- `AI_Security_Shield.check_content_safety()` usa la Moderation API de OpenAI — si cambiamos, necesitamos alternativa
- El `SEOOptimizationService` usa Structured Outputs de OpenAI (Pydantic → `response_format`) — no todos los proveedores lo soportan

**Subtareas técnicas:**

| # | Subtarea | Talla | Dev |
|---|---------|-------|-----|
| 1 | Ejecutar PoC con 3 transcripciones reales (2min, 10min, 30min) contra Gemini Flash, GPT-4o-mini, y opcionalmente Groq/Together | M | Dev 1 + Dev 2 |
| 2 | Medir: latencia (p50/p95), costo por request, calidad JSON (parseable rate), calidad de selección (review manual) | S | Dev 1 + Dev 2 |
| 3 | Evaluar Content Moderation alternativa si no es OpenAI: Perspective API (Google), Gemini Safety Settings, o clasificador local | M | Dev 2 |
| 4 | Evaluar `instructor` library como wrapper para Structured Outputs multi-proveedor | S | Dev 1 |
| 5 | Refactorizar `SelectionEngine` para que el Strategy Pattern cubra TODOS los servicios LLM (no solo clip selection) — diseñar `LLMGateway` | M | Dev 1 + Dev 2 |
| 6 | Completar y aceptar ADR-001 con la decisión final + datos del PoC | S | Dev 1 |

**Definition of Done:**
- [ ] ADR-001 en estado `accepted` con datos reales del PoC
- [ ] `LLMGateway` diseñado (al menos la interfaz) — ningún servicio importa `from openai import OpenAI` directamente
- [ ] Moderation pathway definido para el proveedor elegido
- [ ] `.env.example` actualizado con las keys del proveedor seleccionado
- [ ] El equipo completo fue notificado de la decisión (sync semanal)

**Riesgos:**
- 🔴 Si se elige un proveedor sin Structured Outputs nativos, el `SEOOptimizationService` necesita refactor significativo
- 🟡 Free tiers pueden ser insuficientes para el PoC si se usan transcripciones largas
- 🟡 Si se elige Gemini, los safety filters pueden ser demasiado agresivos para contenido de entretenimiento (false positives)

**ADR asociado:** [ADR-001: Selección de proveedor LLM](adrs/001-llm-provider-selection.md)

---

### ARCH-01: Revisión de ADRs por Par `P0` 🔴 `missing`

**Descripción:**
Cada par tiene ADRs `proposed` que debe evaluar, debatir, y aceptar o rechazar. No es código — es ingeniería de decisiones.

**Subtareas por par:**

| Par | ADRs a revisar | Acción esperada |
|-----|---------------|-----------------|
| 🧠 Par IA | ADR-001 (LLM Provider), ADR-002 (Whisper) | IA-00 resuelve ADR-001. ADR-002 ya está accepted — validar que sigue vigente |
| ⚙️ Par Engine | ADR-003 (Rendering Stack), ADR-004 (R2 Storage), ADR-006 (Deployment) | Revisar ADR-003 y ADR-004 (accepted). ADR-006 requiere evaluación y PoC |
| 🚀 Par Producto | ADR-005 (Social Publishing) | Evaluar OAuth directo vs Ayrshare. Investigar tiempos de app review de TikTok/Instagram |

**Definition of Done:**
- [ ] Cada par revisó sus ADRs y dejó comentarios o propuso cambios
- [ ] ADRs `proposed` tienen decisión o timeline para decidir
- [ ] Si algún par propone una alternativa mejor → nuevo ADR supersede al anterior

---

## Módulo 1 — Core Engine (Mes 1)

> Objetivo: que el pipeline de video edite a nivel producción — rápido, preciso, seguro.

\---

### CORE-01: Face Tracking v2 — Multi-face + Smoothing Mejorado `P0` 🟡 `partial`

**Descripción:**
El `FaceTracker` actual usa MediaPipe Tasks con un modelo `.tflite` estático. Funciona para un solo rostro pero tiene limitaciones críticas:

* **Single-face only:** `max()` sobre detecciones descarta todas las caras excepto la de mayor score.
* **Sin interpolación temporal:** frames sin detección usan el `last\_valid\_point`, causando saltos.
* **Sampling a 3 fps fijo:** insuficiente para movimiento rápido, excesivo para talking heads.
* **Sin cache de modelo:** se reinicializa por cada clip renderizado.

**Tareas técnicas:**

1. Multi-face tracking: trackear N caras simultáneamente, asignar IDs persistentes entre frames (tracking por proximidad).
2. Interpolación temporal: cuando se pierde una detección, interpolar posición entre el último y siguiente punto válido (no repetir el último).
3. Smoothing adaptativo: reemplazar `MovingAverageSmoothing(window=5)` por Exponential Moving Average con factor configurable por estilo de video.
4. Adaptive sampling rate: 1-2 fps para podcast/talking-head, 5+ fps para gaming/acción rápida (derivar del `editing\_style`).
5. Singleton del modelo: cargar `FaceDetector` una vez por worker process, no por clip.

**Archivos a modificar:**

* `services/face\_tracker.py` — refactor completo
* `services/tracking\_utils.py` — agregar EMA smoothing + interpolation
* `services/layouts/` — adaptar para recibir multi-face paths

**Criterios de Aceptación:**

* \[ ] Trackea hasta 3 caras simultáneamente con IDs estables.
* \[ ] Sin saltos visibles entre frames — transiciones suaves.
* \[ ] Sampling rate varía según `editing\_style` del proyecto.
* \[ ] El modelo MediaPipe se carga una sola vez por worker (singleton).
* [ ] Test con video de 2+ personas que demuestre tracking estable.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Refactorizar `FaceTracker` para devolver lista de detecciones (no `max()`) | M | Breaking change para layouts existentes |
| 2 | Implementar tracking por proximidad (Hungarian algorithm o IoU matching) | L | Core del multi-face — asigna IDs estables |
| 3 | Reemplazar `MovingAverageSmoothing` por EMA configurable | S | Factor α por `editing_style` |
| 4 | Interpolar frames sin detección (linear/spline entre puntos válidos) | M | Elimina saltos visuales |
| 5 | Adaptive sampling rate por `editing_style` | S | Config: podcast=1fps, gaming=5fps |
| 6 | Singleton de `FaceDetector` en worker process | S | `worker_init` signal de Celery |
| 7 | Tests: video sintético de 2+ personas, assert IDs estables | M | Generar fixture con MoviePy |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Tests automatizados pasan en CI
- [ ] Layouts existentes (`split`, `versus`, `active`) funcionan sin regresión
- [ ] PR revisado por Par Engine (cross-review)
- [ ] Documentación inline actualizada en `face_tracker.py`

**Riesgos:**
- 🔴 El Hungarian algorithm puede ser lento para >3 caras por frame — limitar a 3 max
- 🟡 MediaPipe Tasks puede no detectar caras de perfil — necesita testing con contenido real
- 🟡 El singleton del modelo puede causar problemas con `fork()` en Celery — testear con `--pool=prefork`

**Dependencia:** CORE-03 (podcast/pip layouts) depende de este multi-face output

---

### CORE-02: Speaker Detection Real — pyannote.audio `P1` 🔴 `missing`

**Descripción:**
`SpeakerTrackingEngine` es un stub: `detect\_active\_speaker\_segment()` devuelve `\[(0, clip.duration, 0)]` hardcodeado. Para el layout `active` (speaker dinámico) necesitamos detección real de quién habla.

**Tareas técnicas:**

1. Integrar `pyannote.audio` para voice activity detection + speaker diarization.
2. Correlacionar segments de audio con las caras detectadas por `FaceTracker` (proximity matching).
3. Generar timeline de speaker activo: `\[(start, end, face\_id), ...]`.
4. El layout `ActiveSpeakerLayout` consume este timeline para switchear entre caras.

**Criterios de Aceptación:**

* \[ ] Diarización funcional: detecta al menos 2 speakers distintos.
* \[ ] Correlación audio → cara con al menos 80% de precisión en videos de podcast.
* \[ ] Fallback graceful: si la diarización falla, usar el face con mayor saliency.
* [ ] No bloquea el pipeline si `pyannote` no está disponible (degradación graceful).

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Investigar licencia de pyannote.audio (es research license — ¿válido para SaaS?) | S | ⚠️ BLOQUEANTE — si no es viable, evaluar alternativas |
| 2 | Integrar VAD (Voice Activity Detection) | M | Detectar quién habla cuándo |
| 3 | Speaker diarization con pyannote pipeline | L | Requiere modelo pre-entrenado (~1GB) |
| 4 | Correlacionar segmentos de audio con face_ids del FaceTracker | L | Proximity + timing matching |
| 5 | Generar timeline: `[(start, end, face_id), ...]` | S | Output format para layouts |
| 6 | Fallback: usar saliency score si diarization falla | M | Graceful degradation |
| 7 | Tests con audio de 2+ speakers | M | Mock del modelo para CI |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Licencia de pyannote verificada como compatible con el proyecto
- [ ] Tests pasan en CI (con modelo mockeado — no descargar 1GB en CI)
- [ ] Pipeline no se rompe si pyannote no está instalado (`try/except` + fallback)

**Riesgos:**
- 🔴 **Licencia**: pyannote.audio usa licencia MIT pero los modelos pre-entrenados tienen restricciones de uso. Verificar ANTES de implementar.
- 🔴 El modelo pesa ~1GB — impacta el tamaño de la imagen Docker del worker
- 🟡 La correlación audio→cara es un problema no trivial — 80% accuracy puede ser optimista

**Dependencia:** Requiere CORE-01 (multi-face tracking) completado

---

### CORE-03: Nuevos Layouts — Podcast, Cinematic, Trending `P0` 🔴 `missing`

**Descripción:**
Actualmente hay 6 layouts (`fill`, `fit`, `blurred`, `split`, `versus`, `active`). `pip` está mapeado a `FitLayout` (placeholder). Para competir con Opus Clips faltan layouts que son estándar en la industria.

**Layouts nuevos a implementar:**

|Layout|Descripción|Referencia|
|-|-|-|
|`podcast`|Split horizontal 50/50 con face tracking independiente por mitad|Riverside.fm|
|`cinematic`|Letterbox 2.35:1 con crop inteligente siguiendo la acción|Film look|
|`pip\_real`|Picture-in-Picture real — video principal + cámara en esquina (configurable)|Loom/OBS|
|`trending`|Template rotativo: borde con gradiente + texto overlay + emoji|TikTok trends|
|`reaction`|Video original arriba, reacción abajo (layout YouTube Shorts)|React content|

**Archivos a crear/modificar:**

* `services/layouts/podcast.py` — nuevo
* `services/layouts/cinematic.py` — nuevo
* `services/layouts/pip\_real.py` — nuevo (reemplazar el placeholder)
* `services/layouts/trending.py` — nuevo
* `services/layouts/reaction.py` — nuevo
* `services/layouts/\_\_init\_\_.py` — registrar en `LAYOUT\_REGISTRY`
* `models.py` — agregar al enum `Layout.choices`

**Criterios de Aceptación:**

* \[ ] Cada layout implementa la interfaz `LayoutStrategy` (método `apply(clip) → clip`).
* \[ ] Todos respetan `target\_w × target\_h` y producen dimensiones pares (H.264 safe).
* \[ ] `podcast` y `pip\_real` usan multi-face tracking de CORE-01.
* \[ ] `trending` acepta parámetros configurables (color de borde, texto overlay).
* [ ] Tests visuales: render de un clip de 5s con cada layout nuevo.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Implementar `PodcastLayout` (split 50/50 + face tracking por mitad) | L | Depende de CORE-01 multi-face |
| 2 | Implementar `CinematicLayout` (letterbox 2.35:1 + saliency crop) | M | Puede usar saliency_tracker existente |
| 3 | Implementar `PipRealLayout` (PiP con posición configurable) | M | Reemplaza placeholder actual |
| 4 | Implementar `TrendingLayout` (borde gradiente + texto overlay) | L | Parámetros configurables por proyecto |
| 5 | Implementar `ReactionLayout` (video original + reacción) | M | Layout YouTube Shorts standard |
| 6 | Registrar en `LAYOUT_REGISTRY` + actualizar `Layout.choices` | S | Incluye migración de DB |
| 7 | Tests visuales: script que renderiza 5s con cada layout nuevo | M | Genera PNG/MP4 para visual review |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Cada layout tiene test automatizado (assert dimensiones, assert no crash)
- [ ] Migración de DB para nuevos choices
- [ ] PR revisado por Par IA (cross-review — ellos consumen los layouts)

**Riesgos:**
- 🟡 `PodcastLayout` depende de CORE-01 multi-face — si se retrasa, este layout se bloquea
- 🟡 `TrendingLayout` requiere assets (gradientes, emojis) — definir de dónde vienen
- 🟢 Los layouts son independientes entre sí — se pueden implementar en paralelo

**Dependencia:** CORE-01 (multi-face tracking) para `podcast` y `pip_real` layouts

---

### CORE-04: Optimización de Performance del Worker `P0` 🟡 `partial`

**Descripción:**
El worker Celery actual procesa todo secuencialmente en un solo task. Un video de 763s tardó \~7 min solo en transcripción (CPU). Para competir con Opus Clips necesitamos resultados en <3 minutos para un video de 10 min.

**Tareas técnicas:**

1. **GPU acceleration para Whisper** (si disponible):

   * Detectar CUDA/MPS al iniciar el worker.
   * Usar modelo `small` con GPU vs `base` en CPU (mismo accuracy, 5x speed).
2. **Parallel task chains:**

   * Separar el pipeline monolítico en sub-tasks encadenadas con `celery.chain()`:

```
     upload\_to\_r2 | generate\_proxy | transcribe | ai\_select
     ```

   * `transcribe` y `generate\_proxy` pueden correr en paralelo con `celery.group()`.
3. **Whisper model preloading:**

   * Precargar el modelo Whisper al iniciar el worker (signal `worker\_init`), no al procesar cada video.
   * Cache en variable global del process.
4. **FFmpeg optimization:**

   * Proxy generation: usar `-preset ultrafast` en vez de default.
   * Render final: pool de threads para renderizar múltiples clips en paralelo.
5. **Memory management:**

   * Configurar `--max-memory-per-child` en Celery para reciclar workers que acumulan memoria.
   * Cerrar explícitamente clips MoviePy en `finally` blocks (ya parcial).

**Archivos a modificar:**

* `backend/celery.py` — worker signals, memory config
* `tasks.py` — refactorizar en sub-tasks + chain/group
* `services/transcription\_engine.py` — GPU detect, model preload
* `utils/ffmpeg\_utils.py` — ultrafast preset

**Criterios de Aceptación:**

* \[ ] Video de 10 min procesado en <3 min con GPU, <6 min en CPU.
* \[ ] Whisper model se carga una sola vez por worker process.
* \[ ] Proxy generation y transcripción corren en paralelo.
* \[ ] Workers se reciclan después de 50 tareas (`max\_tasks\_per\_child`).
* [ ] Métricas de tiempo por etapa logueadas para benchmarking.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | GPU detection (CUDA/MPS) al iniciar worker | S | Ya parcial en TranscriptionEngine |
| 2 | Whisper model preloading via `worker_init` signal | M | Singleton en variable global del process |
| 3 | Refactorizar `tasks.py` en sub-tasks + `celery.chain()` / `group()` | L | Breaking change — requiere testing E2E |
| 4 | FFmpeg proxy generation con `-preset ultrafast` | S | Cambio en ffmpeg_utils.py |
| 5 | `max_tasks_per_child` + memory monitoring | S | Config en celery.py |
| 6 | Evaluar migración a `faster-whisper` (CTranslate2) | M | 4x speedup potencial — PoC |
| 7 | Métricas de tiempo por etapa (structured logging) | M | Base para INFRA-02 dashboard |
| 8 | Benchmark: video de 10min, medir antes vs después | S | Documentar resultados |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Benchmark documentado con tiempos antes/después
- [ ] No hay regresión en calidad de output
- [ ] Celery chain/group funciona con retry y error handling

**Riesgos:**
- 🔴 La refactorización de `tasks.py` en sub-tasks es un cambio grande — puede romper el pipeline existente
- 🟡 `faster-whisper` tiene API diferente a `openai-whisper` — requiere adaptar `TranscriptionEngine`
- 🟡 GPU detection puede fallar silenciosamente — necesita logging explícito

**Dependencia:** Impacta a todos los módulos — es infraestructura transversal

---

### CORE-05: Security Layer — AI Guardrails + Input Validation `P0` 🟡 `partial`

**Descripción:**
`AI\_Security\_Shield` existe en `core/security.py` (aislamiento de input para prompts). Pero faltan guardrails críticos:

**Tareas técnicas:**

1. **Validación de video input robusta:**

   * Verificar MIME type real con `python-magic` (ya está en requirements pero no se usa en el upload).
   * Limitar resolución máxima de input (4K = OK, 8K = rechazar).
   * Scan de duración máxima contra el plan del workspace ANTES de subir a R2.
2. **Output sanitization de IA:**

   * Validar que el JSON del `SelectionEngine` tenga timestamps dentro del rango del video.
   * Validar que `virality\_score` esté entre 0-100.
   * Rechazar respuestas con código ejecutable o prompts inyectados.
3. **Rate limiting por workspace:**

   * Máximo N proyectos concurrentes por workspace (según plan).
   * Cooldown entre uploads (anti-abuse).
4. **Audit trail:**

   * Loguear todo prompt enviado a OpenAI en `ai\_rationale\_log`.
   * Loguear input/output de cada servicio de IA para debugging.

**Criterios de Aceptación:**

* \[ ] Video con MIME falso (`.mp4` que es `.txt`) se rechaza con error claro.
* \[ ] JSON de SelectionEngine con timestamps fuera de rango se descarta (no crashea).
* \[ ] Rate limit configurable por plan: free=2 concurrent, pro=10 concurrent.
* [ ] Todo prompt enviado al LLM queda registrado en `ai_rationale_log`.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Integrar `python-magic` en upload endpoint para MIME validation real | S | Ya está en requirements, no se usa |
| 2 | Validación de resolución y duración pre-upload | S | Contra plan del workspace |
| 3 | Output sanitization: JSON schema validation para respuestas LLM | M | Pydantic models para cada output |
| 4 | Rate limiting con django-ratelimit o manual con Valkey | M | Configurable por plan |
| 5 | Audit trail: modelo `AIAuditLog` con prompt, response, timestamps | M | Para debugging y compliance |
| 6 | Adaptar `AI_Security_Shield` al proveedor elegido en IA-00 | M | Depende de la decisión de LLM provider |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Tests de seguridad: fuzzing con inputs maliciosos (MIME fake, prompt injection, oversized files)
- [ ] Rate limiting testeado con requests concurrentes
- [ ] Audit log consultable desde Django Admin

**Riesgos:**
- 🔴 **Depende de IA-00**: si se cambia de OpenAI, la Moderation API necesita reemplazo
- 🟡 `python-magic` requiere `libmagic` instalado en la imagen Docker — verificar Dockerfile
- 🟡 Rate limiting con Valkey requiere que el servicio redis esté saludable

**Dependencia:** IA-00 (selección de proveedor LLM) para la parte de moderation y audit trail

---

### CORE-06: Tests de Integración del Pipeline `P0` 🟡 `partial`

**Descripción:**
Tests existentes son mínimos. Para un core robusto necesitamos coverage del camino crítico.

**Criterios de Aceptación:**

* \[ ] Test E2E: upload video 5s → transcription (Whisper tiny) → selection (mock OpenAI) → assert `awaiting\_approval`.
* \[ ] Test de `PricingEngine`: todos los multiplicadores, edge cases, planes free vs pro.
* \[ ] Test de `WalletService`: reserve + commit, reserve + rollback, insufficient funds, concurrent reserve (race condition).
* \[ ] Test de `CloudflareR2Manager`: `USE\_S3=False` mode (local bypass).
* \[ ] Test de cada `LayoutStrategy.apply()` con un clip sintético de 2s.
* [ ] Mock de LLM en todos los tests (no gastar créditos en CI).

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Test E2E del pipeline completo con video sintético | L | Genera video de 5s con MoviePy, corre todo |
| 2 | Test suite de `PricingEngine` | M | Multiplicadores, edge cases, planes |
| 3 | Test suite de `WalletService` (incluye race condition con `select_for_update`) | L | Concurrencia es el caso difícil |
| 4 | Test de `CloudflareR2Manager` en modo local | S | `USE_S3=False` bypass |
| 5 | Test de cada `LayoutStrategy.apply()` con clip sintético | M | Assert dimensiones pares, no crash |
| 6 | Fixtures: video sintético de 2s + transcripción mock + LLM response mock | M | Compartidas entre tests |
| 7 | CI config para correr tests (prerequisito de INFRA-01) | S | `pytest.ini` + `conftest.py` |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Coverage ≥70% del camino crítico
- [ ] Todos los tests pasan offline (sin APIs externas)
- [ ] `conftest.py` con fixtures reutilizables documentadas
- [ ] Test execution time <2 min (para que no sea excusa para no correrlos)

**Riesgos:**
- 🟡 Tests de video son lentos (MoviePy rendering) — usar video lo más corto posible (2s)
- 🟡 Race condition tests son flaky por naturaleza — usar `select_for_update` + transaction isolation

---

## Módulo 2 — Prompt-to-Edit Engine (Mes 2)

> Objetivo: el usuario describe lo que quiere en lenguaje natural y la IA edita el video.

\---

### PTE-01: Motor de Prompt-to-Edit — Diseño e Implementación `P0` 🔴 `missing`

**Descripción:**
Sistema nuevo donde el usuario escribe un prompt de edición (ej: "Cortá los mejores momentos de humor, poné subtítulos grandes amarillos, layout vertical con fondo borroso") y el sistema traduce eso a configuración de proyecto + selección de clips.

**Arquitectura propuesta:**

```
User Prompt
     │
     ▼
┌─────────────────┐
│ PromptParser     │  LLM via LLMGateway (proveedor definido en IA-00)
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
  "editing\_style": "dynamic",
  "aspect\_ratio": "9:16",
  "render\_layout": "blurred",
  "add\_subtitles": true,
  "subtitle\_color": "#FFFF00",
  "subtitle\_size": "large",
  "max\_clips": 3,
  "clip\_selection\_criteria": "humor and engagement peaks",
  "remove\_silences": true,
  "use\_facetracking": true
}
```

**Archivos a crear:**

* `services/prompt\_parser.py` — LLM prompt → ProjectConfig
* `services/prompt\_validator.py` — guardrails + plan validation
* `views.py` — nuevo endpoint `POST /api/v1/projects/from-prompt/`

**Criterios de Aceptación:**

* \[ ] `POST /api/v1/projects/from-prompt/` acepta `{ "prompt": "...", "source\_file": <video> }`.
* \[ ] El LLM genera un `ProjectConfig` JSON válido usando Structured Outputs.
* \[ ] Config se valida contra el plan del user (duración, resolución, features).
* \[ ] El pipeline existente se reutiliza al 100% — no se duplica lógica.
* \[ ] Prompts ambiguos generan config con defaults sensatos (no errores).
* [ ] Prompt injection attempts se neutralizan via `AI_Security_Shield`.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Diseñar Pydantic model `ProjectConfig` con todos los campos del proyecto | M | Reutilizar fields del modelo `Project` existente |
| 2 | Implementar `PromptParser` usando `LLMGateway` (salida de IA-00) | L | Core del motor — prompt engineering intensivo |
| 3 | Implementar `ConfigValidator` (valida contra plan + límites del sistema) | M | Reutilizar lógica de `PricingEngine` |
| 4 | Endpoint `POST /api/v1/projects/from-prompt/` | M | Serializer + view + URL routing |
| 5 | Tests: prompt ambiguo → defaults sensatos, prompt injection → neutralizado | M | Contract tests con mocked LLM |
| 6 | Prompt library: 10-15 test prompts con expected output para regresión | S | Fixture de golden tests |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Prompt funciona con el proveedor LLM elegido en IA-00
- [ ] `PromptParser` usa `LLMGateway`, no importa ningún SDK de proveedor directamente
- [ ] 10+ test prompts con golden output documentados
- [ ] PR revisado por Par Engine (cross-review)

**Riesgos:**
- 🔴 **Bloqueado por IA-00** — no empezar hasta que el proveedor LLM esté definido
- 🟡 La calidad del prompt engineering determina la calidad del producto — iterar múltiples veces
- 🟡 Structured Outputs pueden variar entre proveedores — `instructor` library mitiga

**Dependencia:** IA-00 (proveedor LLM) es BLOQUEANTE. CORE-05 (AI guardrails) para la protección de prompts.

---

### PTE-02: Prompt-to-Edit — Selección Dirigida por Prompt `P1` 🔴 `missing`

**Descripción:**
Extender el `SelectionEngine` para que acepte criterios de selección del prompt del usuario (ej: "momentos de humor", "picos de emoción", "partes educativas") además de la detección genérica de viralidad.

**Criterios de Aceptación:**

* \[ ] `SelectionEngine.select\_viral\_clips()` acepta un parámetro `selection\_criteria: str` opcional.
* \[ ] El criteria del usuario se inyecta en el prompt del LLM de forma segura (aislado).
* \[ ] El `ai\_reasoning` de cada clip refleja cómo el criteria influyó en la selección.
* [ ] Sin criteria explícito, el comportamiento default no cambia.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Agregar parámetro `selection_criteria: Optional[str]` a `select_viral_clips()` | S | No rompe la interfaz existente |
| 2 | Inyectar criteria en el prompt del LLM con aislamiento de seguridad | M | Usar `AI_Security_Shield.isolate_user_input()` |
| 3 | Modificar prompt template para incorporar criteria del usuario | M | Prompt engineering |
| 4 | Agregar `ai_reasoning` con referencia explícita al criteria | S | El LLM debe explicar cómo influyó |
| 5 | Tests: criteria custom vs default, verificar que la selección difiere | M | Golden tests con mocked LLM |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Backward compatible — sin `selection_criteria`, el behavior es idéntico al actual
- [ ] Tests de regresión para el flow default

**Riesgos:**
- 🟡 El criteria del usuario puede ser vago ("hacelo lindo") — el LLM debe manejar ambigüedad
- 🟡 Criteria malicioso (prompt injection via criteria) — mitigar con `isolate_user_input()`

**Dependencia:** IA-00 (proveedor LLM). PTE-01 (PromptParser genera el criteria).

---

### PTE-03: Preview Pipeline — Pre-render Rápido `P1` 🔴 `missing`

**Descripción:**
Generar un preview de baja resolución (360p, sin subtítulos complejos) antes del render final para que el usuario vea qué va a obtener sin gastar tokens de render completo.

**Criterios de Aceptación:**

* \[ ] `POST /api/v1/clips/{id}/preview/` genera un render 360p en <30s.
* \[ ] El preview usa el layout seleccionado pero sin subtítulos animados (solo texto plano).
* \[ ] El preview se almacena temporalmente (TTL 1h, auto-cleanup).
* [ ] No consume tokens del wallet (es gratuito).

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | `RenderEngine.render_preview()` — render 360p simplificado | M | Reutilizar `render_clip()` con params reducidos |
| 2 | Endpoint `POST /api/v1/clips/{id}/preview/` | S | View + serializer |
| 3 | TTL cleanup: Celery Beat task para eliminar previews >1h | M | Requiere DIST-03 (Beat setup) o cron manual |
| 4 | Bypass de wallet — previews no descuentan tokens | S | Flag en `PricingEngine` |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Preview se genera en <30s para un clip de 30s
- [ ] Cleanup automático funciona (no se acumulan previews en storage)

**Riesgos:**
- 🟡 Sin Celery Beat configurado (DIST-03), el cleanup requiere cron manual
- 🟢 Riesgo bajo — es una versión simplificada de funcionalidad existente

**Dependencia:** CORE-03 (layouts) para que el preview use el layout correcto. DIST-03 (Beat) para cleanup automático.

---

## Módulo 3 — Monetización (Mes 2)

\---

### PAY-01: Watermark Injection en Render (Plan Free) `P0` 🟡 `partial`

**Descripción:**
`SubscriptionPlan.has\_watermark` existe. Falta la inyección física en `RenderEngine.render\_clip()`.

**Criterios de Aceptación:**

* \[ ] Si `plan.has\_watermark == True`, el render superpone el logo de OneCreator.
* \[ ] Watermark: semi-transparente (40% opacity), esquina inferior derecha, 15% del ancho del video.
* \[ ] Asset del watermark en `back/assets/watermarks/onecreator\_logo.png`.
* \[ ] Server-side enforced — no bypasseable desde la API.
* [ ] Test: render con y sin watermark produce archivos diferentes.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Watermark NO removible manipulando la API (server-side enforced)
- [ ] Asset PNG del watermark commiteado en el repo
- [ ] Test automatizado: hash de output difiere con/sin watermark

**Riesgos:**
- 🟢 Riesgo bajo — es un overlay de imagen sobre video, MoviePy lo resuelve nativo
- 🟡 El asset del watermark necesita ser creado (diseño gráfico)

---

### PAY-02: Stripe Checkout — Compra de Tokens `P1` 🔴 `missing`

**Descripción:**
`stripe\_service.py` es un stub vacío. Implementar Stripe Checkout Session para compra de tokens.

**Criterios de Aceptación:**

* \[ ] `POST /api/v1/payments/checkout/` crea un Stripe Checkout Session.
* \[ ] Webhook `POST /api/v1/payments/webhook/` procesa `checkout.session.completed`.
* \[ ] Tokens se acreditan en la Wallet tras pago exitoso (`Transaction.Type.DEPOSIT`).
* \[ ] Webhook verificado con `stripe.Webhook.construct\_event()` (signature validation).
* [ ] Idempotencia: mismo evento procesado 2 veces no duplica créditos.

**Subtareas con estimación:**

| # | Subtarea | Talla | Notas |
|---|---------|-------|-------|
| 1 | Implementar `create_checkout_session()` en `stripe_service.py` | M | Stripe SDK + product/price setup |
| 2 | Webhook handler con signature validation | M | Seguridad crítica — no aceptar eventos sin validar |
| 3 | Acreditación atómica en Wallet (`Transaction.Type.DEPOSIT`) | M | Usar `select_for_update` para evitar race conditions |
| 4 | Idempotency key basada en `checkout.session.id` | S | Prevenir doble acreditación |
| 5 | Tests con Stripe CLI (`stripe listen --forward-to`) | M | Testing local de webhooks |

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Webhook NO acepta eventos sin signature válida
- [ ] Idempotencia verificada con test de doble envío
- [ ] `STRIPE_SECRET_KEY` y `STRIPE_WEBHOOK_SECRET` en `.env.example`
- [ ] Documentación de setup de productos/precios en Stripe Dashboard

**Riesgos:**
- 🔴 **Seguridad financiera**: un webhook sin validar permite acreditar tokens gratis. La signature validation es OBLIGATORIA.
- 🟡 Stripe requiere HTTPS para webhooks en producción — necesita SSL configurado
- 🟡 Los precios deben crearse manualmente en Stripe Dashboard o via API — documentar el proceso

---

### PAY-03: Endpoint de Planes Públicos `P0` 🟡 `partial`

**Descripción:**
El modelo `SubscriptionPlan` existe completo. Falta un endpoint público que liste los planes.

**Criterios de Aceptación:**

* \[ ] `GET /api/v1/plans/` (público, sin auth) devuelve planes con pricing y features.
* \[ ] Incluye: `name`, `max\_video\_duration\_seconds`, `max\_resolution`, features flags, `base\_discount\_rate`.
* [ ] Solo planes con `is_active=True`.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Endpoint accesible sin autenticación (público)
- [ ] Response cacheada (1h TTL) para evitar queries innecesarias
- [ ] Schema documentado en Spectacular/Swagger

**Riesgos:**
- 🟢 Riesgo mínimo — es un endpoint de lectura simple

---

## Módulo 4 — Distribución Social (Mes 3)

> Objetivo: publicación directa multi-plataforma. Ver [ADR-005](adrs/005-social-publishing-strategy.md) para la evaluación OAuth directo vs Ayrshare.

\---

### DIST-01: Social Auth — OAuth Flows por Plataforma `P0` 🔴 `missing`

**Descripción:**
Para publicar en redes sin Ayrshare como intermediario, necesitamos OAuth directo con cada plataforma. El archivo `social\_auth.py` existe pero hay que implementar los flows.

**Plataformas target (MVP):**

|Plataforma|API|OAuth|Dificultad|
|-|-|-|-|
|TikTok|Content Posting API|OAuth 2.0|Media — requiere app review|
|YouTube|YouTube Data API v3|OAuth 2.0 (Google)|Baja — bien documentado|
|Instagram|Instagram Graph API|OAuth via Facebook|Alta — requiere Business account + app review|

**Criterios de Aceptación:**

* \[ ] Modelo `SocialAccount` con campos: `platform`, `access\_token`, `refresh\_token`, `expires\_at`, `profile\_data`.
* \[ ] `GET /api/v1/social/connect/{platform}/` inicia el OAuth flow (redirect a la plataforma).
* \[ ] `GET /api/v1/social/callback/{platform}/` procesa el callback y almacena tokens.
* \[ ] Tokens se encriptan en DB (no plain text).
* [ ] Refresh automático de tokens expirados antes de publicar.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Tokens encriptados con `Fernet` o equivalente (no AES manual)
- [ ] Refresh flow testeado con tokens expirados (mock)
- [ ] ADR-005 actualizado con la decisión final post-evaluación

**Riesgos:**
- 🔴 **App review de TikTok e Instagram puede tardar semanas/meses** — iniciar el proceso ASAP
- 🔴 Tokens de acceso en DB son un target de seguridad — encriptar SIEMPRE
- 🟡 Cada plataforma tiene quirks en el OAuth flow — YouTube es el más simple, empezar por ahí

**Dependencia:** ADR-005 (estrategia de publicación social)

---

### DIST-02: Publishing Engine — Publicación Directa Multi-plataforma `P0` 🔴 `missing`

**Descripción:**
Motor de publicación que sube el video directamente a cada API de plataforma, sin depender de Ayrshare. Reemplaza `AyrshareClient.send\_post()`.

**Criterios de Aceptación:**

* \[ ] `PublishingEngine.publish(clip\_id, platform, caption, hashtags)` sube el video directamente.
* \[ ] Adapta el video a los requisitos de cada plataforma:

  * TikTok: max 10 min, aspect ratio 9:16, max 287 MB.
  * YouTube Shorts: max 60s, vertical, max 256 MB.
  * Instagram Reels: max 90s, min 3s, max 1 GB.
* \[ ] Retry con backoff exponencial si la API falla.
* \[ ] Status tracking: actualiza `ScheduledPost.status` en tiempo real.
* [ ] Fallback a Ayrshare si la publicación directa falla y Ayrshare está configurado.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Al menos YouTube funciona end-to-end (la plataforma más simple)
- [ ] Retry con backoff exponencial (3 intentos, 1s/5s/30s)
- [ ] Status tracking visible en la API (polling o webhook)

**Riesgos:**
- 🔴 Cada API tiene rate limits diferentes y eráticos — implementar circuit breaker
- 🟡 Videos grandes (>100MB) pueden hacer timeout en upload — usar resumable uploads donde esté disponible
- 🟡 Las APIs de redes sociales cambian frecuentemente — abstraer con Strategy Pattern

---

### DIST-03: Scheduling Engine — Celery Beat + Calendar `P0` 🟡 `partial`

**Descripción:**
`ScheduledPost` model y `dispatch\_scheduled\_posts\_batch` task existen. Falta Celery Beat y endpoints REST.

**Criterios de Aceptación:**

* \[ ] Servicio `beat` en `docker-compose.yml`.
* \[ ] `dispatch\_scheduled\_posts\_batch` corre cada 5 min vía `CELERY\_BEAT\_SCHEDULE`.
* \[ ] CRUD completo: `POST/GET/PATCH/DELETE /api/v1/scheduled-posts/`.
* \[ ] `GET /api/v1/scheduled-posts/calendar/?from=...\&to=...` devuelve posts por día.
* [ ] SEO metadata auto-generado al crear un scheduled post (via `SEOOptimizationService`).

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Servicio `beat` en `docker-compose.yml` funcionando
- [ ] Posts se despachan dentro de ±5 min de la hora programada
- [ ] CRUD con permisos (solo el dueño del workspace puede CRUD sus posts)

**Riesgos:**
- 🟡 Celery Beat requiere un scheduler store (django-celery-beat o database scheduler) — elegir implementación
- 🟡 Si el worker se cae, los posts programados se pierden hasta que se recupere — persistence en DB mitiga

---

### DIST-04: Adaptación de Contenido por Plataforma `P1` 🟡 `partial`

**Descripción:**
`SEOOptimizationService` genera `platform\_tweaks`. Extender para que la adaptación sea más profunda: re-encode del video si es necesario (ej: YouTube Shorts requiere ≤60s).

**Criterios de Aceptación:**

* \[ ] Si el clip excede la duración máxima de la plataforma, ofrecer trimming automático.
* \[ ] Caption y hashtags adaptados por plataforma (límites: TikTok 2200 chars, IG 2200, YT 5000).
* [ ] El usuario puede override el caption generado antes de confirmar.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Trimming automático produce video válido (no corta en medio de palabra)
- [ ] Caption limits por plataforma documentados en constantes

---

## Módulo 5 — Frontend (Mes 3, últimas semanas)

> Prioridad baja. MVP funcional mínimo que demuestre el core.

\---

### FE-01: Setup + Auth + Dashboard `P0` 🔴 `missing`

**Criterios de Aceptación:**

* \[ ] App React/Next.js inicializada con design system base.
* \[ ] Login/registro con JWT.
* [ ] Dashboard con lista de proyectos y balance del wallet.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] JWT flow completo (login, refresh, logout)
- [ ] Responsive (mobile-first)
- [ ] Consume API real del backend (no mocks)

---

### FE-02: Upload + Configuración de Proyecto `P0` 🔴 `missing`

**Criterios de Aceptación:**

* \[ ] Drag \& drop de video o input de URL.
* \[ ] Wizard de configuración: estilo, layout, subtítulos.
* \[ ] Campo de prompt para Prompt-to-Edit (si PTE-01 está implementado).
* [ ] Feedback visual de progreso del pipeline.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Upload con progress bar funcional
- [ ] Si PTE-01 está implementado, el campo de prompt está disponible
- [ ] Polling o WebSocket para estado del pipeline en tiempo real

**Dependencia:** PTE-01 (campo de prompt). Backend API completa para uploads.

---

### FE-03: Revisión de Clips + Publish `P1` 🔴 `missing`

**Criterios de Aceptación:**

* \[ ] Vista de clips propuestos con score, reasoning, preview.
* \[ ] Aprobar/rechazar clips.
* [ ] Programar publicación desde la vista del clip.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Preview del clip reproduce inline (video player)
- [ ] Flow de publicación integrado con DIST-03 (scheduling)

**Dependencia:** PTE-03 (preview), DIST-03 (scheduling), DIST-01 (social auth para publicar).

---

## Módulo 6 — Infraestructura y DevOps

> Ver [ADR-006](adrs/006-deployment-strategy.md) para la evaluación de estrategia de deployment.

\---

### INFRA-01: CI/CD — GitHub Actions `P1` 🔴 `missing`

**Criterios de Aceptación:**

* \[ ] Workflow en cada PR: `manage.py check`, `makemigrations --check`, `test`.
* \[ ] Build de imágenes Docker para verificar Dockerfile.
* [ ] Branch protection: merge bloqueado si checks fallan.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Pipeline corre en <5 min para no bloquear PRs
- [ ] ADR-006 actualizado con la decisión de deployment
- [ ] Documentación de cómo agregar nuevos checks

**Dependencia:** CORE-06 (tests deben existir para que CI los corra)

---

### INFRA-02: Monitoring y Logging Estructurado `P2` 🔴 `missing`

**Criterios de Aceptación:**

* \[ ] Logs JSON estructurados (no plain text) para búsqueda en producción.
* \[ ] Métricas de tiempo por etapa del pipeline (dashboard).
* [ ] Alertas cuando un task falla N veces consecutivas.

**Definition of Done:**
- [ ] Criterios de aceptación cumplidos
- [ ] Logs parseables por herramientas de observabilidad (Datadog, Grafana, etc.)
- [ ] Al menos 1 dashboard con métricas del pipeline

---

## Asignación por Pares (Pair Coding)

### 🧠 Par IA — Dev 1 (vos) + Dev 2

> Foco: todo lo que involucra modelos de ML, LLMs, pipelines de IA y sus guardrails.

|Issue|Título|Prioridad|Estado|
|-|-|-|-|
|**IA-00**|**Evaluación y Selección de Proveedor LLM**|**`P0`**|**🔴 `missing` ⛔ BLOQUEANTE**|
|ARCH-01|Revisión de ADRs por Par|`P0`|🔴 `missing`|
|CORE-01|Face Tracking v2 — Multi-face + Smoothing|`P0`|🟡 `partial`|
|CORE-02|Speaker Detection — pyannote.audio|`P1`|🔴 `missing`|
|CORE-05|Security Layer — AI Guardrails + Input Validation|`P0`|🟡 `partial`|
|PTE-01|Motor de Prompt-to-Edit|`P0`|🔴 `missing`|
|PTE-02|Selección Dirigida por Prompt|`P1`|🔴 `missing`|

**Rationale:** IA-00 es la primera tarea porque define qué proveedor LLM usa todo el producto. CORE-01 y CORE-02 son ML puro (MediaPipe, pyannote). PTE-01/02 son el corazón LLM del producto. CORE-05 protege toda la superficie de IA.

\---

### ⚙️ Par Engine — Dev 3 + Dev 4

> Foco: rendimiento del pipeline, layouts de video, infra y calidad del código.

|Issue|Título|Prioridad|Estado|
|-|-|-|-|
|CORE-03|Nuevos Layouts — Podcast, Cinematic, Trending|`P0`|🔴 `missing`|
|CORE-04|Optimización de Performance del Worker|`P0`|🟡 `partial`|
|CORE-06|Tests de Integración del Pipeline|`P0`|🟡 `partial`|
|PTE-03|Preview Pipeline — Pre-render Rápido|`P1`|🔴 `missing`|
|INFRA-01|CI/CD — GitHub Actions|`P1`|🔴 `missing`|
|INFRA-02|Monitoring y Logging Estructurado|`P2`|🔴 `missing`|

**Rationale:** Layouts (CORE-03) y performance (CORE-04) comparten contexto de FFmpeg/MoviePy/Celery. PTE-03 (preview) depende de los layouts ya implementados. Tests (CORE-06) e infra (INFRA-01/02) aseguran la calidad de todo lo que produce este par y el par de IA.

\---

### 🚀 Par Producto — Dev 5 + Dev 6

> Foco: monetización, distribución social y la cara visible del producto (frontend).

|Issue|Título|Prioridad|Estado|
|-|-|-|-|
|PAY-01|Watermark Injection en Render|`P0`|🟡 `partial`|
|PAY-02|Stripe Checkout — Compra de Tokens|`P1`|🔴 `missing`|
|PAY-03|Endpoint de Planes Públicos|`P0`|🟡 `partial`|
|DIST-01|Social Auth — OAuth Flows|`P0`|🔴 `missing`|
|DIST-02|Publishing Engine — Multi-plataforma|`P0`|🔴 `missing`|
|DIST-03|Scheduling Engine — Celery Beat + Calendar|`P0`|🟡 `partial`|
|DIST-04|Adaptación de Contenido por Plataforma|`P1`|🟡 `partial`|
|FE-01|Setup + Auth + Dashboard|`P0`|🔴 `missing`|
|FE-02|Upload + Configuración de Proyecto|`P0`|🔴 `missing`|
|FE-03|Revisión de Clips + Publish|`P1`|🔴 `missing`|

**Rationale:** Monetización (PAY-*) y distribución (DIST-*) son dominio de producto. Frontend (FE-\*) es la capa que consume ambos. Un par que domine los 3 módulos evita handoffs innecesarios y puede iterar rápido en el flujo completo del usuario.

\---

### 📌 Dependencias entre Pares

```
Par IA ──────────────────────────────────────────► Par Engine
  CORE-01 (multi-face) ──► CORE-03 (podcast/pip layouts lo consumen)
  CORE-05 (AI guardrails) ──► CORE-06 (tests deben cubrir guardrails)
  PTE-01 (prompt-to-edit) ──► PTE-03 (preview usa la config generada)

Par Engine ──────────────────────────────────────► Par Producto
  CORE-03 (layouts) ──► PAY-01 (watermark se inyecta en render)
  CORE-04 (worker perf) ──► DIST-03 (scheduling usa workers optimizados)

Par IA ──────────────────────────────────────────► Par Producto
  PTE-01 (prompt parser) ──► FE-02 (wizard incluye campo de prompt)
```

### 🔄 Sincronización sugerida

|Frecuencia|Actividad|
|-|-|
|**Diaria**|Standup async (Slack/Discord) — blockers y PRs pendientes de review|
|**Semanal**|Sync entre pares — revisar dependencias cruzadas y interfaces compartidas|
|**Por milestone**|Demo interna — cada par muestra su progreso al resto del equipo|

\---

## Resumen Ejecutivo

|Prioridad|Issues|Core/Backend|Frontend|
|-|-|-|-|
|**P0**|15|13|2|
|**P1**|7|6|1|
|**P2**|1|1|0|
|**Total**|**23**|**20**|**3**|

### Diferenciadores vs Opus Clips

|Feature|Opus Clips|OneCreator (Target)|
|-|-|-|
|Clip selection AI|GPT-4 (locked)|Multi-proveedor configurable (ADR-001)|
|Face tracking|Básico|Multi-face + speaker detection|
|Layouts|~5|11+ (con podcast, cinematic, trending)|
|Prompt-to-Edit|No|Sí — lenguaje natural → config|
|Social publishing|Integración limitada|Directo via OAuth + fallback Ayrshare (ADR-005)|
|Watermark (free)|Sí|Sí|
|Subtítulos|Estáticos|Word-level animados + traducción|
|Transparencia IA|No|Sí — rationale log + audit trail explicable|
|Arquitectura documentada|No público|Sí — ADRs públicos, stack justificado|



