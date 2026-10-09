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
```

| Argumento | Valor | Nota |
|-----------|-------|------|
| `--since` | `YYYY-MM-DD` (obligatorio) | Inclusivo, 00:00 UTC |
| `--until` | `YYYY-MM-DD` (opcional) | **Exclusivo**, 00:00 UTC |
| `--level` | `project` (default) o `record` | `project`: una fila por proyecto creado en la ventana, incluso sin registros |
| `--format` | `csv` (default) o `jsonl` | Ventana vacía: solo encabezado (csv) o nada (jsonl), exit 0 |
| `--output` | ruta | Sin este argumento escribe a stdout |

**Privacidad:** el export no incluye títulos, transcripts, prompts, reasoning, email ni id de usuario. Los registros no tienen columnas de texto libre.

**Costo por 30 min:** usa la duración del video fuente, no `audio_seconds`. Si falta la duración, la columna queda vacía.

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
| 6 | Activar V2 solo para el usuario interno del piloto: maestro en `True` y allowlist = `[pilot_user_id]` | |
| 7 | Procesar los 20 videos con V2 y el mismo set con legacy | |
| 8 | Calcular WER y timestamps, exportar costo y latencia, completar la evaluación humana | **G1** |
| 9 | Ejecutar los fallos inducidos (sección 6) | **G2** |
| 10 | Habilitación gradual y observación (5.3) | **G3** |
| 11 | Completar el sign-off (sección 9) | |

### 5.2 Gates

| Gate | Condición de paso | Si falla |
|------|-------------------|----------|
| **G0** | La imagen contiene ambos caminos (`python -c "import whisper, torch, groq, litellm"` dentro del contenedor); el smoke legacy está en verde; la medición en vivo y la verificación de precios están hechas (ver abajo) | No se activa V2 |
| **G1** | Se cumple la tabla de la sección 1.1 | Se para, se documenta y se vuelve a diseño. No se avanza |
| **G2** | Ninguna etapa completada se repite (sección 6); los proyectos terminan en el estado correcto; los `NonRetryable` no reintentan | Se corrige antes de abrir a usuarios |
| **G3** | 7 días corridos, 100 o más trabajos V2, ningún disparador de rollback activado y criterios de costo sostenidos | Rollback y reinicio del conteo |

**Qué incluye G0 además de los checks de imagen:**

| Ítem | Qué hacer |
|------|-----------|
| Medición en vivo | Una llamada real para confirmar qué devuelve `response.model` con el modelo fijado y si `completion_tokens` incluye los thinking tokens de Gemini. Si no los incluye, el costo está subestimado: verificar contra el usage metadata del proveedor antes de confiar en los costos |
| Precios | Verificar contra las páginas oficiales de Groq y Gemini y comparar con `pricing.py` (`PRICING_VERSION`). Si difieren, corregir por PR antes de medir |

### 5.3 Habilitación gradual (después de G2)

| Paso | Alcance | Duración mínima |
|------|---------|-----------------|
| 1 | Allowlist: equipo interno | 24 h |
| 2 | Allowlist: cohorte de creadores beta | 48 h |
| 3 | Allowlist vacía (todos) | Hasta completar 7 días totales y ≥ 100 trabajos V2 |

**Monitoreo diario** (export con `--since` del día):

| Indicador | Cómo se obtiene |
|-----------|-----------------|
| Tasa de fallos por etapa y `error_code` | Export nivel `record` |
| Tasa de fallback | `fallback_used` en nivel `project` (ver salvedad a) |
| Latencia p50 y p95 por etapa | `transcription_latency_s` y `selection_latency_s` |
| Costo agregado | `total_cost_usd`, `cost_per_30min_usd` y `cost_complete` |

Un disparador de rollback (sección 7) activa el rollback inmediato y reinicia el conteo.

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
| Un solo cobro de transcripción exitoso por proyecto | Export nivel `record`: una sola fila `stage=transcription`, `success=True` por `project_id` |
| Sin etapa repetida | Revisar `attempt` en `AIUsageRecord` y los timestamps de etapa (`pipeline_stage_status`): una etapa completada no reaparece con un `attempt` posterior |
| Estado final correcto | `status` y `final_stage` en el export nivel `project` |
| `NonRetryable` sin reintentos | Un único registro `success=False` con el `error_code` correspondiente |

---

## 7. Rollback

### 7.1 Disparadores

| Disparador | Umbral |
|------------|--------|
| Tasa de fallos de V2 | Mayor que la de legacy + 5 puntos porcentuales en 24 h |
| Costo agregado | Mayor a USD 0,04 por 30 min |
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
| d | Los timeouts y las conexiones cortadas registran costo NULL (desconocido) | Ese proyecto queda con `cost_complete = False` |
| e | `response.model` y `completion_tokens` con el modelo fijado deben verificarse con una llamada real en G0 | Si `completion_tokens` no incluye los thinking tokens de Gemini, el costo está subestimado: verificar contra el usage metadata del proveedor antes de confiar en los costos |
| f | Los precios de Groq y Gemini deben reverificarse contra las páginas oficiales en G0 (`PRICING_VERSION` de `pricing.py`) | Con precios desactualizados, el criterio de costo no es confiable |
| g | La moderación de contenido cubre solo los primeros 10.000 caracteres y no cubre el camino de ingesta (ADR-011) | Fuera del alcance de este piloto, pero es un límite conocido |
