# AI-DECISIONS.md — Registro de Uso de IA

> Auditoría obligatoria — Ingeniería de Software en la Nube, UTN FRLP 2026.

---

# Fase 0 — Desarrollo Pre-Académico

| # | Fecha | Artefacto | IA Utilizada | Rol de la IA | Rol del Desarrollador |
|---|-------|-----------|-------------|-------------|----------------------|
| 1 | 2026-05 | Arquitectura base, modelos Django | Claude, GPT-4 | Scaffolding de código a partir de decisiones de diseño | Diseño de dominios, relaciones entre entidades, decisiones de arquitectura |
| 2 | 2026-06 | Motor de transcripción (Whisper) | Claude | Integración de Whisper local con word-level timestamps | Decisión de usar Whisper local vs API, configuración de modelos |
| 3 | 2026-06 | Motor de selección de clips (LLM) | Claude | Implementación del patrón Strategy y llamadas a OpenAI | Diseño del patrón, criterios de selección viral, prompt engineering |
| 4 | 2026-06/07 | Render engine + layouts de video | Claude | Implementación de layouts con MoviePy 2.0 | Definición de layouts, interfaz Strategy, debugging de renders |
| 5 | 2026-06/07 | Face tracking (MediaPipe) | Claude | Integración de MediaPipe Tasks API | Elección de MediaPipe sobre alternativas, calibración visual de encuadre |
| 6 | 2026-07 | Motor de subtítulos | Claude | Implementación iterativa (5 versiones hasta Pillow) | Evaluación visual de cada versión, dirección de la migración a Pillow |
| 7 | 2026-07 | Sistema financiero (wallet + pricing) | Claude | Implementación de transacciones atómicas y pricing engine | Diseño del modelo económico, reglas de negocio, planes de suscripción |
| 8 | 2026-07 | Migración de storage a Cloudflare R2 | Claude | Re-implementación del cliente S3 para R2 | Decisión estratégica de migración por zero egress fees |
| 9 | 2026-07/08 | Seguridad IA + SEO engine | Claude | Implementación de prompt injection shield y metadata social | Identificación de vectores de ataque, diseño de schemas |
| 10 | 2026-08 | Sanitización del repositorio | Claude Opus 4.6 | Auditoría de secretos, fix de bugs, documentación | Autorización de cada cambio, decisiones legales (CLA, licencia) |

---

# Fase 1 — Entregables Académicos

| # | Fecha | Entregable | IA Utilizada | Rol de la IA | Rol del Equipo |
|---|-------|-----------|-------------|-------------|----------------|
| 11 | 2026-08-25 | `documents/entregas/tp1.md` (One-Pager) | Claude Opus 4.6 | Síntesis del stack real del repo en formato académico | Definición de consigna, verificación técnica, condensación |
| 12 | 2026-08-22 | `documents/backlog_mvp.md` (Backlog v2) | Claude Opus 4.6 | Análisis de código existente para generar issues con estado | Prioridades estratégicas, decisiones de producto, requerimientos |
| 13 | 2026-08-28 | `docker-compose.yml` — Migración Redis → Valkey | Claude Opus 4.6 | Reemplazo de imagen `redis:7-alpine` por `valkey/valkey:8-alpine` en Docker Compose | Decisión estratégica de migración, validación de compatibilidad de protocolo, aprobación de cambios |
| 14 | 2026-09-13 | ADR-001, ADR-002, ADR-006, ADR-007 y plan del core de IA | OpenAI Codex | Revisión del core existente, contraste con documentación vigente y redacción de decisiones/plan | Selección de Gemini, Groq y LiteLLM; aprobación del alcance; revisión final por el equipo |
| 15 | 2026-09-13 | ADR-008 — Metadata social con IA | OpenAI Codex | Análisis del flujo SEO existente y redacción de la decisión para captions y hashtags por plataforma | Propuesta de hashtags generados por LLM; aprobación de separar metadata social de transcripción y publicación |

---

### Detalle — Entrada #13: Migración de Redis a Valkey

**Problema abordado:**
Riesgo de vendor lock-in y cambios de licencia en Redis. Desde 2024, Redis Ltd. cambió la licencia de Redis de BSD-3 a SSPL + RSALv2 (licencias restrictivas no aprobadas por la OSI). Esto representa un riesgo legal y estratégico para un proyecto académico y SaaS que se presenta como open-source. Se decide migrar al fork Valkey, respaldado por la Linux Foundation bajo licencia BSD-3-Clause.

**Prompt / Herramienta utilizada:**
Claude Opus 4.6 (Antigravity IDE). Se solicitó actuar como DevOps Engineer y Arquitecto Cloud para generar los entregables de migración: cambio en `docker-compose.yml`, análisis de impacto en código Django/Celery, y esta entrada de auditoría.

**Código / Arquitectura generada:**
- Se reemplazó la imagen Docker `redis:7-alpine` por `valkey/valkey:8-alpine` en el servicio `redis` de `docker-compose.yml`.
- El nombre del servicio se mantuvo como `redis` para que la resolución DNS interna (`redis://redis:6379/0`) siga funcionando sin modificar variables de entorno, `celery.py` ni `base.py`.
- No se requirieron cambios en código Python: el proyecto no usa `django-redis` como backend de caché, y Celery se conecta exclusivamente vía protocolo RESP (compatible sin modificaciones).

**Validación y Corrección Humana:**
- **Compatibilidad de protocolo:** Valkey implementa el protocolo RESP de Redis de forma idéntica. Las URIs `redis://` son válidas sin cambios — Celery (`redis-py`) y Django no distinguen entre un servidor Redis y uno Valkey.
- **Seguridad de la migración:** El servicio mantiene el mismo nombre DNS (`redis`), el mismo puerto por defecto (6379), y el mismo protocolo. Es un reemplazo transparente ("drop-in replacement") verificado por la Linux Foundation y adoptado por AWS (ElastiCache), Google Cloud (Memorystore), y Oracle.
- **Beneficio a largo plazo:** Se elimina el riesgo de licencia restrictiva, se garantiza continuidad open-source bajo BSD-3-Clause, y se alinea el stack con la política de la cátedra de utilizar software libre auditado.
- **Verificación:** El desarrollador debe ejecutar `docker compose up --build` y confirmar que el worker Celery conecta correctamente al broker y procesa tareas sin errores.

---

### Detalle — Entrada #14: Modernización del core de IA

**Problema abordado:**
El diseño aceptado de Whisper local requiere CPU/RAM o GPU que no encajan con una VPS de bajos recursos. La selección de clips también mantiene clientes de proveedores acoplados, un modelo Gemini anterior y validación insuficiente de la salida.

**Prompt / herramienta utilizada:**
OpenAI Codex. Se solicitó revisar exclusivamente el core de IA, comparar opciones actuales de precio/calidad y preparar una propuesta y un plan para Gemini 3.8 Flash, Groq Whisper Large V3 Turbo, context caching y LiteLLM.

**Documentación y arquitectura generadas:**

- ADR-001 se acotó a selección de clips y propone Gemini 3.8 Flash mediante LiteLLM SDK, con OpenAI como fallback.
- ADR-002 conserva su estado histórico `accepted` y queda en revisión por ADR-007.
- ADR-007 propone transcripción remota con Groq, normalización FLAC, chunks, checkpoints y criterios de aceptación.
- ADR-006 refleja que una producción sin GPU depende de aceptar ADR-007.
- El plan ejecutable se alineó con esos ADR y separa piloto, observación y retiro de Whisper local.

**Validación y corrección humana requerida:**

- El Par IA debe aprobar ADR-001 y ADR-007 después del piloto de 20 videos.
- El equipo debe revisar términos de privacidad y retención de Groq y Google antes de usar contenido real.
- Los precios y límites externos se deben verificar nuevamente al contratar o actualizar versiones.
- ADR-002 solo puede marcarse `superseded` si el piloto cumple WER, timestamps, utilidad, costo y recuperación.

---

### Detalle — Entrada #15: Metadata social con IA

**Problema abordado:**
El código ya generaba captions y hashtags mediante `SEOOptimizationService`, pero la llamada estaba acoplada directamente a OpenAI, usaba la transcripción completa del proyecto y no formaba parte de las decisiones del nuevo core Gemini/LiteLLM.

**Prompt / herramienta utilizada:**
OpenAI Codex. Se solicitó evaluar si los hashtags debían agregarse al ADR-007 o documentarse como una decisión separada.

**Documentación y arquitectura generadas:**

- ADR-008 mantiene la selección de clips y la metadata social como contratos separados.
- La metadata se genera por `ScheduledPost`, con la plataforma conocida y usando solo la transcripción temporizada del clip.
- Gemini 3.8 Flash mediante LiteLLM es el proveedor primario; OpenAI queda como fallback opcional.
- Se reutilizan `generated_caption` y `generated_hashtags`, con validación e idempotencia antes de publicar.
- ADR-001, ADR-005 y ADR-007 enlazan la nueva decisión sin ampliar sus responsabilidades.

**Validación y corrección humana requerida:**

- El Par IA y el Par Producto deben aprobar el contrato, los límites configurables por plataforma y la muestra del piloto.
- Solo se puede persistir y publicar metadata que cumpla el esquema local.
- El piloto debe medir aceptación humana, costo por publicación y ausencia de regeneraciones duplicadas.
