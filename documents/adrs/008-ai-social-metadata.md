# ADR-008: Generación de metadata social con IA

**Estado:** `proposed`
**Fecha:** 2026-09-13
**Par responsable:** 🧠 Par IA + 🚀 Par Producto
**Relacionado:** [ADR-001 — Proveedor LLM](001-llm-provider-selection.md), [ADR-005 — Publicación social](005-social-publishing-strategy.md), [ADR-007 — Core de IA remoto](007-remote-ai-core.md)

---

## Contexto

OneCreator ya genera metadata para publicaciones mediante `SEOOptimizationService`. El servicio actual usa `gpt-4o-mini` directamente, recibe la transcripción completa del proyecto y devuelve título, descripción, hashtags, horario recomendado y ajustes por plataforma. `process_video_seo` persiste la descripción y los hashtags en `ScheduledPost.generated_caption` y `ScheduledPost.generated_hashtags`.

El nuevo core de IA definido por ADR-001 incorpora Gemini 3.8 Flash mediante LiteLLM para seleccionar clips, pero excluye expresamente `SEOOptimizationService`. Sin una decisión adicional quedarían dos integraciones LLM diferentes: el selector usaría LiteLLM/Gemini y la metadata social seguiría acoplada al SDK de OpenAI.

Los hashtags dependen del contenido exacto del clip y de la red donde se publicará. Generarlos durante la selección de momentos los asociaría a una transcripción extensa y a una etapa donde todavía puede no conocerse la plataforma de destino.

### Fuerzas de decisión

- Reutilizar la abstracción LLM del nuevo core sin operar LiteLLM Proxy.
- Generar metadata específica para TikTok, Instagram Reels o YouTube Shorts.
- Enviar al proveedor únicamente la transcripción correspondiente al clip.
- Mantener editables el caption y los hashtags antes de publicar.
- Validar localmente la salida antes de persistirla o enviarla a una red social.
- Evitar llamadas duplicadas en reintentos de Celery.
- No registrar prompts, transcripciones, respuestas crudas ni API keys.

---

## Opciones evaluadas

### Opción A: devolver hashtags dentro de cada `ClipCandidate`

- **Pros:** no agrega una llamada LLM y entrega una propuesta completa durante la selección.
- **Contras:** la plataforma todavía no está definida, el modelo analiza la transcripción general y el contrato editorial queda acoplado a publicación social.
- **Impacto:** obliga a ampliar el contrato de ADR-001 y a decidir cómo propagar metadata provisional hasta `ScheduledPost`.

### Opción B: generar metadata por `ScheduledPost` mediante LiteLLM

- **Pros:** la plataforma y el clip ya están definidos, reutiliza los campos persistentes existentes y mantiene separadas selección editorial y publicación.
- **Contras:** requiere una llamada LLM por publicación y un control idempotente propio.
- **Impacto:** reemplaza la llamada directa actual de OpenAI sin agregar un servicio de infraestructura.

### Opción C: conservar `SEOOptimizationService` con OpenAI directo

- **Pros:** menor esfuerzo inmediato y comportamiento ya integrado.
- **Contras:** mantiene clientes, errores, métricas y configuración separados del core nuevo; también conserva OpenAI como dependencia obligatoria para SEO.

---

## Decisión propuesta

Adoptar la **Opción B**: generar metadata social después de seleccionar el clip y crear el `ScheduledPost`, cuando la plataforma de destino ya es conocida.

`SEOOptimizationService` debe migrar a **Gemini 3.8 Flash mediante LiteLLM SDK embebido**. El grupo primario y el fallback reutilizan la política de ADR-001:

| Configuración | Valor inicial |
|---------------|---------------|
| Grupo primario | `social-metadata-primary` |
| Modelo primario | `gemini/gemini-3.8-flash` |
| Grupo de fallback | `social-metadata-fallback` |
| Modelo de fallback | `openai/gpt-4o-mini` |
| Fallback | Opcional; solo si existe `OPENAI_API_KEY` |
| Caché explícito | Desactivado |

LiteLLM se usa como biblioteca dentro del worker Celery. Esta decisión no agrega LiteLLM Proxy, otra base de datos, otro broker ni un servicio adicional en la VPS.

### Separación de responsabilidades

- La selección de clips continúa devolviendo únicamente tiempos, título, puntaje y razonamiento validado.
- La metadata social se genera por publicación y no forma parte de `ClipCandidate`.
- ADR-007 continúa gobernando la transcripción remota y los checkpoints de ingesta.
- ADR-005 continúa decidiendo cómo se autentica y publica en cada red.
- Este ADR gobierna exclusivamente la generación, validación y persistencia de caption y hashtags.

### Entrada mínima

El proveedor recibe:

```json
{
  "clip_transcript": "Fragmento exacto correspondiente al clip",
  "clip_title": "Título editable del clip",
  "target_niche": "tecnología",
  "platform": "TIKTOK",
  "language": "es"
}
```

La aplicación debe reconstruir `clip_transcript` usando palabras o segmentos cuyos timestamps se superpongan con `VideoClip.start_time` y `VideoClip.end_time`. Si no existe una transcripción temporizada, la tarea debe fallar de forma recuperable y no enviar la transcripción completa del proyecto como sustituto silencioso.

### Contrato de salida

El proveedor devuelve un objeto estructurado:

```json
{
  "caption": "Una descripción breve y específica para la publicación.",
  "hashtags": ["InteligenciaArtificial", "Productividad", "Tecnologia", "Innovacion", "HerramientasDigitales"]
}
```

Reglas locales:

- `caption` es obligatorio, no puede quedar vacío y debe respetar el límite configurado para la plataforma.
- `hashtags` contiene entre 5 y 8 valores.
- Cada hashtag se guarda sin el carácter `#`.
- Se eliminan espacios exteriores y hashtags vacíos.
- Se rechazan espacios internos y caracteres distintos de letras, números o `_`.
- Se eliminan duplicados sin distinguir mayúsculas de minúsculas, preservando el primer valor.
- Pydantic rechaza campos adicionales y respuestas incompletas.

El adaptador de publicación agrega `#` al construir el caption final. La representación persistida en `generated_hashtags` conserva la lista normalizada sin `#`.

### Persistencia e idempotencia

- `generated_caption` y `generated_hashtags` siguen siendo la fuente persistente para la publicación.
- Si ambos campos ya contienen una generación válida, un retry de Celery no vuelve a llamar al proveedor.
- Una regeneración solicitada explícitamente por el usuario puede reemplazarlos.
- Un lock por `ScheduledPost.id` evita dos generaciones simultáneas.
- La publicación usa exclusivamente metadata validada o contenido editado por el usuario.
- Un fallo de generación deja el post recuperable y no debe publicar silenciosamente un caption genérico como si fuera resultado de la IA.

### Errores, fallback y observabilidad

- Errores de autenticación o configuración no se reintentan.
- Timeout, conexión, 429 y 5xx admiten retry con backoff.
- Un resultado inválido del primario permite un solo intento con el fallback configurado.
- Se registran `post_id`, etapa, proveedor, modelo, latencia, tokens, costo estimado y tipo de error.
- No se registran caption, hashtags, prompt, transcripción ni respuesta cruda.

### Context caching

No se implementa caché explícito. Cada request usa una transcripción corta y parámetros variables por plataforma, por lo que no se espera reutilización suficiente para justificar creación, TTL e invalidación de cachés. Si el proveedor informa tokens de caché implícito pueden contabilizarse, sin convertirlos en requisito del piloto.

---

## Consecuencias

### Positivas

- Los hashtags se basan en el contenido exacto del clip y en la plataforma real.
- La integración LLM deja de estar acoplada al SDK directo de OpenAI.
- Gemini comparte configuración, validación, fallback y métricas con el nuevo core.
- No se requieren campos nuevos para persistir caption y hashtags.
- La metadata sigue siendo editable antes de publicar.

### Negativas

- Cada publicación puede generar una llamada LLM adicional.
- Una misma pieza publicada en tres redes puede producir tres generaciones distintas.
- La disponibilidad del proveedor afecta la preparación de metadata, aunque no el renderizado del clip.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Hashtags genéricos o irrelevantes | Usar transcripción recortada, nicho y plataforma; evaluación humana en piloto |
| Respuesta con caracteres no válidos | Esquema estricto y normalización local antes de persistir |
| Doble facturación en retries | Campos persistidos, checkpoint implícito y lock por post |
| Prompt injection desde la transcripción | Mantener `AI_Security_Shield` y aislar el contenido del usuario |
| Límite de caption distinto por plataforma | Configuración local por plataforma y validación previa al upload |
| Caída de Gemini | Retry acotado y fallback OpenAI opcional |

---

## Criterios para aceptar este ADR

- Una muestra de al menos 20 clips autorizados cubre las tres plataformas soportadas.
- El 100% de las respuestas persistidas cumple el esquema local.
- Cada resultado contiene entre 5 y 8 hashtags únicos y normalizados.
- Al menos el 80% de captions y hashtags es aceptado sin cambios o con edición mínima.
- Ningún retry automático repite una generación ya persistida.
- La exportación del piloto identifica proveedor, modelo, latencia, tokens y costo sin contenido sensible.
- El costo medido se informa por publicación y se aprueba antes del rollout general.

### Transición de estado

El ADR permanece `proposed` hasta completar el piloto. Si cumple todos los criterios, debe cambiar a `accepted` con fecha y enlace a los resultados. Si la calidad varía demasiado entre plataformas, se mantiene el proveedor actual o se limita Gemini a las plataformas aprobadas, documentando el resultado sin reescribir esta propuesta.

---

## Plan de implementación asociado

1. Definir contratos estrictos para request, respuesta y uso del proveedor.
2. Extraer la transcripción temporizada correspondiente a `VideoClip`.
3. Migrar `SEOOptimizationService` al Router LiteLLM compartido.
4. Hacer `process_video_seo` idempotente y seguro por `ScheduledPost.id`.
5. Agregar tests de contrato, normalización, fallback, retry y privacidad de logs.
6. Incorporar costo y calidad de metadata al runbook del piloto.

---

## Referencias

- [ADR-001: proveedor LLM](001-llm-provider-selection.md)
- [ADR-005: publicación social](005-social-publishing-strategy.md)
- [ADR-007: core de IA remoto](007-remote-ai-core.md)
- [Código: SEOOptimizationService](../../back/apps/videos/services/seo_engine.py)
- [Código: ScheduledPost](../../back/apps/videos/models.py)
- [Código: process_video_seo](../../back/apps/videos/tasks.py)
- [LiteLLM Router](https://docs.litellm.ai/docs/routing)
