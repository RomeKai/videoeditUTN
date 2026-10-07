# ADR-008: Transcripción remota con Groq Whisper y eliminación del fallback local

**Estado:** `accepted`
**Fecha:** 2026-10-06
**Supersede a:** [ADR-002: Whisper local vs API de transcripción](002-whisper-local-transcription.md)
**Par responsable:** 🧠 Par IA (Dev 1 + Dev 2)
**Issues:** #39 (AICORE-5), #43 (AICORE-9), #44 (AICORE-10)

---

## Contexto

ADR-002 eligió `openai-whisper` local porque la API de OpenAI no devolvía timestamps por palabra. Esa premisa ya no vale: Groq expone `whisper-large-v3-turbo` con `timestamp_granularities=["word"]`. El addendum de ADR-002 documenta la evaluación (latencia, costo e imagen Docker).

Quedaba un problema operativo. Tras la integración inicial de AICORE, `TranscriptionEngine` atrapaba **cualquier** excepción de Groq y caía en silencio a Whisper local. En el worker target (VPS de 2-4 vCPU y 4-8 GB sin GPU) eso significa cargar un modelo de 0.5-3 GB en un proceso que puede estar renderizando en paralelo, lo que lleva a un OOM kill del contenedor. Además, el error real (rate limit, key inválida) desaparece de los logs y de las métricas.

### Restricciones

- Word-level timestamps obligatorios (subtítulos animados, selección).
- El worker no tiene GPU y comparte memoria con el render.
- El piloto (AICORE-9) exige que la imagen mantenga ambos caminos para poder hacer rollback por flag.

---

## Opciones evaluadas

| Opción | Pros | Contras |
|--------|------|---------|
| **A. Groq + fallback automático a Whisper local** (estado previo) | Tolera caídas de Groq | Riesgo de OOM, oculta errores, duplica costo de cómputo y hace inviable el sizing del worker |
| **B. Groq + retry en Celery, local solo por flag explícito** | Fallos tipados y visibles, memoria predecible, rollback controlado | Si Groq cae, los trabajos esperan (backoff) en lugar de degradar |
| **C. Groq + fallback a otra API remota (Deepgram / OpenAI)** | Resiliencia sin costo de RAM | Segundo contrato, segunda key y diferencias de formato; se pospone hasta tener datos del piloto |

---

## Decisión

**Opción B.**

1. **Ruteo** (`TranscriptionEngine.resolve_backend()`, evaluado en cada llamada):
   - `AI_CORE_V2_ENABLED=False` → Whisper local (camino legacy, kill switch del piloto).
   - `AI_CORE_V2_ENABLED=True` → `TRANSCRIPTION_BACKEND` (`groq` por defecto, `local` para dev o rollback).
   - Un valor inválido lanza `ImproperlyConfigured`.
2. **Sin fallback silencioso.** Los errores de Groq se propagan con la jerarquía AICORE (`errors.py`):
   - `RetryableAIError` (429, timeout, 5xx, conexión): la tarea Celery reintenta con backoff exponencial.
   - `NonRetryableAIError` (401/403, key ausente, contrato inválido): fail-fast, sin reintentos.
3. **Import liviano.** `whisper` y `torch` solo se importan al cargar el modelo local. Importar `transcription_engine` no carga modelos ni clientes de red.
4. **Contrato.** `transcribe()` mantiene firma y formato `{start, end, text}`. `transcribe_detailed()` devuelve `AIExecutionResult[TranscriptionResult]` con proveedor, modelo, latencia y costo, que alimenta AICORE-8.

---

## Consecuencias

### Positivas

- El consumo de memoria del worker es predecible (<50 MB por transcripción remota).
- Las fallas quedan visibles y clasificadas, lo que habilita las métricas del piloto y las alertas.
- El rollback no requiere redeploy: alcanza con apagar el flag.

### Negativas

- Durante una caída de Groq los trabajos se demoran (retries) en vez de completarse en CPU.
- Hasta AICORE-10 la imagen sigue cargando `torch` y `openai-whisper` (~3.5 GB) para sostener el rollback.

### Riesgos y mitigaciones

- **Dependencia de un único proveedor ASR:** evaluar la opción C con datos de disponibilidad del piloto (#42).
- **Retry storm ante un 429 sostenido:** respetar `AIRateLimitError.retry_after` en la política de retry de la tarea (AICORE-7, #41).
- **El reintento de la tarea completa repite etapas ya pagas:** persistir la transcripción antes de la selección (AICORE-7, #41).

---

## Plan de retiro (AICORE-10)

Cuando el piloto supere los umbrales de AICORE-9: eliminar `openai-whisper` y `torch` de `requirements/ia.txt` y del `Dockerfile`, y eliminar el backend `local`. `AI_CORE_V2_ENABLED` queda como kill switch, y el rollback pasa a ser desplegar la imagen anterior.

---

## Referencias

- [Código: TranscriptionEngine](../../back/apps/videos/services/transcription_engine.py)
- [Código: GroqTranscriptionProvider](../../back/apps/videos/services/ai/groq_transcription.py)
- [Código: jerarquía de errores AICORE](../../back/apps/videos/services/ai/errors.py)
- [Groq Speech-to-Text](https://console.groq.com/docs/speech-text)
