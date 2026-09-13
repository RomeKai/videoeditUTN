# ADR-007: Core de IA remoto para una VPS de bajos recursos

**Estado:** `proposed`
**Fecha:** 2026-09-13
**Par responsable:** 🧠 Par IA (Dev 1 + Dev 2)
**Revisa:** [ADR-002 — Whisper local](002-whisper-local-transcription.md)
**Complementa:** [ADR-001 — Proveedor LLM](001-llm-provider-selection.md)

---

## Contexto

La ingesta actual ejecuta `openai-whisper` dentro del worker Celery. El worker carga PyTorch y Whisper, usa CPU si no encuentra CUDA y comparte la VPS con FFmpeg, MoviePy, Django y Celery.

En una medición documentada, un video de 763 segundos necesitó aproximadamente siete minutos de transcripción en CPU. Esta arquitectura contradice el deployment objetivo: una VPS económica, sin GPU y con poca memoria.

La tarea de ingesta también encadena subida, proxy, transcripción, selección y persistencia. Si una etapa posterior falla, el retry puede repetir operaciones externas ya completadas y duplicar tiempo o costo.

### Fuerzas de decisión

- Timestamps a nivel de palabra obligatorios para subtítulos.
- Español e inglés como mínimo; prioridad para español rioplatense.
- VPS sin GPU dedicada.
- Costo variable bajo y medible.
- Recuperación de fallos sin repetir etapas completadas.
- Privacidad: enviar a cada proveedor únicamente el insumo necesario.
- Rollback controlado durante el piloto.

---

## Opciones evaluadas

### Opción A: mantener `openai-whisper` local

- **Pros:** funciona sin red, no factura por minuto y mantiene el audio en infraestructura propia.
- **Contras:** consumo alto de CPU/RAM, imagen Docker pesada, baja concurrencia y necesidad probable de GPU.

### Opción B: migrar a `faster-whisper` local

- **Pros:** mejora rendimiento y memoria respecto de Whisper original.
- **Contras:** la VPS sigue ejecutando inferencia; reduce, pero no elimina, el cuello de botella.

### Opción C: Groq Whisper Large V3 Turbo

- **Pros:** timestamps de palabra y segmento, soporte multilingüe, SDK oficial, precio publicado de USD 0,04 por hora y velocidad alta.
- **Contras:** dependencia de red/proveedor, envío de audio y límites de tamaño por archivo.

### Opción D: otra API de transcripción

- **Pros:** diversificación potencial y funciones como diarización según proveedor.
- **Contras:** no mejora la relación precio/rendimiento del caso inicial y amplía la superficie de integración antes de medir el piloto.

---

## Decisión propuesta

Migrar la transcripción principal a **Groq `whisper-large-v3-turbo` mediante el SDK oficial de Groq**.

La VPS conserva:

- Coordinación Celery.
- Descarga y almacenamiento R2.
- FFmpeg para normalización de audio y proxy.
- Validación de contratos.
- Persistencia y renderizado.

La VPS deja de ejecutar inferencia de voz una vez aprobado el piloto y retirada la imagen legacy.

### Preprocesamiento de audio

1. Extraer la primera pista de audio a FLAC mono de 16 kHz.
2. Usar carga directa mientras el archivo no supere `24_000_000` bytes.
3. Si lo supera, dividir en ventanas de 600 segundos con 2 segundos de solapamiento.
4. Verificar el tamaño de cada chunk antes de llamar a Groq.
5. Solicitar `verbose_json` con granularidades `word` y `segment`.
6. Aplicar el offset absoluto de cada chunk.
7. Eliminar duplicados del solapamiento y limpiar temporales en éxito o error.

La configuración inicial usa `language="es"` y `temperature=0.0`. El idioma debe seguir siendo configurable para contenido inglés o detección automática en una evolución posterior.

### Contrato normalizado

El proveedor debe devolver:

```json
{
  "language": "es",
  "text": "Transcripción completa",
  "duration_seconds": 1800.0,
  "words": [
    {"start": 0.12, "end": 0.44, "text": "Hola"}
  ],
  "segments": [
    {"start": 0.0, "end": 2.1, "text": "Hola, ¿cómo están?"}
  ]
}
```

`TranscriptionEngine.transcribe(audio_path, word_timestamps=True)` debe continuar devolviendo la lista `{start, end, text}` para no romper `SubtitleEngine`, Paper Edit ni los tests existentes.

### Resiliencia e idempotencia

La transcripción se persiste antes de invocar el selector LLM. `VideoProject.metadata.ai_pipeline` guardará por etapa:

```json
{
  "transcription": {
    "status": "completed",
    "provider": "groq",
    "model": "whisper-large-v3-turbo",
    "latency_ms": 820,
    "attempts": 1,
    "audio_seconds": 1800.0,
    "estimated_cost_usd": 0.02,
    "completed_at": "2026-09-13T12:00:00Z"
  }
}
```

No se agrega una tabla ni una migración en esta entrega. Los campos JSON existentes permiten validar el diseño antes de fijar un esquema relacional.

Reglas:

- `original_r2_key` existente evita repetir la subida original.
- `proxy_r2_key` existente evita repetir proxy y subida.
- `transcript_data` más checkpoint `completed` evita repetir Groq.
- Selección más checkpoint `completed` evita repetir Gemini y crear clips duplicados.
- Un lock distribuido por `project_id` evita dos ingestas simultáneas.
- Errores de autenticación/configuración no se reintentan.
- Timeout, conexión, 429 y 5xx usan reintento con backoff.
- Logs de error guardan tipo, etapa e intento, no payloads.

### Rollout y rollback

Durante el piloto, `AI_CORE_V2_ENABLED=False` conserva el flujo local. En el entorno de evaluación se activa `True` para los 20 videos seleccionados.

Después de aprobar el piloto:

1. Activar el flujo remoto en producción.
2. Observar durante siete días y al menos 100 trabajos; se exige que ambos umbrales se cumplan.
3. Retirar `openai-whisper` y la instalación de PyTorch del Dockerfile.
4. Conservar la imagen anterior etiquetada para rollback.

Una vez retirado Whisper local, una caída de Groq debe dejar el trabajo recuperable/en cola. No se permite fallback automático a inferencia local en la VPS.

---

## Consecuencias

### Positivas

- La VPS no reserva RAM/CPU para el modelo de voz.
- La imagen final elimina PyTorch y `openai-whisper`.
- El costo se vuelve proporcional a los minutos procesados y queda registrado por trabajo.
- Se mantienen timestamps por palabra.
- Una falla del selector no vuelve a facturar transcripción.

### Negativas

- El pipeline deja de ser completamente offline.
- Audio del usuario se transmite a un tercero.
- Se necesita política de reintento, timeout y límites de archivo.
- El costo cero por request de Whisper local se reemplaza por costo variable.

### Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| API no disponible o rate limit | Backoff, checkpoint y cola recuperable |
| Archivo fuera del límite | FLAC mono/16 kHz y chunks de 600 s |
| Duplicados por solapamiento | Offset absoluto y deduplicación temporal |
| Calidad insuficiente en audio difícil | Medir WER; evaluar `whisper-large-v3` solo como retry manual |
| Exposición de audio | Minimización, temporales efímeros y revisión de términos/retención |
| Doble ejecución Celery | Lock distribuido usando Valkey/Redis protocol |
| Cambio de precio/modelo | Settings configurables y revisión trimestral |

---

## Plan de implementación asociado

El [plan ejecutable completo](../../docs/superpowers/plans/2026-09-13-modernizacion-core-ia.md) se divide en estos gates:

| Gate | Entrega | ADR principal | Criterio de salida |
|------|---------|---------------|--------------------|
| 1 | Dependencias, settings, contratos y errores tipados | ADR-001 + ADR-007 | Contratos Pydantic en verde |
| 2 | FLAC 16 kHz y fragmentación | ADR-007 | Chunks dentro del límite y temporales limpios |
| 3 | Proveedor Groq | ADR-007 | Timestamps normalizados sin red en tests |
| 4 | Selector LiteLLM/Gemini | ADR-001 | Schema estricto, fallback y métricas |
| 5 | Adaptadores compatibles | ADR-001 + ADR-007 | APIs actuales sin regresión |
| 6 | Checkpoints y retries | ADR-007 | No se repiten etapas completadas |
| 7 | Exportación y piloto | ADR-001 + ADR-007 | 20 videos medidos |
| 8 | Cutover y retiro local | ADR-006 + ADR-007 | Ventana estable y worker sin Whisper/PyTorch |

No se debe comenzar el gate 8 hasta aceptar este ADR y el ADR-001 con resultados del piloto.

---

## Criterios para aceptar este ADR

| Métrica | Umbral |
|---------|--------|
| WER en la muestra etiquetada | <= 15% |
| Error mediano de timestamps | <= 300 ms |
| Sugerencias aceptadas o con edición mínima | >= 70% |
| Costo combinado equivalente a 30 min | < USD 0,04 con tarifas del piloto |
| Recuperación | Cero repetición de etapas completadas |
| Worker final | Sin imports instalables de `whisper` ni `torch` |

### Transición de estado

Mientras el piloto no finalice:

- ADR-002 continúa `accepted`.
- ADR-007 continúa `proposed`.

Si los umbrales se cumplen:

- Cambiar ADR-007 a `accepted` con fecha y enlace a resultados.
- Cambiar ADR-002 a `superseded by ADR-007` sin borrar su contenido histórico.
- Actualizar ADR-006 para retirar el requisito de GPU de producción.

Si los umbrales no se cumplen, documentar el resultado y mantener ADR-002 como decisión activa.

---

## Referencias

- [ADR-001: Proveedor LLM](001-llm-provider-selection.md)
- [ADR-002: Whisper local](002-whisper-local-transcription.md)
- [ADR-006: Deployment](006-deployment-strategy.md)
- [Código actual: TranscriptionEngine](../../back/apps/videos/services/transcription_engine.py)
- [Código actual: tarea de ingesta](../../back/apps/videos/tasks.py)
- [Groq Speech to Text](https://console.groq.com/docs/speech-to-text)
- [Groq SDK Python](https://pypi.org/project/groq/)
