# ADR-001: Selección de Proveedor LLM para Clip Selection y Prompt-to-Edit

**Estado:** `proposed`
**Fecha:** 2026-09-01
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
| **Modelos** | Gemini 2.0 Flash ($0.10/1M input, $0.40/1M output), Gemini 2.0 Pro ($1.25/1M input, $10/1M output) |
| **JSON mode** | ✅ `response_mime_type: "application/json"` + JSON Schema nativo |
| **Structured Outputs** | ✅ Soporta schemas JSON en `responseSchema` |
| **Context window** | 1M tokens (Flash), 2M tokens (Pro) — gigantesco para transcripciones largas |
| **Content Moderation** | ✅ Safety settings integrados en cada request + Vertex AI Content Moderation API |
| **SDK** | `google-genai` (oficial), `google-generativeai` (legacy) |
| **Latencia** | Flash: ~1-3s, Pro: ~3-8s |
| **Free tier** | Generous: 15 RPM Flash, 2 RPM Pro (suficiente para dev) |

- **Pros:** Costo 25x menor que GPT-4o para el tier Flash. Ventana de contexto masiva elimina la necesidad de truncar transcripciones. Ya existe `GeminiStrategy` en el código (parcial). Free tier viable para desarrollo y testing.
- **Contras:** SDK menos maduro que OpenAI. Structured Outputs con Pydantic requiere adaptación (no hay `.beta.chat.completions.parse()` equivalente). Safety filters pueden ser agresivos (false positives en contenido de entretenimiento).
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

**PENDIENTE** — El Par IA debe:

1. **Ejecutar PoC comparativo** (tarea IA-00 del backlog):
   - Tomar 3 transcripciones reales de videos de distinta duración (2min, 10min, 30min)
   - Correr el prompt actual de `SelectionEngine` contra: Gemini 2.0 Flash, GPT-4o-mini, y opcionalmente un modelo open-source via Groq/Together
   - Medir: latencia, costo, calidad del JSON (parseable sin errores), calidad de la selección (manual review)
   - Documentar resultados en este ADR

2. **Resolver la dependencia de Content Moderation**:
   - Si el proveedor elegido no tiene Moderation API → evaluar alternativas: Perspective API (Google, gratis), Azure Content Safety, o moderation local con modelos clasificadores

3. **Evaluar el impacto en Structured Outputs**:
   - `SEOOptimizationService` usa Pydantic → OpenAI Structured Outputs. Si se cambia de proveedor, hay que adaptar o usar un wrapper (ej: `instructor` library)

4. **Proponer arquitectura de abstracción**:
   - El `SelectionEngine` ya tiene el patrón Strategy. Extender para que TODOS los servicios LLM usen el mismo patrón — no hardcodear ningún proveedor.

---

## Consecuencias (preliminares, pendientes de decisión)

### Si se elige Gemini Flash como primary:
- ✅ Reducción de costos ~25x vs GPT-4o
- ✅ Ventana de 1M tokens elimina truncación de transcripciones
- ⚠️ Requiere migrar `seo_engine.py` de Structured Outputs de OpenAI a JSON Schema de Gemini
- ⚠️ Requiere reemplazar `AI_Security_Shield.check_content_safety()` — evaluar Gemini Safety Settings o Perspective API
- ⚠️ Adaptar o reemplazar el SDK wrapper

### Si se mantiene OpenAI como primary:
- ✅ Mínimo esfuerzo de migración (código ya funciona)
- ✅ Structured Outputs y Moderation API resueltos
- ⚠️ Costo operativo más alto a escala
- ⚠️ Context window de 128K puede ser limitante para videos largos

### Arquitectura recomendada (independiente del proveedor):

```python
# Patrón sugerido: LLM Gateway con Strategy + Factory
class LLMGateway:
    """Single entry point for all LLM interactions."""
    
    @staticmethod
    def completion(prompt, schema=None, provider=None):
        """Unified interface — abstracts provider details."""
        provider = provider or settings.DEFAULT_LLM_PROVIDER
        strategy = LLMStrategyFactory.create(provider)
        return strategy.complete(prompt, schema)
    
    @staticmethod
    def moderate(text):
        """Content moderation — may use different provider than completion."""
        moderator = ModerationFactory.create(settings.MODERATION_PROVIDER)
        return moderator.check(text)
```

---

## Referencias

- [Código actual: SelectionEngine](../../back/apps/videos/services/selection_engine.py)
- [Código actual: SEOOptimizationService](../../back/apps/videos/services/seo_engine.py)
- [Código actual: AI_Security_Shield](../../back/apps/core/security.py)
- [Gemini API Pricing](https://ai.google.dev/pricing)
- [OpenAI Pricing](https://openai.com/api/pricing/)
- [instructor library (multi-provider Structured Outputs)](https://github.com/jxnl/instructor)
