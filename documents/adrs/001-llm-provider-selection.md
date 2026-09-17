# ADR-001: Selección de Proveedor LLM para Clip Selection y Prompt-to-Edit
**Estado:** `accepted`
**Fecha:** 2026-09-01 (propuesta inicial) | 2026-09-17 (aprobación arquitectura AICORE)
**Par responsable:** 🧠 Par IA (Dev 1 + Dev 2)

---

## Contexto

OneCreator usa LLMs en tres puntos críticos del pipeline:

1. **Clip Selection** (`SelectionEngine`): analiza la transcripción y selecciona los momentos más virales. Actualmente usa OpenAI GPT-4o/4o-mini con fallback a Gemini 1.5 Flash.
2. **SEO Metadata** (`SEOOptimizationService`): genera títulos, hashtags y metadata social. Actualmente hardcodeado a OpenAI GPT-4o-mini con Structured Outputs.
3. **Content Moderation** (`AI_Security_Shield`): valida que el contenido del usuario no viole políticas de seguridad. Actualmente usa la Moderation API de OpenAI.

### Problema

- **Vendor lock-in**: 3 servicios dependen de OpenAI. Si OpenAI cambia pricing, rate limits, o ToS, el producto queda vulnerable.
- **Costo operativo**: GPT-4o a $2.50/1M input tokens + $10/1M output tokens es caro a escala. Un video de 30 min genera ~15K tokens de transcripción por request.
- **Código acoplado**: `seo_engine.py` importa `from openai import OpenAI` directamente. `security.py` usa `client.moderations.create()` (API exclusiva de OpenAI).
- **Structured Outputs**: `SEOOptimizationService` usa `response_format=SocialMetadataResponse` (Pydantic → Structured Outputs de OpenAI). No todos los proveedores soportan esto nativamente.

### Restricciones

- El proveedor debe soportar **JSON mode o Structured Outputs** — el pipeline depende de respuestas JSON parseable.
- Latencia máxima aceptable: **<10s por request** (el usuario espera resultados, no es batch offline).
- Debe existir una **API de moderación de contenido** (o equivalente) — requerimiento de seguridad P0.
- Budget estimado del proyecto: bajo (proyecto académico con aspiración SaaS).

---

## Opciones Evaluadas

### Opción A: Google Gemini (Flash / Pro)

| Criterio | Detalle |
|----------|---------|
| **Modelos** | Gemini 2.0 / 3.8 Flash ($0.10/1M input, $0.40/1M output), Gemini Pro ($1.25/1M input, $10/1M output) |
| **JSON mode** | ✅ `response_mime_type: "application/json"` + JSON Schema nativo |
| **Structured Outputs** | ✅ Soporta schemas JSON en `responseSchema` |
| **Context window** | 1M tokens (Flash), 2M tokens (Pro) — gigantesco para transcripciones largas |
| **Content Moderation** | ✅ Safety settings integrados en cada request + Vertex AI Content Moderation API |
| **SDK** | `google-genai` (oficial), `google-generativeai` (legacy), compatible LiteLLM |
| **Latencia** | Flash: ~1-3s, Pro: ~3-8s |
| **Free tier** | Generous: 15 RPM Flash, 2 RPM Pro (suficiente para dev) |

- **Pros:** Costo 25x menor que GPT-4o para el tier Flash. Ventana de contexto masiva elimina la necesidad de truncar transcripciones. Ya existe `GeminiStrategy` en el código (parcial). Free tier viable para desarrollo y testing.
- **Contras:** SDK menos maduro que OpenAI. Structured Outputs con Pydantic requiere adaptación (no hay `.beta.chat.completions.parse()` equivalente nativo sin wrapper). Safety filters pueden ser agresivos (false positives en contenido de entretenimiento).
- **Riesgo:** Dependencia de Google. Cambios frecuentes en la API (v1beta → v1).

### Opción B: OpenAI (GPT-4o / GPT-4o-mini)

| Criterio | Detalle |
|----------|---------|
| **Modelos** | GPT-4o-mini ($0.15/1M input, $0.60/1M output), GPT-4o ($2.50/1M input, $10/1M output) |
| **JSON mode** | ✅ `response_format: { type: "json_object" }` |
| **Structured Outputs** | ✅ Nativo con Pydantic via `response_format=PydanticModel` |
| **Context window** | 128K tokens |
| **Content Moderation** | ✅ Moderation API dedicada (gratis, endpoint separado) |
| **SDK** | `openai>=1.0` — el más maduro del mercado |
| **Latencia** | 4o-mini: ~1-2s, 4o: ~3-6s |

- **Pros:** SDK más maduro y estable. Structured Outputs con Pydantic son first-class. Moderation API separada y gratuita. Comunidad y documentación extensiva.
- **Contras:** Más caro que Gemini Flash (especialmente 4o). Context window de 128K puede requerir truncar transcripciones largas (>30 min). Sin free tier útil para dev.
- **Riesgo:** Costo a escala. Rate limits más restrictivos en tiers bajos.

### Opción C: Anthropic Claude (Sonnet / Haiku)

| Criterio | Detalle |
|----------|---------|
| **Modelos** | Claude Haiku 3.5 ($0.80/1M input, $4/1M output), Sonnet 4 ($3/1M input, $15/1M output) |
| **JSON mode** | ⚠️ No nativo — se fuerza via prompt + `prefill` |
| **Structured Outputs** | ❌ No soporta schemas Pydantic nativos |
| **Context window** | 200K tokens |
| **Content Moderation** | ❌ No tiene API de moderación separada |
| **SDK** | `anthropic` — bueno pero menos ecosistema |
| **Latencia** | Haiku: ~1-2s, Sonnet: ~3-8s |

- **Pros:** Calidad de razonamiento superior (especialmente Sonnet para análisis de contenido). Context window de 200K es generoso.
- **Contras:** Sin JSON mode nativo ni Structured Outputs — requiere parsing manual y validación. Sin API de moderación — hay que buscar alternativa. Más caro que Gemini Flash. Ecosystem más pequeño.
- **Riesgo:** Fragilidad en parsing JSON (depende del prompt engineering).

### Opción D: Modelos Open-Source (Llama 3.1, Mistral)

| Criterio | Detalle |
|----------|---------|
| **Modelos** | Llama 3.1 70B/8B, Mistral Large/Small |
| **JSON mode** | ⚠️ Depende del framework de serving (vLLM, Ollama) |
| **Structured Outputs** | ❌ Requiere constrained decoding (vLLM/Outlines) |
| **Context window** | 128K (Llama 3.1 70B), 32K (Mistral) |
| **Content Moderation** | ❌ No incluido — implementar manualmente |
| **Hosting** | Self-hosted o via providers (Together, Groq, Fireworks) |
| **Costo** | Self-hosted: costo de GPU (~$1-3/h A100). Providers: $0.05-0.90/1M tokens |

- **Pros:** Sin vendor lock-in. Costo potencialmente más bajo a escala con self-hosting. Control total sobre el modelo y datos.
- **Contras:** Requiere infraestructura de GPU (complejidad operativa masiva para un equipo de 6). Calidad inferior a GPT-4o/Gemini Pro en tareas de análisis de contenido. Sin moderación integrada — hay que construirla. Mantenimiento del modelo es responsabilidad del equipo.
- **Riesgo:** Complejidad operativa desproporcionada para el tamaño del equipo. Calidad de JSON generation inferior.

---

## Decisión

**APROBADO — Adopción de Google Gemini 3.8 Flash como proveedor primario unificado a través de LiteLLM, con OpenAI GPT-4o-mini como fallback secundario.**

1. **Proveedor Primario**: **Google Gemini 3.8 Flash** para `SelectionEngine` y `SEOOptimizationService`. Su ventana de 1M tokens y costo ($0.10/1M tokens) lo hacen óptimo para ingesta completa de transcripciones sin truncamiento.
2. **Capa de Abstracción Universal**: En lugar de implementar SDKs directos propietarios en cada servicio, se adopta **LiteLLM** (`litellm`). LiteLLM estandariza la interfaz bajo el formato OpenAI-compatible, permitiendo intercambiar modelos simplemente cambiando variables de entorno (`model="gemini/gemini-3.8-flash"` o `model="gpt-4o-mini"`).
3. **Cadena de Resiliencia (Fallback Chain)**:
   - Primario: `gemini/gemini-3.8-flash`
   - Secundario (Fallback automático ante rate limit, timeout o 5xx): `gpt-4o-mini`
   - Terciario: Reintento Celery con backoff exponencial.
4. **Content Moderation**: Se desacopla de la selección. Se mantiene el endpoint gratuito de OpenAI Moderation (`text-moderation-latest`) como escudo pre-flight o los Safety Ratings de Gemini en el request primario.
5. **Caché Semántica / Hash de Transcripción**: Se implementa caching en Redis basado en el hash del texto de la transcripción para evitar llamadas duplicadas a la API ante re-renders.

---

## Consecuencias

### Positivas
- **Reducción de costos directa**: ~90% de ahorro vs GPT-4o tradicional, fundamental para la sostenibilidad de una VPS y tier gratuito de usuarios.
- **Cero truncamiento**: Transcripciones de 1h+ entran holgadamente en el millón de tokens de Gemini Flash.
- **Desacoplamiento total con LiteLLM**: Si mañana Anthropic o Groq lanzan un modelo superior en relación costo/calidad, el cambio requiere solo modificar una variable de configuración, sin reescribir `selection_engine.py` ni `seo_engine.py`.
- **Estructuración garantizada**: LiteLLM soporta Pydantic models para Structured Outputs mapeando transparentemente hacia OpenAI o Gemini.

### Negativas
- Dependencia de la librería `litellm` como adaptador central.
- Los filtros de seguridad de Gemini pueden bloquear contenido legítimo de comedia/gaming con lenguaje explícito si no se ajustan los `safety_settings` (`BLOCK_NONE` o permisivo).

---

## Addendum: Justificación Arquitectónica para el Equipo de Desarrollo (Épicas AICORE)

> **Nota para los desarrolladores:** Esta sección documenta los motivos de ingeniería, los tradeoffs evaluados y las directrices de implementación para la modernización del motor de IA (Épicas AICORE-1 a AICORE-6).

### 1. ¿Por qué Gemini 3.8 Flash como Primary?
Al evaluar la operación en producción de un SaaS de edición de video, el cuello de botella de los LLMs no es la capacidad de razonamiento abstracto complejo (no estamos resolviendo demostraciones matemáticas), sino:
- **Throughput y latencia:** Gemini Flash responde en 1-2 segundos.
- **Tamaño del contexto:** Un podcast de 45 minutos produce más de 12.000 palabras de transcripción con timestamps. En modelos de 128K o menos con cotizaciones caras, esto requiere chunking y múltiples llamadas (lo que destruye el contexto global para entender qué partes son los "mejores momentos"). Gemini ingesta la transcripción entera de una sola pasada con costo marginal despreciable ($0.001 - $0.003 por video).

### 2. ¿Por qué LiteLLM en lugar de nuestro propio `LLMGateway` custom?
En la propuesta original se planteaba construir una clase `LLMGateway` con Strategy Pattern manual. La experiencia en arquitecturas de producción dicta: **no reinventar la rueda del provider routing**.
- **LiteLLM** ya maneja retries automáticos, fallbacks de proveedor (`fallbacks=[{"gemini/gemini-3.8-flash": ["gpt-4o-mini"]}]`), métricas de latencia, trackeo de costos en USD y compatibilidad nativa con esquemas Pydantic.
- Permite que `SelectionEngine` y `SEOOptimizationService` invoquen una única llamada `completion(...)`, reduciendo drásticamente las líneas de código a mantener y probar por el equipo.

### 3. Estrategia de Caching en Redis
Cualquier llamada a un LLM en un pipeline de video debe ser idempotente:
- **Clave de caché:** `hash = sha256(f"{transcript_text}:{prompt_template_version}")`
- Si el usuario solicita regenerar clips con el mismo estilo o si la tarea de Celery falla en un paso posterior (ej. rendering) y se reintenta, el sistema consulta Redis con TTL de 24 horas antes de golpear la API del LLM. Esto previene costos duplicados y rate-limits.

### 4. Sinergia con Groq (Whisper Remoto)
El pipeline completo de IA opera ahora de forma 100% remota:
```
[Audio File] 
    ↓
[Groq Whisper Large V3 Turbo]  <-- Transcripción ultra-rápida con timestamps a nivel de palabra
    ↓ (JSON con words + timestamps)
[LiteLLM Gateway]              <-- Envío de transcripción completa + Prompt de viralidad
    ↓
[Gemini 3.8 Flash]             <-- Selección de timestamps de inicio/fin de clips + ganchos virales
    ↓ (Fallback: GPT-4o-mini)
[RenderEngine / Celery Queue]  <-- Corte y generación final
```
Esto permite que el servidor web / VPS opere con recursos mínimos (1-2 vCPUs, 2-4GB RAM) sin necesidad de GPUs dedicadas.

---

## Referencias

- [Código actual: SelectionEngine](../../back/apps/videos/services/selection_engine.py)
- [Código actual: SEOOptimizationService](../../back/apps/videos/services/seo_engine.py)
- [Código actual: AI_Security_Shield](../../back/apps/core/security.py)
- [LiteLLM Documentation](https://docs.litellm.ai/docs/)
- [Gemini API Pricing & Structured Outputs](https://ai.google.dev/pricing)
- [Groq Cloud API Reference](https://console.groq.com/docs/speech-text)
- [OpenAI Pricing](https://openai.com/api/pricing/)
- [instructor library (multi-provider Structured Outputs)](https://github.com/jxnl/instructor)
