# ADR-002: Whisper Local vs API de Transcripción

**Estado:** `superseded`
**Fecha:** 2026-06 (decisión original) | 2026-09-01 (documentación formal) | 2026-09-17 (superado por arquitectura AICORE)
**Superado por:** [ADR-007: Migración a Groq Whisper API (AICORE)](007-groq-whisper-migration.md) / Épica AICORE
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

## Addendum: Justificación de Transición a Groq Whisper API (Épicas AICORE)

> **Nota para los desarrolladores:** Esta decisión quedó superada en Septiembre 2026 al proyectar el despliegue del SaaS en una VPS de recursos estándar (2-4 vCPU, 4-8 GB RAM) sin GPU dedicada.

### 1. La premisa técnica original fue invalidada
La razón principal para adoptar Whisper local en ADR-002 fue: *"La API de Whisper de OpenAI no soporta word-level timestamps"*.
Esta limitación quedó superada con **Groq Cloud**:
- Groq expone la API de Whisper (modelo `whisper-large-v3-turbo` y `whisper-large-v3`) con soporte completo de timestamps a nivel de palabra mediante:
  ```python
  response = client.audio.transcriptions.create(
      file=audio_file,
      model="whisper-large-v3-turbo",
      response_format="verbose_json",
      timestamp_granularities=["word"]
  )
  ```
- Devuelve la lista exacta de palabras con `start` y `end` en segundos, compatible 100% con la estructura que consume `SubtitleEngine` y `SelectionEngine`.

### 2. Impacto Masivo en Infraestructura y Docker
Mantener PyTorch y modelos locales en el worker de Celery generaba costos y riesgos inviables para un SaaS en VPS:
- **Reducción del Dockerfile**: Al eliminar `torch`, `torchaudio` y `torchvision` (línea 29 del `Dockerfile`), la imagen del contenedor se reduce de **~3.5 GB a ~800 MB**, reduciendo drásticamente los tiempos de CI/CD y despliegue.
- **Eliminación de Out-Of-Memory (OOM)**: Whisper `small`/`medium` en CPU consumía entre 1.5 GB y 3 GB de RAM por proceso Celery. En un servidor con 4 GB de RAM, dos tareas concurrentes provocaban el reinicio forzoso del kernel Linux (OOM Killer). Con Groq, el worker realiza una llamada HTTP streaming consumiendo < 50 MB de RAM.
- **Velocidad de transcripción**:
  - Whisper local en CPU: **5 a 7 minutos** para un video de 10 min.
  - Groq LPU (Language Processing Unit): **~3 a 5 segundos** para el mismo audio (~150x a 200x tiempo real).

### 3. Costo Económico en Producción
- `whisper-large-v3-turbo` en Groq tiene un costo de **$0.04 por hora de audio** (~$0.00067 por minuto).
- Un video de 10 minutos cuesta **$0.0067 USD**.
- Costo de alquilar una VPS con GPU (mínimo NVIDIA T4 en AWS/RunPod/Hetzner): **$150 - $350 USD/mes**.
- Con Groq se necesitarían procesar más de **25.000 videos al mes** para que una GPU dedicada empiece a ser más económica.

### 4. Guía de Migración para el Equipo
1. **Configuración**: Introducir `GROQ_API_KEY` en el entorno.
2. **Estrategia en `TranscriptionEngine`**:
   - Primario: `GroqTranscriptionStrategy` usando `whisper-large-v3-turbo` con `timestamp_granularities=["word"]`.
   - Fallback Cloud: `OpenAITranscriptionStrategy` (si Groq reporta 503/Rate Limit).
   - Modo Offline/Local: Si `TRANSCRIPTION_BACKEND=local` en dev, mantener la capacidad de usar un Whisper `tiny` local sin forzar dependencias pesadas en la imagen base de producción.

---

## Referencias

- [Código: TranscriptionEngine](../../back/apps/videos/services/transcription_engine.py)
- [AI-DECISIONS.md #2](../../AI-DECISIONS.md) — registro original de esta decisión
- [Groq Whisper Documentation & Word Timestamps](https://console.groq.com/docs/speech-text)
- [OpenAI Whisper API limitations](https://platform.openai.com/docs/guides/speech-text)
