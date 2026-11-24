# Runbook del piloto de AI Core V2 (AICORE-8 / AICORE-9)

**Issues:** #42 (captura y export), #43 (piloto), #44 (retiro de Whisper, solo con go).
**Plan de referencia:** `documents/plan_aicore_8_9_10.md` (secciones 3.7 y 4).
**Estado:** borrador para ejecutar en AICORE-9. Los campos entre corchetes se completan al correr el piloto.

## Cómo leer este documento

| Si necesitás... | Andá a |
|-----------------|--------|
| Saber qué se mide y con qué umbral | 1 |
| Armar la muestra y las referencias | 2 y 3 |
| Evaluar clips con personas | 4 |
| Correr el piloto paso a paso | 5 |
| Provocar fallos y verificar la recuperación | 6 |
| Revertir | 7 |
| Decidir sobre el caché explícito | 8 |
| Firmar el resultado | 9 |
| Entender qué no mide el piloto | 10 |

---

## 1. Objetivo y criterios

**Objetivo:** decidir con evidencia si el pipeline V2 (Groq para ASR, LiteLLM para selección) puede reemplazar al legacy, y habilitarlo de forma gradual.

### 1.1 Criterios de aceptación (gate G1)

| Métrica | Definición exacta | Umbral |
|---------|-------------------|--------|
| WER | Corpus-level: Σ errores / Σ palabras de referencia, sobre los 20 videos, con la normalización de la sección 3.3 | ≤ 15 % |
| Error de timestamps | Mediana del error absoluto entre el timestamp del sistema y el ancla anotada, sobre todas las anclas | ≤ 300 ms |
| Clips aceptados | Clips "aceptado" o "cambios mínimos" sobre el total evaluado (sección 4) | ≥ 70 % |
| Costo combinado | Σ costo / Σ minutos × 30, agregado sobre la muestra (ASR + selección) | < USD 0,04 por 30 min |
| Costo p95 | Percentil 95 de `cost_per_30min_usd` por video. Se reporta, sin umbral propio | Informativo |
| Costo completo | `cost_complete = True` en cada proyecto de la muestra | 100 % de los proyectos |

**Los criterios de costo se evalúan solo sobre proyectos V2.** La selección legacy llama a un LLM pago cuyo usage no se captura, así que sus registros llevan costo NULL: todo proyecto legacy sale con `cost_complete = False` y sin `cost_per_30min_usd` (ver salvedad h). Las comparaciones de costo legacy vs. V2 **no son equivalentes**: se excluye legacy del cálculo de costo (los criterios de WER, timestamps y calidad sí comparan ambos motores).

### 1.2 Fuentes de cada número

| Métrica | Fuente |
|---------|--------|
| WER y timestamps | `back/scripts/pilot_eval.py` (AICORE-9, solo dev, salida numérica) |
| Costo, latencia, tokens, fallback, caché | `export_ai_pilot_metrics` |
| Calidad de clips | Planilla de evaluación humana (sección 4) |

### 1.3 Export de métricas

```
docker compose exec web python manage.py export_ai_pilot_metrics \
  --since 2026-10-09 --until 2026-10-16 --level project --format csv --output /tmp/pilot_project.csv
docker compose cp web:/tmp/pilot_project.csv ./pilot_project.csv
```

El servicio de `docker-compose.yml` se llama `web`. `--output` escribe **dentro del contenedor**: hay que copiar el archivo al host con `docker compose cp` (o apuntar `--output` a una ruta del volumen montado `./back:/app`, p. ej. `/app/pilot_project.csv`, y no versionarlo). Sin `--output`, el export va a stdout y se puede redirigir en el host con `docker compose exec -T web ... > pilot_project.csv`.

| Argumento | Valor | Nota |
|-----------|-------|------|
| `--since` | `YYYY-MM-DD` (obligatorio) | Inclusivo, 00:00 UTC |
| `--until` | `YYYY-MM-DD` (opcional) | **Exclusivo**, 00:00 UTC |
| `--level` | `project` (default) o `record` | `project`: una fila por proyecto creado en la ventana, incluso sin registros |
| `--format` | `csv` (default) o `jsonl` | Ventana vacía: solo encabezado (csv) o nada (jsonl), exit 0 |
| `--output` | ruta | Sin este argumento escribe a stdout |

**Metadata social (AICORE-17):** cada intento de generar caption y hashtags de una publicación escribe un registro con `stage=seo`. En `--level=record` aparecen como cualquier otro registro. En `--level=project`, las columnas de costo, tokens y conteos siguen midiendo solo la ingestión (transcripción y selección), así que `cost_per_30min_usd` no cambia. La metadata va aparte en `seo_records`, `seo_cost_usd` y `seo_cost_complete`, que es falso si algún registro quedó sin precio. Para el criterio de costo por publicación de ADR-013, dividir `seo_cost_usd` por la cantidad de publicaciones del proyecto.

**Privacidad:** el export no incluye títulos, transcripts, prompts, reasoning, email ni id de usuario. Los registros no tienen columnas de texto libre.

**Costo por 30 min:** usa la duración del video fuente, no `audio_seconds`. Si falta la duración, la columna queda vacía.

**Tokens de razonamiento:** `reasoning_tokens` (por registro y sumado por proyecto) son los thinking tokens de Gemini que el proveedor cobra como salida y que no están en `completion_tokens`. El costo los cuenta al precio de salida y `total_tokens` es prompt + completion + reasoning.

---

## 2. Muestra de 20 videos en español rioplatense

### 2.1 Composición

| Dimensión | Requisito |
|-----------|-----------|
| Duración | 5 de menos de 15 min, 10 de 15 a 45 min, 5 de 45 a 90 min |
| Formato | Podcast, entrevista y stream (los tres presentes) |
| Hablantes | 1, 2 y 3 o más (los tres presentes) |
| Audio difícil | Al menos 4 con música o ruido de fondo |
| Lenguaje | Al menos 3 con lunfardo o code-switching con inglés |

### 2.2 Reglas

| Regla | Detalle |
|-------|---------|
| Derechos de uso | Documentados por video (quién autoriza y para qué) antes de procesarlo |
| Ubicación | Bucket R2 privado de piloto: `[nombre del bucket]` |
| Repositorio | Los videos y las referencias **no** van al repo |
| Identificación | Cada video lleva un `sample_id` (V01 a V20) y el `project_id` que genera la plataforma |

---

## 3. Preparación de referencias

### 3.1 Transcripción de referencia

Transcripción corregida por una persona, sobre el audio completo de cada video. Se guarda fuera del repo, junto a la muestra.

### 3.2 Anclas de tiempo

| Parámetro | Valor |
|-----------|-------|
| Cantidad | 20 por video |
| Posición | Inicios de frase |
| Precisión | 50 ms o menos |
| Herramienta | Audacity o Aegisub |

### 3.3 Normalización para WER

Se fija y se documenta **antes** de correr la evaluación. Se aplica igual a la referencia y a la hipótesis.

| Paso | Regla |
|------|-------|
| 1 | Minúsculas |
| 2 | Sin puntuación |
| 3 | Números a dígitos |
| 4 | Espacios colapsados |
| 5 | Tildes: [se conservan / se quitan] (decisión registrada antes de evaluar) |

Registro de la decisión: [fecha, responsable].

### 3.4 Cálculo automático de WER y timestamps

El comando `evaluate_ai_transcription` calcula las dos métricas automáticas del G1 aplicando la normalización de §3.3. Las referencias se guardan en un directorio **fuera del repo**, con esta estructura:

| Archivo | Contenido |
|---------|-----------|
| `manifest.csv` | Encabezado `video_id,project_id`. Un video aparece una vez por cada proyecto que lo procesó (V2 y legacy) |
| `<video_id>.txt` | Transcripción de referencia (§3.1), en UTF-8 |
| `<video_id>.anchors.csv` | Encabezado `time_seconds,text`: el tiempo de cada ancla (§3.2) y las primeras palabras de la frase |

`video_id` es una etiqueta de la muestra (por ejemplo `v01`), con letras, números, `-` y `_`.

```bash
docker compose run --rm -T -v /ruta/a/referencias:/refs:ro web python manage.py evaluate_ai_transcription --references /refs --accents keep > pilot_eval.csv
```

Sin `--output`, el CSV sale por stdout y el resumen por stderr: el archivo queda en el host y el veredicto en la terminal. Con `--output`, igual que en el export (§1.3), la ruta es dentro del contenedor.

- `--accents keep|strip` es obligatorio y tiene que coincidir con la decisión registrada en §3.3.
- `--window` (5 s por defecto) es cuánto se busca alrededor de cada ancla.
- El CSV tiene una fila por proyecto y una fila `ALL` por motor (`v2`, `legacy`).
  - El WER del `ALL` es el de corpus: Σ errores / Σ palabras de referencia.
  - El error de timestamps es la mediana de todas las anclas del motor.
- Al final se imprime si cada motor cumple los umbrales del §1.1.
- Las anclas sin match se informan aparte, no como error. Muchas anclas sin match indican referencias desalineadas o un ASR con muchos errores en los inicios de frase: revisar antes de dar el G1 por bueno.
- La salida solo tiene ids, códigos de estado y números, nunca texto.

---

## 4. Evaluación humana

| Elemento | Definición |
|----------|------------|
| Evaluadores | 2, ciegos al motor (legacy vs. V2) |
| Orden | Aleatorizado por clip |
| Rúbrica | **Aceptado**; **cambios mínimos** (ajuste de bordes de ±2 s o menos, o retitular); **rechazado** |
| Desacuerdo | Lo resuelve un tercer evaluador |
| Registro | Planilla fuera de la BD, con clave `clip_id` |
| Métrica | (aceptados + cambios mínimos) / total de clips evaluados |

La comparación ciega legacy vs. V2 es solo de **calidad**. No incluye costo: los registros legacy tienen costo NULL (sección 1.1, salvedad h).

La planilla no debe contener títulos ni transcripts del usuario: solo `clip_id`, motor (enmascarado hasta cerrar la evaluación), veredicto de cada evaluador y veredicto final.

---

## 5. Procedimiento del piloto

### 5.1 Pasos

| # | Paso | Gate |
|---|------|------|
| 1 | Etiquetar de forma inmutable `videoedit-back:pre-aicore` (imagen productiva actual) y `videoedit-back:pilot-<sha>` (legacy + V2) | |
| 2 | Desplegar `pilot-<sha>` con `AI_CORE_V2_ENABLED=False` | |
| 3 | Smoke del camino legacy de punta a punta (1 video corto) | |
| 4 | Correr el export y verificar `pipeline_version=legacy` | |
| 5 | Medición en vivo y verificación de precios (ver G0) | **G0** |
| 6 | Activar V2 solo para el usuario interno del piloto: `AI_CORE_V2_ENABLED=True` y `AI_CORE_V2_ALLOWLIST=<uuid del usuario>` (ids separados por coma; se compara con quien subió el video) y reiniciar los workers | |
| 7 | Procesar los 20 videos con V2 y el mismo set con legacy, repartidos en varios días según la capacidad de transcripción (ver 5.4) | |
| 8 | Calcular WER y timestamps (`evaluate_ai_transcription`, §3.4), exportar costo y latencia, completar la evaluación humana | **G1** |
| 9 | Ejecutar los fallos inducidos (sección 6) | **G2** |
| 10 | Habilitación gradual y observación (5.3) | **G3** |
| 11 | Completar el sign-off (sección 9) | |

### 5.2 Gates

| Gate | Condición de paso | Si falla |
|------|-------------------|----------|
| **G0** | La imagen contiene ambos caminos (`python -c "import whisper, torch, groq, litellm"` dentro del contenedor); `python manage.py check` pasa con el `.env` del piloto (ver chequeo de arranque abajo); el smoke legacy está en verde; la medición en vivo y la verificación de precios están hechas (ver abajo) | No se activa V2 |
| **G1** | Se cumple la tabla de la sección 1.1 (los criterios de costo, solo sobre proyectos V2) | Se para, se documenta y se vuelve a diseño. No se avanza |
| **G2** | Ninguna etapa completada se repite (sección 6); los proyectos terminan en el estado correcto; los `NonRetryable` no reintentan | Se corrige antes de abrir a usuarios |
| **G3** | 7 días corridos, 100 o más trabajos V2, ningún disparador de rollback activado y criterios de costo sostenidos | Rollback y reinicio del conteo |

**Chequeo de arranque (ADR-012 D-C3).** `manage.py check` (y por lo tanto `migrate`) falla si la configuración de IA es inválida. No llama a ningún proveedor:

| Código | Qué detecta |
|--------|-------------|
| `videos.E001` | Un modelo configurado es un alias móvil (`*-latest`) |
| `videos.E002` | Un modelo configurado no tiene precio vigente en `pricing.py` (el de transcripción, solo si corre Groq) |
| `videos.E003` | `TRANSCRIPTION_BACKEND` inválido con V2 activo |
| `videos.E004` | `AI_LLM_REASONING_EFFORT` inválido |
| `videos.E005` | Con `AI_CAPACITY_ENABLED`, un modelo que el pipeline reserva no tiene límites válidos en `AI_CAPACITY_LIMITS` (o la variable no es un objeto JSON) |
| `videos.E006` | `AI_CORE_V2_ALLOWLIST` tiene entradas que no son ids de usuario (UUID): nunca coincidirían con nadie |

Ojo: si la variable `AI_CORE_V2_ALLOWLIST` falta o está mal escrita, la lista queda vacía y V2 corre para todos. Verificarla antes del paso 6 de §5.1.

**Qué incluye G0 además de los checks de imagen:**

| Ítem | Qué hacer |
|------|-----------|
| Medición en vivo | Una llamada real para confirmar qué devuelve `response.model` con el modelo fijado y que `reasoning_tokens` sea mayor que 0 en la selección con Gemini (los thinking tokens no vienen en `completion_tokens`; se derivan del total del proveedor y se cobran al precio de salida). Si es 0 con un modelo que razona, verificar contra el usage metadata del proveedor antes de confiar en los costos |
| Precios | Verificar contra las páginas oficiales de Groq y Gemini y comparar con `pricing.py` (`PRICING_VERSION`). Si difieren, corregir por PR antes de medir |
| Perfil de modelos | El piloto usa los mismos modelos, versiones y tiers que producción ([ADR-012](../adrs/012-model-profiles-and-capacity.md), D-C1). Si no, el go/no-go no es válido |
| Privacidad | Los proveedores corren en tier pago, con términos verificados que excluyen el uso de los datos para entrenamiento (ADR-012, D-C2). Sin esto no se procesan videos de usuarios |
| Capacidad | Los límites de transcripción y de LLM alcanzan para el calendario de 5.4. Con los límites de Groq relevados el 2026-10-11, la evaluación se reparte en varios días y G3 requiere ampliar la capacidad |

### 5.3 Habilitación gradual (después de G2)

| Paso | Alcance | Duración mínima |
|------|---------|-----------------|
| 1 | `AI_CORE_V2_ALLOWLIST`: ids del equipo interno | 24 h |
| 2 | `AI_CORE_V2_ALLOWLIST`: ids de la cohorte de creadores beta | 48 h |
| 3 | `AI_CORE_V2_ALLOWLIST` vacía (todos) | Hasta completar 7 días totales y ≥ 100 trabajos V2 |

Cada etapa decide el camino cuando corre, según quien subió el video. Un cambio de la lista o del flag aplica desde la siguiente etapa de un proyecto en curso, y los registros de uso guardan el camino que corrió (`pipeline_version`). `AI_CORE_V2_ENABLED=False` manda todo a legacy aunque la lista tenga ids.

**Monitoreo diario** (export con `--since` del día):

| Indicador | Cómo se obtiene |
|-----------|-----------------|
| Tasa de fallos por etapa y `error_code` | Export nivel `record` |
| Tasa de fallback | `fallback_used` en nivel `project` (ver salvedad a) |
| Latencia p50 y p95 por etapa | `transcription_latency_s` y `selection_latency_s` |
| Costo agregado | `total_cost_usd`, `cost_per_30min_usd` y `cost_complete` |

Un disparador de rollback (sección 7) activa el rollback inmediato y reinicia el conteo.

### 5.4 Capacidad y calendario

Límites de la organización en Groq relevados en la consola el 2026-10-11 (detalle y análisis en [ADR-012](../adrs/012-model-profiles-and-capacity.md)):

| Recurso | Límite actual |
|---------|---------------|
| Audio de `whisper-large-v3-turbo` | 7.200 s por hora (2 h) y 28.800 s por día (8 h) |
| Requests de transcripción | 20 por minuto y 2.000 por día |

Consecuencias para el piloto:

| Fase | Audio estimado | Plan |
|------|----------------|------|
| Evaluación de los 20 videos (2.1) | Entre ~7 h y ~16 h según la composición, más el solapamiento entre chunks | Repartir en **2 o 3 días** y como máximo un video de 90 min por hora. El set legacy usa Whisper local y no consume cuota de Groq |
| G3: ≥ 100 trabajos V2 en 7 días | ~50 h con un promedio de 30 min (~7 h por día) | Al límite, sin margen para picos. **Ampliar la capacidad antes de 5.3** (ADR-012, D-C5) |
| Soft-launch (50 creadores, ~21 videos por día de 25 min) | ~8,75 a 8,9 h por día; el pico de 5 uploads simultáneos suma 7.500 s | Supera el límite diario y el horario. Requisito de producción en ADR-012 |

Al superar un límite, Groq responde 429. El pipeline lo reintenta con backoff (AICORE-7), así que no se pierde trabajo, pero la entrega se demora (ver salvedad k).

Con el rate limiting propio activo (sección 5.5), el pipeline espera **antes** de llamar y los 429 por cuota dejan de ser el camino normal.

### 5.5 Rate limiting propio (AICORE-11)

Con `AI_CAPACITY_ENABLED=True`, cada llamada a Groq (transcripción) y al LLM (selección y SEO) reserva capacidad en token buckets de Valkey antes de salir. Si no hay lugar, la etapa queda en `pending_capacity` y la tarea vuelve a la cola sin ocupar el worker. Esa espera no cuenta como intento fallido ni genera registros de costo. Diseño y decisiones en [ADR-012, D-C4](../adrs/012-model-profiles-and-capacity.md).

**Límites por entorno.** `AI_CAPACITY_LIMITS` es un JSON en el `.env` con una entrada por modelo. Cada valor es el **80 %** del límite que muestra la consola del proveedor. Todo modelo que llame el pipeline necesita su entrada: el primario, el fallback y `groq/<modelo de Whisper>`. Si falta alguna, la etapa falla con `ImproperlyConfigured` antes de llamar al proveedor.

| Entorno | `AI_CAPACITY_ENABLED` | Groq (`groq/whisper-large-v3-turbo`) | LLM (primario y fallback) |
|---------|-----------------------|--------------------------------------|---------------------------|
| Dev / CI | `False` | — | — |
| Piloto / staging | `True` | Con lo relevado el 2026-10-11 (sección 5.4): `audio_seconds_per_hour` 5.760, `audio_seconds_per_day` 23.040, `requests_per_minute` 16, `requests_per_day` 1.600. Con el Developer Tier activo, recalcular con la consola | `tokens_per_minute` y `requests_per_minute` al 80 % del tier pago. **Pendiente de relevar en la consola**: el `.env.example` trae valores de ejemplo |
| Producción | `True` | 80 % del tier elegido en D-C5 | 80 % del tier elegido en D-C5 |

Recursos admitidos: `audio_seconds`, `tokens` y `requests`, por `minute`, `hour` o `day` (por ejemplo, `tokens_per_minute`). Una demanda usa todos los buckets configurados de su recurso.

> **Cuentas compartidas.** Cada entorno tiene su propio Valkey. Si piloto y producción usan la **misma cuenta** de un proveedor, sus buckets no se ven entre sí: repartir el 80 % entre los dos (por ejemplo, 60 % y 20 %).

**Qué se reserva y cómo se ajusta**

| Llamada | Reserva | Ajuste después de la llamada |
|---------|---------|------------------------------|
| Transcripción (Groq) | Duración del video en segundos y un request por chunk de 10 min | Segundos facturados (`audio_seconds`). Si se desconocen, queda la reserva |
| Selección (LiteLLM) | `456 × minutos de audio + 1.500 + 4.000` tokens y un request, en el primario **y** en el fallback si tiene key | Tokens reales de cada intento. Se devuelve lo del modelo que no se usó. Un timeout o una conexión cortada mantienen la reserva |
| Selección legacy | Lo mismo, solo en el primario | Sin ajuste: el selector legacy no informa tokens |
| SEO | `caracteres del título y del transcript del clip / 4 + 1.500 + 4.000` tokens y un request, en el primario y en el fallback | Tokens reales de cada intento, igual que la selección. Si la moderación frena el post antes de llamar al LLM, se devuelve todo |
| Lector de prompts (Prompt-to-Edit) | `caracteres del prompt / 4 + 1.500 + 4.000` tokens y un request, en el primario y en el fallback, antes de la primera etapa | Tokens reales de cada intento, igual que la selección. Si no hay lugar, la etapa que sigue queda en `pending_capacity` y el prompt se parsea al retomar. Si la espera supera el máximo, el proyecto sigue con su configuración (`pte.status=failed`, `CapacityTimeoutError`): el prompt es opcional y no hace fallar el proyecto |

Los coeficientes se configuran con `AI_CAPACITY_LLM_TOKENS_PER_AUDIO_MINUTE`, `AI_CAPACITY_LLM_PROMPT_TOKENS` y `AI_CAPACITY_LLM_OUTPUT_TOKENS`.

**Espera y vencimiento.** Cada re-encolado espera el déficit del bucket, con un tope de `AI_CAPACITY_MAX_COUNTDOWN_SECONDS` (300 s) y entre 0 y 20 % de jitter. Si la espera prevista de una etapa supera `AI_CAPACITY_MAX_WAIT_SECONDS` (3.600 s), el proyecto falla con `pipeline_error_code=CapacityTimeoutError` y se le devuelven los tokens reservados (en SEO, el post queda con `error_log=capacity_timeout`). Si después se re-despacha a mano, corre sin reserva: no se vuelve a cobrar. Una cuota **diaria** agotada produce esperas de horas, así que falla enseguida en vez de esperar al día siguiente: re-despachar cuando vuelva a haber cuota.

Un video más largo que un bucket entero (por ejemplo, 2 h de audio contra 5.760 s por hora) espera a que el bucket esté lleno, pasa y lo deja en negativo. Los siguientes esperan a que se recupere. El bucket se rellena solo y nunca queda negativo de forma permanente.

**Valkey caído.** La reserva falla y la etapa se reintenta con backoff, igual que el lock (AICORE-7). Nunca se llama al proveedor sin reserva.

**Cómo leer la latencia**

- Logs, sin texto del usuario: `pipeline.pending_capacity` (etapa, `countdown_s`, `planned_s`), `pipeline.capacity_granted` (`waited_s`), `pipeline.capacity_timeout`, `seo.pending_capacity`, `seo.capacity_timeout` y `capacity.settle_failed`.
- `VideoProject.metadata["capacity_waited_s"]` guarda los segundos de espera por etapa. Mientras espera, `metadata["capacity_wait"]` muestra la etapa, desde cuándo y la espera planificada.
- La latencia de punta a punta incluye la espera. `transcription_latency_s` y `selection_latency_s` del export miden solo la llamada al proveedor. Para comparar contra el criterio de latencia, restar `capacity_waited_s` y reportar las dos cifras.
- Si hay `pending_capacity` frecuentes y sostenidos, falta capacidad: hay que ampliar el tier (D-C5). Subir los límites por encima del 80 % no sirve, porque solo devuelve los 429.

**Verificación en staging.** Dejar para Groq solo `{"audio_seconds_per_minute": 120, "requests_per_minute": 16}` y subir dos videos de ~90 s, uno detrás del otro. El segundo tiene que quedar en `pending_capacity` unos 30 s, aparecer en `pipeline.pending_capacity` y completarse solo, con `pipeline_attempts` igual a 0 y sin filas de costo durante la espera. Después, restaurar los límites.

---

## 6. Fallo inducido

Los fallos se inyectan **desde afuera**. No hay código de inyección de fallos en la imagen productiva. A nivel de tests unitarios, la inyección se cubre en CORE-06.

| Escenario | Cómo provocarlo | Resultado esperado |
|-----------|-----------------|--------------------|
| Worker muerto a mitad de etapa | `docker kill` del worker durante la transcripción y, en otra corrida, durante la selección; luego levantarlo | La tarea se reentrega y retoma desde la etapa pendiente. No se repite ninguna etapa ya completada |
| Key de LLM inválida | Key inválida en **staging** | La selección falla con error no reintentable, sin reintentos, y el proyecto queda en un estado coherente con `pipeline_error_code` |
| Reinicio de Valkey | Reiniciar el contenedor de Valkey con trabajos en vuelo | Los trabajos se recuperan o fallan de forma controlada, sin duplicar etapas completadas |

### 6.1 Verificación

| Chequeo | Cómo |
|---------|------|
| Un solo cobro de transcripción exitoso por proyecto | Export nivel `record`: contar las filas con `stage=transcription` y `success=True` por `project_id`. El esperado es 1 |
| Sin etapa repetida | Los registros son **solo de inserción**: una ejecución real deja una fila y nunca se pisa otra. Dos filas `success=True` de la misma etapa en un proyecto son dos cobros reales y se investigan (ver la lectura de abajo) |
| Estado final correcto | `status` y `final_stage` en el export nivel `project` |
| `NonRetryable` sin reintentos | Un único registro `success=False` con el `error_code` correspondiente |

**Cómo leer un reintento por fallo de persistencia.** Si la llamada al proveedor se cobró pero falló al guardar el artefacto de la etapa (por ejemplo, perdió la propiedad del proyecto), se escribe una fila `success=True` y el reintento escribe otra, con el mismo `attempt` o con `attempt` N+1. Son **dos cobros reales**, y es lo que debe mostrar el export: la etapa se pagó dos veces. Para distinguirlo de una repetición indebida, cruzar con `pipeline_stage_status` y los timestamps: si la etapa terminó una sola vez pero hay dos filas exitosas, hubo un fallo de persistencia (o una reentrega) y G2 se evalúa sobre esos casos.

**Salvedad:** un worker muerto en medio de la llamada (`docker kill`) **no escribe ninguna fila**: ese cobro es invisible para el export (salvedad i). En el escenario del worker muerto, el conteo de filas es una cota inferior de lo realmente facturado.

### 6.2 Códigos de fallo deterministas

Estos `pipeline_error_code` (y `error_code` del export) marcan un fallo que **no se reintenta**: repetir la misma llamada da el mismo resultado y solo suma cobros. Un video de la muestra que termine así no es un fallo de infraestructura; se analiza por su contenido.

| Código | Etapa | Significado |
|--------|-------|-------------|
| `AINoSpeechDetectedError` | Transcripción | El audio no tiene habla (música, silencio). Igual se registra el cobro de los chunks enviados |
| `AIEmptySelectionError` | Selección | El modelo primario y el fallback respondieron, pero ningún clip pasó la validación (fuera de rango, inválido o lista vacía). El proyecto no avanza a `clips_selected`; un re-despacho vuelve a correr la selección sin repetir la transcripción |
| `AIInvalidRequestError` | Ambas | El proveedor rechazó el pedido con un 4xx (modelo inexistente, contexto excedido, archivo demasiado grande) |
| `AIContentFilterError` | Selección | El proveedor bloqueó el contenido por su política |
| `SoftTimeLimitExceeded` | Cualquiera | La corrida superó `PIPELINE_SOFT_TIME_LIMIT` (2 h por defecto); el lock del proyecto se libera |

Los 401/403 siguen como `AIAuthenticationError`; los 408, 409, 425, 429, 5xx, timeouts y cortes de conexión siguen siendo reintentables.

**Reserva de tokens.** Todo proyecto que termina `FAILED` sin clips (por cualquiera de estos códigos, por moderación, por capacidad o por agotar los reintentos) devuelve los tokens que reservó al crearse: el usuario no recibió nada. Un proyecto que ya tiene clips (sugeridos o renderizados) conserva la reserva. Mientras se reintenta, la reserva se mantiene. Un re-despacho manual posterior corre sin reserva y no se vuelve a cobrar.

**Reservas que quedaron `RESERVED`.** Al cerrar el piloto, y después de cualquier fallo inducido, correr el barrido en modo reporte y, si las acciones son las esperadas, con `--apply`. Las reglas, la salida y lo que queda para resolver a mano están en [07 · Operación: reservas de tokens que quedaron RESERVED](../ia/07-capacidad-costos-y-operacion.md#operación-reservas-de-tokens-que-quedaron-reserved).

```bash
docker compose exec web python manage.py settle_stale_reservations            # reporte (dry run)
docker compose exec web python manage.py settle_stale_reservations --apply    # liquida
```

---

## 7. Rollback

### 7.1 Disparadores

| Disparador | Umbral |
|------------|--------|
| Tasa de fallos de V2 | Mayor que la de legacy + 5 puntos porcentuales en 24 h |
| Costo agregado | Mayor a USD 0,04 por 30 min, medido sobre proyectos V2 (legacy no tiene costo medible, salvedad h) |
| Caída de Groq | Mayor a 30 min |

### 7.2 Pasos

| # | Paso | Nota |
|---|------|------|
| 1 | Poner `AI_CORE_V2_ENABLED=False` | |
| 2 | **Reiniciar los workers** | La variable de entorno se lee al arrancar el proceso. Sin reinicio, no aplica |
| 3 | Verificar con el export que los proyectos nuevos salen con `pipeline_version=legacy` | |
| 4 | Si no alcanza: redeploy de la imagen `videoedit-back:pre-aicore` | |
| 5 | Registrar el disparador, la hora y la acción en el sign-off | |

| Dato | Valor |
|------|-------|
| Responsable del rollback | [a definir] |
| Canal de comunicación | [a definir] |

---

## 8. Caché explícito (TD-02)

Queda condicionado a la evidencia del piloto.

| Condición | Umbral | Cómo medirla |
|-----------|--------|--------------|
| Selecciones que repiten el mismo hash de transcript | Más del 20 % | [fuente a definir: el export actual no incluye el hash de transcript] |
| Peso de la selección en el costo combinado | Más del 40 % | Export nivel `record`: Σ costo de `stage=selection` / Σ costo total |

| Resultado | Acción |
|-----------|--------|
| Se cumple alguna condición | Se habilita el caché explícito y se abre el trabajo correspondiente |
| No se cumple ninguna | Queda descartado y se registra la decisión en el sign-off |

---

## 9. Sign-off

### 9.1 Resultados

| Criterio | Umbral | Resultado | Cumple |
|----------|--------|-----------|--------|
| WER corpus-level | ≤ 15 % | [ ] | [ ] |
| Error mediano de timestamps | ≤ 300 ms | [ ] | [ ] |
| Clips aceptados o con cambios mínimos | ≥ 70 % | [ ] | [ ] |
| Costo combinado por 30 min | < USD 0,04 | [ ] | [ ] |
| Costo p95 por video | Informativo | [ ] | n/a |
| `cost_complete = True` | 100 % de los proyectos | [ ] | [ ] |
| G2: sin etapas repetidas | Sin repeticiones | [ ] | [ ] |
| G3: 7 días y ≥ 100 trabajos V2 sin disparadores | Cumplido | [ ] | [ ] |

### 9.2 Decisión

| Campo | Valor |
|-------|-------|
| Decisión | [go / no-go] |
| Caché explícito (TD-02) | [habilitado / descartado] |
| Observaciones | [ ] |
| Firma | [ ] |
| Fecha | [ ] |

Un go se registra en un addendum de ADR-008 y habilita AICORE-10. En ambos casos se conserva la imagen `pilot-<sha>`.

---

## 10. Salvedades de medición

Límites conocidos de lo que el piloto mide. Leerlos antes de interpretar los números.

| # | Salvedad | Consecuencia |
|---|----------|--------------|
| a | El fallback del LLM es **temporalmente del mismo proveedor** (`gemini-3.5-flash-lite` después de `gemini-3.8-flash`, addendum de ADR-001) | `fallback_used` mide fallos a nivel de modelo. **No** mide resiliencia ante una caída del proveedor, un problema de key o de cuota |
| b | Los precios de Gemini 3.6, 3.7 y 3.8 Flash se duplican el 2027-01-01. `pricing.py` maneja el período | Las comparaciones de costo a ambos lados de esa fecha no son equivalentes |
| c | El SDK de Groq reintenta internamente hasta 2 veces. Un request que expiró del lado del cliente pero se completó en el servidor se factura y es invisible | En ese caso poco frecuente, el costo de ASR es una cota inferior |
| d | Los timeouts y las conexiones cortadas registran costo NULL (desconocido), también en una transcripción con chunks ya cobrados cuyo siguiente chunk expiró | Ese proyecto queda con `cost_complete = False`. Un worker muerto en plena llamada no deja fila alguna (salvedad i) y no aparece ni como NULL |
| e | `response.model` y `completion_tokens` con el modelo fijado deben verificarse con una llamada real en G0 | Los thinking tokens de Gemini no vienen en `completion_tokens`: se capturan como `reasoning_tokens` (total del proveedor menos prompt y completion) y se cobran al precio de salida. Verificar contra el usage metadata del proveedor |
| f | Los precios de Groq y Gemini deben reverificarse contra las páginas oficiales en G0 (`PRICING_VERSION` de `pricing.py`) | Con precios desactualizados, el criterio de costo no es confiable |
| g | La moderación de contenido cubre solo los primeros 10.000 caracteres y no cubre el camino de ingesta (ADR-011) | Fuera del alcance de este piloto, pero es un límite conocido |
| h | La selección legacy no captura el usage del LLM: sus registros llevan costo NULL y todo proyecto legacy tiene `cost_complete = False` | El costo legacy no es comparable con el de V2. Los criterios y el disparador de costo se evalúan solo sobre V2 |
| i | Los registros son solo de inserción y se escriben después de la llamada. Un worker muerto en medio de una llamada no deja registro | Un cobro de un worker matado es invisible: el costo y los conteos son una cota inferior en ese caso |
| j | Un fallo de persistencia después de una llamada cobrada deja una fila exitosa y el reintento otra | Es correcto (dos cobros reales), pero se lee como etapa repetida: ver 6.1 |
| k | Los 429 por límite de capacidad se reintentan con backoff | La latencia p95 sube por capacidad, no por el modelo. Leerla junto con la cantidad de registros con `error_code = AIRateLimitError` del export de nivel `record` |
| l | Los registros anteriores a la migración `0004` (corrida 1 del G0, sección 11) tienen `reasoning_tokens = 0` aunque el modelo haya razonado | El costo de esos registros es una cota inferior aunque `cost_complete` sea `True`; no mezclarlos con las mediciones nuevas |

## 11. Registro de ejecución

### 11.1 G0, primera corrida (2026-10-12): NO PASA

| Ítem del G0 | Resultado | Detalle |
|-------------|-----------|---------|
| Imagen con ambos caminos | Cumplido | Imports de `whisper`, `torch`, `groq` y `litellm` verificados en el contenedor |
| Medición en vivo | Cumplido, con hallazgo | Video propio de 763 s: 5.800 tokens de entrada en la selección (~456 por minuto). El primario respondió 503 y el fallback completó la etapa. `resolved_model` devuelve el modelo fijado |
| Tokens de razonamiento | **Bloqueante para el costo** | No se cuentan en `completion_tokens`: el costo de selección con Gemini está subestimado. Ver [ADR-012](../adrs/012-model-profiles-and-capacity.md), "Medición del G0" |
| Precios | Cumplido | `pricing.py` coincide con las páginas oficiales de Groq y Gemini (consulta del 2026-10-12) |
| Perfil de modelos y privacidad | **No cumplido** | El proyecto de Google estaba en tier gratuito (20 requests por día en `gemini-3.8-flash` y uso de datos para entrenamiento) |
| Capacidad | **No cumplido** | Groq en límites de desarrollo (8 h de audio por día) |

### 11.2 Para la segunda corrida del G0

1. Tier pago de Google y Developer Tier de Groq activos (decisión del owner del 2026-10-13); confirmar en las consolas los límites efectivos.
2. Corrección de los tokens de razonamiento mergeada (conteo como salida y columna en `AIUsageRecord`).
3. Repetir la medición con un video de ~25 min y el primario `gemini-3.8-flash`, y verificar el costo por 30 min contra USD 0,04 con los tokens de razonamiento incluidos.
4. Si el costo supera el umbral, evaluar limitar el razonamiento del modelo y validar la calidad de los clips antes de G1.

### 11.3 Medición del nivel de razonamiento (AICORE-14)

Corresponde al paso 4 de §11.2. Requiere el PR del #66 desplegado.

1. **Chequeo rápido de que el proveedor respeta el límite**, con las keys del entorno:

   ```bash
   docker compose run --rm -e RUN_LIVE_AI=1 web python manage.py test apps.videos.tests.live.test_live_ai_smoke.LiveReasoningEffortSmokeTest
   ```

   Imprime los tokens de razonamiento con `low` y con `high` (solo números) y falla si `low` razona más que `high`.
2. **Corridas:** con el mismo video de ~25 min y el primario `gemini-3.8-flash`, procesar una vez sin el setting (referencia) y una vez con cada nivel `low`, `medium` y `high`. El nivel se cambia con `AI_LLM_REASONING_EFFORT` en el entorno del worker.
3. **Registro por corrida:**
   - Nivel con el que corrió la selección: columna `reasoning_effort` del export nivel `project` (`low`, `medium`, `high`, `minimal` o `default`). Se guarda por proyecto, así que las corridas se distinguen sin anotarlas aparte.
   - Costo por 30 min (export nivel `project`).
   - Tokens de salida de la selección (export nivel `record`).
   - Revisión humana de los clips según §4.
4. **Elección:** el nivel más bajo cuyos clips pasan la revisión. Se registra en [ADR-012](../adrs/012-model-profiles-and-capacity.md) por entorno, y se verifica que el costo por 30 min quede por debajo de USD 0,04.
