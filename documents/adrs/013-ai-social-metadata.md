# ADR-013: Generación de metadata social con IA

**Estado:** `accepted`
**Fecha:** 2026-09-13 (propuesta, rama `chore/ai-core-adrs`, commit 92d7c85), 2026-11-23 (aceptada y actualizada al núcleo de IA vigente)
**Par responsable:** 🧠 Par IA + 🚀 Par Producto
**Relacionado:** [ADR-001 — Proveedor LLM](001-llm-provider-selection.md), [ADR-005 — Publicación social](005-social-publishing-strategy.md), [ADR-008 — Transcripción con Groq](008-groq-whisper-migration.md), [ADR-011 — Moderación](011-content-moderation.md), [ADR-012 — Perfiles de modelos](012-model-profiles-and-capacity.md)

> Este ADR se propuso como "ADR-008" en una rama que nunca se mergeó; ese número quedó tomado por la transcripción con Groq. Se renumera como 013 y se actualiza al estado real del código.

---

## Contexto

OneCreator genera la metadata de cada publicación con `SEOOptimizationService` (`back/apps/videos/services/seo_engine.py`). `process_video_seo` la persiste en `ScheduledPost.generated_caption` y `ScheduledPost.generated_hashtags`.

El servicio ya usa LiteLLM con el modelo de `AI_DEFAULT_LLM_MODEL`, pero quedó afuera de las reglas que sigue el resto del núcleo de IA (selección y transcripción):

- Muta la key global de LiteLLM en lugar de resolverla por llamada.
- No pasa `timeout` ni `num_retries=0`, así que rige el timeout por defecto de LiteLLM (600 s).
- Interpola el nicho sin sanear en el system prompt.
- Envía la transcripción completa del proyecto, no la del clip que se publica.
- No registra `AIUsageRecord`: su costo no aparece en el export del piloto.
- `process_video_seo` reintenta cualquier excepción y, al agotar los reintentos, nunca marca el post como `FAILED`.

Además, los hashtags dependen del contenido exacto del clip y de la red donde se publica. Generarlos durante la selección de momentos los asociaría a una transcripción extensa y a una etapa donde la plataforma de destino todavía no se conoce.

### Fuerzas de decisión

- Reutilizar los patrones del núcleo de IA (LiteLLM embebido, key por llamada, timeout, errores tipados, fallback, registro de uso), sin agregar un proxy ni otro servicio.
- Generar metadata específica para TikTok, Instagram Reels o YouTube Shorts.
- Enviar al proveedor únicamente la transcripción del clip.
- Mantener editables el caption y los hashtags antes de publicar.
- Validar localmente la salida antes de persistirla o publicarla.
- Evitar llamadas duplicadas en los reintentos de Celery.
- No registrar prompts, transcripciones, respuestas crudas ni API keys.

---

## Opciones evaluadas

### Opción A: devolver hashtags dentro de cada clip seleccionado

- **Pros:** no agrega una llamada al LLM y entrega una propuesta completa durante la selección.
- **Contras:** la plataforma todavía no está definida, el modelo analiza la transcripción general y el contrato de selección queda acoplado a la publicación social.

### Opción B: generar metadata por `ScheduledPost`

- **Pros:** la plataforma y el clip ya están definidos, reutiliza los campos persistentes existentes y mantiene separadas la selección editorial y la publicación.
- **Contras:** requiere una llamada al LLM por publicación y un control de idempotencia propio.

### Opción C: dejar `SEOOptimizationService` como está

- **Pros:** sin esfuerzo inmediato.
- **Contras:** mantiene todas las brechas del contexto (key global, sin timeout, sin costo registrado, transcripción completa, posts que nunca fallan).

---

## Decisión

**Opción B.** La metadata social se genera después de crear el `ScheduledPost`, cuando el clip y la plataforma de destino ya se conocen.

| Configuración | Valor |
|---------------|-------|
| Modelo primario | `AI_DEFAULT_LLM_MODEL` (hoy `gemini/gemini-3.8-flash`), el mismo que la selección |
| Fallback | `AI_FALLBACK_LLM_MODEL`, un solo intento, con la misma política que la selección ([ADR-001](001-llm-provider-selection.md)) |
| Temperatura | 0,7: es copy creativo (la selección usa 0,1 porque extrae) |
| Razonamiento | El mismo perfil por entorno que la selección ([ADR-012](012-model-profiles-and-capacity.md)) |
| Caché explícito | Desactivado |

### Separación de responsabilidades

- La selección de clips sigue devolviendo solo tiempos, título, puntaje y justificación.
- La metadata social se genera por publicación y no forma parte del clip seleccionado.
- [ADR-005](005-social-publishing-strategy.md) sigue decidiendo cómo se autentica y publica en cada red.
- Este ADR gobierna solo la generación, validación y persistencia del caption y los hashtags.

### Entrada mínima

```json
{
  "clip_transcript": "Fragmento exacto correspondiente al clip",
  "clip_title": "Título editable del clip",
  "target_niche": "tecnologia",
  "platform": "TIKTOK",
  "language": "es"
}
```

- `clip_transcript` se reconstruye con las palabras de `transcript_data` cuyos tiempos se superponen con `VideoClip.start_time` y `VideoClip.end_time`. Si no hay transcripción temporizada, la tarea falla de forma recuperable: nunca se envía la transcripción completa del proyecto como sustituto.
- `clip_transcript` y `clip_title` van aislados con `AI_Security_Shield.isolate_user_input`.
- `target_niche` se reduce a un slug; nunca se interpola texto libre en el system prompt.

### Contrato de salida

```json
{
  "caption": "Una descripción breve y específica para la publicación.",
  "hashtags": ["InteligenciaArtificial", "Productividad", "Tecnologia", "Innovacion", "HerramientasDigitales"]
}
```

Reglas locales:

- `caption` es obligatorio, no puede quedar vacío y respeta el límite configurado para la plataforma.
- `hashtags` contiene entre 5 y 8 valores, guardados sin `#`.
- Se eliminan espacios exteriores y hashtags vacíos; se rechazan espacios internos y caracteres que no sean letras, números o `_`.
- Se eliminan duplicados sin distinguir mayúsculas, conservando el primero.
- Pydantic rechaza campos adicionales y respuestas incompletas.

El adaptador de publicación agrega `#` al armar el caption final. `generated_hashtags` guarda la lista normalizada sin `#`.

### Moderación

Según [ADR-011](011-content-moderation.md): la transcripción del clip se modera antes de generar, y el caption y los hashtags generados se moderan antes de persistirlos. Un resultado bloqueado deja el post `FAILED` con un código estático y el mensaje genérico.

### Persistencia e idempotencia

- `generated_caption` y `generated_hashtags` son la fuente persistente para la publicación.
- Si ambos ya tienen una generación válida, un reintento de Celery no vuelve a llamar al proveedor.
- Una regeneración pedida explícitamente por el usuario puede reemplazarlos.
- Un lock por `ScheduledPost.id` evita dos generaciones simultáneas.
- Un fallo de generación deja el post recuperable y nunca publica un caption genérico como si fuera de la IA.

### Errores, reintentos y observabilidad

- Los errores se clasifican con el mismo clasificador que la selección: autenticación, configuración y 4xx no se reintentan; timeout, conexión, 429 y 5xx se reintentan con backoff.
- Al agotar los reintentos, el post queda `FAILED` con un código estático.
- Cada intento escribe una fila de `AIUsageRecord` con la etapa `seo` (proveedor, modelo, latencia, tokens, costo estimado y clase del error).
- No se registran caption, hashtags, prompt, transcripción ni respuesta cruda.

---

## Consecuencias

### Positivas

- Los hashtags se basan en el contenido exacto del clip y en la plataforma real.
- La metadata comparte configuración, validación, fallback y métricas con el resto del núcleo de IA.
- Su costo entra en el export del piloto.
- No hacen falta campos nuevos para el caption y los hashtags.

### Negativas

- Cada publicación puede generar una llamada adicional al LLM.
- Un mismo clip publicado en tres redes puede producir tres textos distintos.
- La disponibilidad del proveedor afecta la preparación de la metadata, aunque no el render.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Hashtags genéricos o irrelevantes | Transcripción del clip, nicho y plataforma; evaluación humana en el piloto |
| Respuesta con caracteres inválidos | Esquema estricto y normalización local antes de persistir |
| Doble cobro en reintentos | Campos persistidos y lock por post |
| Inyección de prompt desde la transcripción | `isolate_user_input` y nicho reducido a slug |
| Límite de caption distinto por plataforma | Configuración local por plataforma |
| Caída del proveedor | Reintento acotado y fallback configurado |

---

## Validación en el piloto

La decisión se acepta para implementar. Su calidad se valida en el piloto con estos criterios; si no se cumplen, se revisa este ADR con los resultados:

- Una muestra de al menos 20 clips autorizados cubre las tres plataformas.
- El 100 % de las respuestas persistidas cumple el esquema local.
- Cada resultado tiene entre 5 y 8 hashtags únicos y normalizados.
- Al menos el 80 % de captions y hashtags se acepta sin cambios o con edición mínima.
- Ningún reintento automático repite una generación ya persistida.
- El costo por publicación se informa en el export del piloto.

---

## Plan de implementación

Issue #69 (AICORE-17):

1. Contratos de entrada y salida y normalización de hashtags.
2. Extracción de la transcripción temporizada del clip.
3. `SEOOptimizationService` con key por llamada, timeout, clasificación de errores y fallback.
4. `process_video_seo` idempotente, con lock por post, moderación de entrada y salida, y estado `FAILED` al agotar reintentos.
5. Etapa `seo` en `AIUsageRecord` y en el export del piloto.
6. Tests de contrato, normalización, fallback, reintentos y privacidad de logs.

---

## Referencias

- [Código: SEOOptimizationService](../../back/apps/videos/services/seo_engine.py)
- [Código: ScheduledPost](../../back/apps/videos/models.py)
- [Código: process_video_seo](../../back/apps/videos/tasks.py)
- [Runbook del piloto](../runbooks/ai-core-pilot-runbook.md)
