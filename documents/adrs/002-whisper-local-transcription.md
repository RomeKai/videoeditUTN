# ADR-002: Whisper Local vs API de Transcripción

**Estado:** `accepted`
**Fecha:** 2026-06 (decisión original) | 2026-09-01 (documentación formal)
**Par responsable:** 🧠 Par IA (Dev 1 + Dev 2)

---

## Contexto

El pipeline de OneCreator requiere transcripción de audio con timestamps a nivel de palabra (word-level timestamps) para:
1. Selección de clips virales (el LLM analiza la transcripción)
2. Generación de subtítulos sincronizados (word-by-word animation)
3. Posible speaker diarization futura (CORE-02)

La transcripción es la operación más costosa en tiempo del pipeline: un video de 763s tardó ~7 minutos solo en transcripción corriendo en CPU.

### Restricciones

- Word-level timestamps son obligatorios (no solo segmentos)
- El servicio debe funcionar offline (no depender de disponibilidad de API externa durante el render)
- Soporte multilenguaje (español, inglés como mínimo)
- Budget limitado para APIs externas

---

## Opciones Evaluadas

### Opción A: Whisper Local (openai-whisper)

- **Pros:** Sin costo por request. Control total sobre el modelo y versión. Funciona offline. Word-level timestamps nativos con `word_timestamps=True`. Sin rate limits. Datos del usuario nunca salen del servidor.
- **Contras:** Requiere GPU para performance aceptable (CPU es 5-10x más lento). El modelo consume RAM (tiny=75MB, base=142MB, small=466MB, medium=1.5GB, large=2.9GB). Mantenimiento del modelo es responsabilidad del equipo.
- **Costo:** $0 por request. Costo de GPU si se necesita aceleración (~$0.50-1.00/h por T4/A10).

### Opción B: OpenAI Whisper API

- **Pros:** Sin infraestructura de GPU. Calidad de transcripción consistente. Rápido (~30s para 10min de audio).
- **Contras:** **No soporta word-level timestamps** (solo segmentos). $0.006/min de audio. Requiere enviar audio del usuario a servidores externos. Rate limits. Dependencia de red.
- **Costo:** ~$0.06 por video de 10 min. A 1000 videos/día = $60/día.

### Opción C: Google Speech-to-Text v2

- **Pros:** Word-level timestamps soportados. Buena calidad multilenguaje. Free tier (60 min/mes).
- **Contras:** Pricing complejo ($0.016/15s para enhanced model). Requiere GCP account. Latencia de red. Setup de credentials más complejo.
- **Costo:** ~$0.64 por video de 10 min.

### Opción D: Faster-Whisper (CTranslate2)

- **Pros:** 4x más rápido que Whisper original en CPU. Menos RAM. Compatible con los mismos modelos. Word-level timestamps soportados.
- **Contras:** Proyecto community-maintained (no OpenAI). API ligeramente diferente. Menos documentación.
- **Costo:** $0 por request (mismo que Whisper local).

---

## Decisión

**Whisper Local (openai-whisper)** — con las siguientes condiciones:

1. Modelo `tiny` para dev/testing, `small` para producción con GPU
2. Lazy loading del modelo (ya implementado en `TranscriptionEngine`)
3. Detección automática de CUDA/MPS para aceleración
4. Plan de migración a `faster-whisper` en roadmap post-MVP (CORE-04 subtarea)

### Razón principal

La API de Whisper de OpenAI **no soporta word-level timestamps**, que son obligatorios para el sistema de subtítulos animados. Esto descartó la opción B desde el inicio. Whisper local ofrece control total y $0 de costo operativo en transcripción.

---

## Consecuencias

### Positivas
- Zero costo operativo en transcripción
- Datos del usuario nunca salen del servidor (privacy by design)
- Word-level timestamps nativos
- Funciona offline — el pipeline no depende de APIs externas para esta etapa

### Negativas
- Requiere GPU para performance aceptable en producción (el worker necesita acceso a CUDA)
- El modelo consume memoria — afecta el sizing de workers Celery
- El equipo es responsable de mantener la versión del modelo

### Riesgos
- **Performance en CPU**: sin GPU, un video de 10min tarda ~5-7 minutos en transcribir. Esto es el bottleneck principal del pipeline → CORE-04 mitiga esto.
- **Memory leaks**: el modelo Whisper puede acumular memoria entre transcripciones → `max_tasks_per_child` en Celery mitiga.

---

## Referencias

- [Código: TranscriptionEngine](../../back/apps/videos/services/transcription_engine.py)
- [AI-DECISIONS.md #2](../../AI-DECISIONS.md) — registro original de esta decisión
- [OpenAI Whisper API limitations](https://platform.openai.com/docs/guides/speech-to-text)
