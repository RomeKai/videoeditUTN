# ADR-012: Perfiles de modelos por entorno y planificación de capacidad

**Estado:** `proposed`
**Fecha:** 2026-10-11
**Par responsable:** 🧠 Par IA + ⚙️ Par Engine
**Relacionado:** [ADR-001](001-llm-provider-selection.md) (cadena de LLM), [ADR-008](008-groq-whisper-migration.md) (transcripción con Groq), [ADR-011](011-content-moderation.md) (moderación), runbook del piloto (`documents/runbooks/ai-core-pilot-runbook.md`), issues #43 (AICORE-9) y #44 (AICORE-10)

> Los ADR 009 (broker) y 010 (PyAV) siguen reservados por el plan de estabilización.

---

## Contexto

AI Core V2 elige sus modelos por variables de entorno (`AI_DEFAULT_LLM_MODEL`, `AI_FALLBACK_LLM_MODEL`, `AI_DEFAULT_TRANSCRIPTION_MODEL`, `TRANSCRIPTION_BACKEND`). Hoy todos los entornos usan las mismas cuentas, con los límites de un plan de desarrollo. Antes del piloto (AICORE-9) y del soft-launch hay que decidir tres cosas: qué modelos usa cada entorno, qué capacidad necesita producción y cómo se comporta el sistema cuando la demanda supera esa capacidad.

### Volumen esperado del soft-launch (estimación del owner, 2026-10-11)

| Dato | Valor |
|------|-------|
| Creadores activos (beta) | 50 |
| Frecuencia | 3 videos por creador por semana |
| Videos | 150 por semana, ~21 por día |
| Duración promedio del video fuente | 25 minutos (podcasts, streams, entrevistas) |
| Audio a transcribir | ~8,75 a 8,9 horas por día |
| Pico | 4 a 5 uploads simultáneos en franjas de alta actividad |

### Límites actuales de la organización en Groq (consola, 2026-10-11)

| Modelo | Límite | Valor |
|--------|--------|-------|
| `whisper-large-v3-turbo` | Segundos de audio por hora / por día | 7.200 / 28.800 (2 h por hora, 8 h por día) |
| `whisper-large-v3-turbo` | Requests por minuto / por día | 20 / 2.000 |
| `openai/gpt-oss-120b` | Tokens por minuto / por día | 8.000 / 200.000 |
| `openai/gpt-oss-120b` | Requests por minuto / por día | 30 / 1.000 |

Los límites de Gemini para `gemini-3.8-flash` y `gemini-3.5-flash-lite` en el tier pago del proyecto **no están relevados** todavía.

### Qué dicen los números

| Recurso | Demanda | Límite actual | Resultado |
|---------|---------|---------------|-----------|
| Transcripción por día | ~8,75 a 8,9 h, más el solapamiento entre chunks | 8 h | **Insuficiente** aun en un día promedio |
| Transcripción por hora en el pico | 5 × 25 min = 7.500 s | 7.200 s | **Insuficiente** en el pico |
| LLM de fallback vía Groq | Un transcript de 25 min ocupa ~6.000 tokens de entrada según la estimación del owner, más el prompt, los timestamps por línea, la salida y el razonamiento | 8.000 tokens por minuto | `gpt-oss-120b` **no sirve** como fallback con estos límites |
| LLM primario en el pico | 4 a 5 transcripciones en el mismo minuto: ~24.000 a 30.000 tokens de entrada por minuto, como mínimo | Sin relevar | A verificar |

La cifra de ~6.000 tokens es una estimación de piso: el prompt real agrega un timestamp `[START-END]` por línea, el system prompt, la salida en JSON y los tokens de razonamiento de Gemini. El valor real sale de la medición del G0 (`prompt_tokens` y `completion_tokens` en el export de AICORE-8) y es el que debe usarse para dimensionar.

---

## Decisiones

### D-C1. Tres perfiles de modelos (propuesta)

| Perfil | Objetivo | Modelos | Tier |
|--------|----------|---------|------|
| Desarrollo / CI | Que el código funcione | Mocks en CI (la red ya está bloqueada). En local, modelos baratos | Cualquiera, solo con contenido propio |
| Piloto / staging | Medir lo que va a producción | **Los mismos modelos, versiones y tiers que producción** | Pago |
| Producción | Usuarios reales | Primario y fallback de **otro proveedor**, versiones fijas y con precio en `pricing.py` | Pago, dimensionado por D-C5 |

**Regla:** el piloto no puede usar modelos distintos de los de producción. Si los usa, sus métricas de costo, latencia y calidad no se transfieren y el go/no-go deja de tener valor.

El mecanismo no cambia: cada entorno define sus modelos en su propio `.env`.

### D-C2. Privacidad de los datos de usuarios (propuesta)

- El piloto y producción corren **solo sobre tiers pagos** cuyos términos excluyan el uso de los datos para entrenamiento y limiten la retención.
- Según el owner, el tier gratuito de Gemini usa los datos para mejorar sus productos. Antes de pasar transcripts reales a cualquier proveedor hay que verificar sus términos vigentes y registrar la cláusula aplicable.
- En desarrollo, el tier gratuito se usa solo con contenido propio, nunca con videos de usuarios.

### D-C3. Chequeo de arranque (propuesta)

Un Django system check que falle el arranque en piloto y producción si:

- un modelo configurado es un alias móvil (`*-latest`);
- un modelo configurado no tiene precio vigente en `pricing.py`;
- `TRANSCRIPTION_BACKEND` tiene un valor no admitido.

La mala configuración se detecta en el arranque y no en la primera factura. La implementación entra en AICORE-9.

### D-C4. Rate limiting propio con degradación ordenada (propuesta)

- Un token bucket en Valkey por proveedor y recurso: segundos de audio por hora y por día para la transcripción, y tokens y requests por minuto para el LLM.
- Los buckets se configuran **por debajo** del límite del proveedor (por ejemplo, al 80 %), para absorber errores de estimación.
- Antes de cada llamada, la etapa reserva capacidad. Si no la hay, la tarea vuelve a la cola con un countdown y la etapa queda en `pending_capacity`. Ese estado no cuenta como intento fallido ni genera cobro.
- **Abierto:** el tiempo máximo de espera antes de marcar el proyecto como fallido y el mensaje que ve el usuario.
- Motivo: es preferible demorar la entrega de forma ordenada antes que recibir 429 del proveedor. Hoy esos 429 se reintentan con backoff (AICORE-7), pero inflan la latencia p95 y consumen reintentos.

### D-C5. Tiers de producción (ABIERTA)

Requisitos mínimos, con margen ×2 sobre la demanda del soft-launch:

| Recurso | Requisito |
|---------|-----------|
| Transcripción | ≥ 18 h de audio por día y ≥ 2,5 h por hora |
| LLM primario | Tokens por minuto ≥ 2 × (tokens reales por transcripción medidos en G0 × 5 concurrentes) |
| LLM de fallback | El mismo requisito, en **otro proveedor** |

Candidatos a evaluar (sin decidir):

- **Transcripción:** Groq en un plan con límites mayores, o Whisper local en un worker con GPU. El [ADR-008](008-groq-whisper-migration.md) descartó Whisper local en workers **sin GPU** por riesgo de OOM; un worker con GPU es otra infraestructura, con otro costo.
- **LLM primario:** Gemini en tier pago, con sus límites a relevar.
- **Fallback de otro proveedor:** `openai/gpt-4o-mini` con crédito en la cuenta, o `groq/openai/gpt-oss-120b` en un plan de Groq con tokens por minuto suficientes. Los modelos Llama de Groq quedan descartados: solo admiten `json_object` y no tienen precio publicado.

La elección depende de cotizaciones, de los términos de privacidad (D-C2) y de las mediciones del G0.

### D-C6. Caché por hash de transcript (TD-02)

Se mantiene condicionada a la evidencia del piloto. Es un candidato a mejora rápida si los creadores suben el mismo video varias veces para probar estilos de edición. Para medirlo hace falta guardar un hash del transcript, que hoy no existe (ver la sección 8 del runbook).

---

## Consecuencias

### Positivas

- El go/no-go del piloto mide lo mismo que va a correr en producción.
- Las facturas sorpresa y los alias que cambian sin aviso se detectan en el arranque.
- Un pico de demanda se traduce en demora ordenada y no en trabajos abortados.
- La capacidad se dimensiona con números y no por intuición.

### Negativas

- El piloto requiere tiers pagos antes de empezar: hay costo antes de tener evidencia.
- El rate limiting propio agrega estado en Valkey y un camino nuevo en el pipeline que hay que probar.
- Mantener dos proveedores de LLM duplica contratos, keys y monitoreo.

### Riesgos

- Con los límites actuales de Groq, el soft-launch no entra ni en un día promedio: sin cambiar de plan, la latencia del producto queda a merced de los reintentos.
- Si el consumo real de tokens por transcripción es bastante mayor que la estimación de piso, el requisito de tokens por minuto crece en la misma proporción.
- Si un proveedor no ofrece términos sin uso para entrenamiento, queda descartado para datos reales, aunque sea el más barato.

---

## Plan de implementación

1. **Antes del G0 del piloto:** relevar los límites de Gemini en tier pago, verificar los términos de privacidad de cada proveedor (D-C2) y ampliar la capacidad de Groq.
2. **AICORE-9:** chequeo de arranque (D-C3) junto con el rollout por allowlist.
3. **Issue nuevo, antes del G3:** rate limiting con `pending_capacity` (D-C4).
4. **Con las mediciones del G0:** cerrar D-C5 y pasar este ADR a `accepted`.
