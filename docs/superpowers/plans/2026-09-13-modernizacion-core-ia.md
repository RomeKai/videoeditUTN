# Modernización del Core de IA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrar la transcripción a Groq Whisper Large V3 Turbo y la selección de clips a Gemini 3.8 Flash mediante LiteLLM SDK, manteniendo el contrato actual de OneCreator y reduciendo CPU, RAM y costo de reintentos en una VPS pequeña.

**Architecture:** Celery continúa orquestando la ingesta. FFmpeg normaliza y, cuando sea necesario, fragmenta el audio; el SDK oficial de Groq realiza la transcripción. LiteLLM Router llama a Gemini como proveedor primario y conserva OpenAI como fallback del selector. Los adaptadores existentes siguen exponiendo listas de palabras y clips, mientras contratos Pydantic validan las respuestas y los JSON existentes de `VideoProject` guardan checkpoints y métricas sin migración de base de datos.

**Tech Stack:** Python 3.11, Django 5, Celery, FFmpeg/ffprobe, Groq Python SDK 1.7.0, LiteLLM 1.100.1, Pydantic 2.13.5, Django TestCase/unittest.mock.

**Spec:** [ADR-001 — Proveedor LLM](../../../documents/adrs/001-llm-provider-selection.md), [ADR-007 — Core de IA remoto](../../../documents/adrs/007-remote-ai-core.md) y el impacto condicional sobre [ADR-006 — Deployment](../../../documents/adrs/006-deployment-strategy.md).

## Global Constraints

- Usar LiteLLM como SDK embebido; no desplegar LiteLLM Proxy en esta etapa.
- Usar el SDK oficial de Groq directamente para transcripción.
- Modelo primario de clips: `gemini/gemini-3.8-flash`; fallback inicial: `openai/gpt-4o-mini` para conservar la contingencia ya existente.
- Mantener los valores `fast` y `smart` de `VideoProject.intelligence_level`; mapearlos a razonamiento `low` y `medium`, respectivamente. No cambiar choices ni crear migraciones en esta entrega.
- No activar caché explícito de Gemini en la primera versión. Registrar tokens de caché implícito si el proveedor los devuelve y decidir el caché explícito después del piloto.
- No enviar el video a Gemini: solo transcripción, duración, título, estilo y cantidad objetivo de clips.
- No registrar API keys, audio, transcripciones completas ni prompts completos.
- No modificar SEO, renderizado, face tracking, subtítulos animados, pagos ni el contrato de Paper Edit.
- Durante el piloto, conservar el camino local actual detrás de `AI_CORE_V2_ENABLED=False`. Después del corte definitivo, el rollback será desplegar la imagen anterior; producción no caerá automáticamente a Whisper local.
- Simular todas las APIs externas en tests automatizados. Las llamadas reales quedan reservadas al piloto manual.

## ADR Alignment

| Parte del plan | Decisión fuente | Condición |
|----------------|-----------------|-----------|
| Contratos, LiteLLM, Gemini, fallback y context caching | ADR-001 | Permanece `proposed` hasta completar el piloto |
| Groq, FLAC/chunks, checkpoints y retiro de Whisper | ADR-007 | Propone reemplazar ADR-002 solo después de cumplir los umbrales |
| VPS sin GPU y rollback por imagen | ADR-006 + ADR-007 | Se vuelve definitivo únicamente tras el cutover |

Las tareas 1 a 7 construyen y miden el piloto. La tarea 8 contiene el gate de aceptación y es la única autorizada a retirar `openai-whisper`/PyTorch o a cambiar el estado de ADR-002.

## File Structure

### Create

- `back/apps/videos/services/ai/__init__.py` — exports públicos del nuevo núcleo.
- `back/apps/videos/services/ai/contracts.py` — modelos Pydantic de transcripción, clips, uso y resultados.
- `back/apps/videos/services/ai/errors.py` — errores tipados y clasificación retryable/no retryable.
- `back/apps/videos/services/ai/audio_preprocessor.py` — normalización FLAC y fragmentación con solapamiento.
- `back/apps/videos/services/ai/groq_transcription.py` — proveedor `TranscriptionProvider` con SDK de Groq.
- `back/apps/videos/services/ai/litellm_selection.py` — proveedor `ClipSelectionProvider` con Gemini y fallback OpenAI.
- `back/apps/videos/services/ai/pipeline_state.py` — lectura y actualización inmutable de checkpoints en `metadata.ai_pipeline`.
- `back/apps/videos/tests/services/__init__.py` — paquete de tests.
- `back/apps/videos/tests/services/test_ai_contracts.py` — validación de contratos.
- `back/apps/videos/tests/services/test_audio_preprocessor.py` — comandos FFmpeg y ventanas de chunks.
- `back/apps/videos/tests/services/test_groq_transcription.py` — normalización y errores del proveedor Groq.
- `back/apps/videos/tests/services/test_litellm_selection.py` — esquema, fallback y métricas LiteLLM.
- `back/apps/videos/tests/services/test_pipeline_state.py` — checkpoints sin pisar metadata previa.
- `back/apps/videos/management/__init__.py` y `back/apps/videos/management/commands/__init__.py` — paquetes Django.
- `back/apps/videos/management/commands/export_ai_pilot_metrics.py` — exportación CSV del piloto.
- `back/apps/videos/tests/test_ai_pilot_metrics.py` — prueba del exportador.
- `docs/ai-core-pilot-runbook.md` — ejecución, medición, aprobación y rollback del piloto.

### Modify

- `back/requirements/ia.txt` — agregar dependencias fijadas; retirar Whisper local solo al aprobar el piloto.
- `back/backend/settings/base.py:123-125` — configuración de proveedores, modelos, límites y feature flag.
- `back/.env.example:17-21` — variables documentadas sin secretos reales.
- `back/apps/videos/utils/ffmpeg_utils.py:8-48` — extracción FLAC, probe y chunks.
- `back/apps/videos/services/transcription_engine.py:1-119` — adaptador compatible y carga local verdaderamente diferida.
- `back/apps/videos/services/selection_engine.py:10-208` — adaptador compatible hacia LiteLLM.
- `back/apps/videos/tasks.py:425-525` — checkpoints, lock, persistencia por etapa y reintentos selectivos.
- `back/apps/videos/tests/test_ingestion_v2.py:39-86` — nuevo resultado detallado e idempotencia.
- `back/Dockerfile:26-33` — retirar instalación de PyTorch tras el piloto.
- `back/README.md:9-68` — arquitectura y variables vigentes.

---

## Task 1: Fijar configuración, dependencias y contratos del dominio

**Files:**

- Modify: `back/requirements/ia.txt`
- Modify: `back/backend/settings/base.py:123-125`
- Modify: `back/.env.example:17-21`
- Create: `back/apps/videos/services/ai/__init__.py`
- Create: `back/apps/videos/services/ai/contracts.py`
- Create: `back/apps/videos/services/ai/errors.py`
- Create: `back/apps/videos/tests/services/__init__.py`
- Create: `back/apps/videos/tests/services/test_ai_contracts.py`

- [ ] **Step 1: Agregar las dependencias fijadas sin retirar todavía Whisper local**

Agregar a la sección IA de `back/requirements/ia.txt`:

```text
litellm==1.100.1
groq==1.7.0
pydantic==2.13.5
```

Mantener temporalmente `openai-whisper` y la instalación de PyTorch del Dockerfile para poder comparar y revertir durante el piloto.

- [ ] **Step 2: Instalar el entorno de desarrollo actualizado**

Run:

```bash
cd back
python -m pip install -r requirements/dev.txt -r requirements/ia.txt
```

Expected: instalación exitosa con Python 3.11 y sin conflictos de resolución.

- [ ] **Step 3: Escribir tests fallidos de los contratos**

En `test_ai_contracts.py`, cubrir exactamente:

```python
from django.test import SimpleTestCase
from pydantic import ValidationError

from apps.videos.services.ai.contracts import (
    ClipCandidate,
    ClipSelection,
    ProviderUsage,
    TranscriptWord,
)


class AIContractsTests(SimpleTestCase):
    def test_word_rejects_end_before_start(self):
        with self.assertRaises(ValidationError):
            TranscriptWord(start=2.0, end=1.0, text="hola")

    def test_clip_rejects_score_outside_range(self):
        with self.assertRaises(ValidationError):
            ClipCandidate(
                start=0.0,
                end=20.0,
                title="Inicio",
                virality_score=101,
                reasoning="Buen gancho",
            )

    def test_selection_rejects_clip_past_source_duration(self):
        selection = ClipSelection(clips=[ClipCandidate(
            start=50.0,
            end=70.0,
            title="Cierre",
            virality_score=80,
            reasoning="Conclusión fuerte",
        )])
        with self.assertRaises(ValueError):
            selection.validate_for_source(duration_seconds=60.0, target_count=1)

    def test_usage_serializes_for_json_field(self):
        usage = ProviderUsage(
            provider="google",
            model="gemini-3.8-flash",
            latency_ms=120,
            attempts=1,
            prompt_tokens=100,
            completion_tokens=20,
            cached_input_tokens=40,
            estimated_cost_usd=0.001,
        )
        self.assertEqual(usage.model_dump(mode="json")["cached_input_tokens"], 40)
```

- [ ] **Step 4: Ejecutar el test y confirmar que falla por módulos inexistentes**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_ai_contracts
```

Expected: `ModuleNotFoundError` para `apps.videos.services.ai`.

- [ ] **Step 5: Implementar contratos estrictos y errores tipados**

`contracts.py` debe usar `ConfigDict(extra="forbid")`, límites `start >= 0`, `end > start`, `0 <= virality_score <= 100`, texto no vacío y los siguientes tipos públicos:

```python
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimedText(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def end_must_follow_start(self):
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("text cannot be blank")
        return self


class TranscriptWord(TimedText):
    pass


class TranscriptSegment(TimedText):
    pass


class Transcript(StrictModel):
    language: str | None
    text: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0)
    words: list[TranscriptWord]
    segments: list[TranscriptSegment]


class ClipCandidate(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field(min_length=1, max_length=120)
    virality_score: int = Field(ge=0, le=100)
    reasoning: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def end_must_follow_start(self):
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class ClipSelection(StrictModel):
    clips: list[ClipCandidate]

    def validate_for_source(
        self,
        duration_seconds: float,
        target_count: int,
    ) -> "ClipSelection":
        if not self.clips:
            raise ValueError("clip selection cannot be empty")
        if len(self.clips) > target_count:
            raise ValueError("clip selection exceeds target count")
        minimum_duration = 15.0 if duration_seconds >= 15.0 else 1.0
        for clip in self.clips:
            clip_duration = clip.end - clip.start
            if clip.end > duration_seconds:
                raise ValueError("clip exceeds source duration")
            if clip_duration < minimum_duration or clip_duration > 60.0:
                raise ValueError("clip duration is outside the accepted range")
        return self

class ProviderUsage(StrictModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    attempts: int = Field(ge=1)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    audio_seconds: float | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    request_id: str | None = None


class TranscriptionOutcome(StrictModel):
    transcript: Transcript
    usage: ProviderUsage


class ClipSelectionOutcome(StrictModel):
    selection: ClipSelection
    usage: ProviderUsage
```

`validate_for_source` debe rechazar clips fuera de la duración del video, duración de clip mayor a 60 segundos, duración menor a 15 segundos cuando la fuente dura 15 segundos o más, lista vacía y más clips que `target_count`. Para una fuente menor a 15 segundos debe aceptar un clip que cubra al menos 1 segundo y no exceda la fuente.

`errors.py` debe exponer:

```python
class AIProviderError(RuntimeError):
    def __init__(self, stage: str, provider: str, message: str, retryable: bool):
        super().__init__(f"{stage}:{provider}: {message}")
        self.stage = stage
        self.provider = provider
        self.retryable = retryable

class AIConfigurationError(AIProviderError):
    def __init__(self, stage: str, provider: str, message: str):
        super().__init__(stage, provider, message, retryable=False)

class AIResponseValidationError(AIProviderError):
    def __init__(self, stage: str, provider: str, message: str):
        super().__init__(stage, provider, message, retryable=True)
```

Los mensajes pueden identificar etapa, proveedor y tipo de error, pero no deben incluir payloads.

- [ ] **Step 6: Agregar configuración con defaults seguros**

En `base.py`:

```python
AI_CORE_V2_ENABLED = env.bool("AI_CORE_V2_ENABLED", default=False)
GROQ_API_KEY = env("GROQ_API_KEY", default=None)
AI_TRANSCRIPTION_MODEL = env(
    "AI_TRANSCRIPTION_MODEL",
    default="whisper-large-v3-turbo",
)
AI_TRANSCRIPTION_LANGUAGE = env("AI_TRANSCRIPTION_LANGUAGE", default="es")
AI_TRANSCRIPTION_MAX_UPLOAD_BYTES = env.int(
    "AI_TRANSCRIPTION_MAX_UPLOAD_BYTES",
    default=24_000_000,
)
AI_TRANSCRIPTION_CHUNK_SECONDS = env.int(
    "AI_TRANSCRIPTION_CHUNK_SECONDS",
    default=600,
)
AI_TRANSCRIPTION_CHUNK_OVERLAP_SECONDS = env.int(
    "AI_TRANSCRIPTION_CHUNK_OVERLAP_SECONDS",
    default=2,
)
AI_CLIP_SELECTION_MODEL = env(
    "AI_CLIP_SELECTION_MODEL",
    default="gemini/gemini-3.8-flash",
)
AI_CLIP_SELECTION_FALLBACK_MODEL = env(
    "AI_CLIP_SELECTION_FALLBACK_MODEL",
    default="openai/gpt-4o-mini",
)
AI_PROVIDER_TIMEOUT_SECONDS = env.int("AI_PROVIDER_TIMEOUT_SECONDS", default=120)
```

Agregar las mismas variables a `.env.example` con `AI_CORE_V2_ENABLED=False`, keys de ejemplo y comentarios que indiquen que `OPENAI_API_KEY` es opcional si no se desea fallback.

- [ ] **Step 7: Ejecutar contratos y check de Django**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_ai_contracts
python manage.py check
```

Expected: tests y check en verde.

- [ ] **Step 8: Commit**

```bash
git add back/requirements/ia.txt back/backend/settings/base.py back/.env.example back/apps/videos/services/ai back/apps/videos/tests/services
git commit -m "feat(ai): add provider contracts and configuration"
```

---

## Task 2: Normalizar y fragmentar audio sin cargarlo en memoria

**Files:**

- Modify: `back/apps/videos/utils/ffmpeg_utils.py:8-48`
- Create: `back/apps/videos/services/ai/audio_preprocessor.py`
- Create: `back/apps/videos/tests/services/test_audio_preprocessor.py`

- [ ] **Step 1: Escribir tests fallidos de ventanas y comandos FFmpeg**

Cubrir:

- Un audio de 599 segundos produce una ventana `(0, 599)`.
- Uno de 1.205 segundos con chunks de 600 y solapamiento de 2 produce inicios `0`, `598` y `1196`.
- El comando de extracción contiene `-vn`, `-ac 1`, `-ar 16000` y `-c:a flac`.
- Si el FLAC completo pesa menos de `24_000_000`, se devuelve un solo chunk con offset `0.0`.
- Si supera el límite, se eliminan el FLAC completo y todos los chunks al salir del context manager, incluso ante excepción.

El helper puro esperado:

```python
windows = build_chunk_windows(
    duration_seconds=1205.0,
    chunk_seconds=600,
    overlap_seconds=2,
)
self.assertEqual([window.start for window in windows], [0.0, 598.0, 1196.0])
```

- [ ] **Step 2: Ejecutar y confirmar el fallo**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_audio_preprocessor
```

Expected: imports de `audio_preprocessor` o métodos de `FFmpegManager` inexistentes.

- [ ] **Step 3: Añadir operaciones FFmpeg deterministas**

Agregar a `FFmpegManager` los métodos estáticos `probe_duration(input_path: str) -> float` y `extract_speech_audio(input_path: str, output_path: str, start_seconds: float | None = None, duration_seconds: float | None = None) -> str`.

`probe_duration` debe ejecutar `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1`. `extract_speech_audio` debe usar una lista de argumentos, `check=True`, `capture_output=True`, `text=True` y nunca `shell=True`.

- [ ] **Step 4: Implementar el context manager de audio temporal**

La interfaz pública será:

```python
@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_seconds: float
    duration_seconds: float
```

Exponer el context manager `prepare_audio_chunks(source_path: str, max_upload_bytes: int, chunk_seconds: int, overlap_seconds: int) -> Iterator[list[AudioChunk]]`.

Algoritmo exacto:

1. Crear `TemporaryDirectory(prefix="onecreator-ai-audio-")`.
2. Extraer un FLAC mono/16 kHz completo.
3. Si su tamaño es menor o igual al límite, devolverlo como único chunk.
4. Si supera el límite, obtener duración con ffprobe y crear ventanas de 600 segundos con 2 segundos de solapamiento.
5. Extraer cada ventana directamente desde el video fuente.
6. Verificar que cada chunk sea menor o igual al límite; si uno excede, lanzar `AIConfigurationError` con instrucción de bajar `AI_TRANSCRIPTION_CHUNK_SECONDS`, sin incluir rutas privadas.
7. Dejar que `TemporaryDirectory` limpie todos los archivos en éxito o error.

Validar `0 <= overlap_seconds < chunk_seconds` y valores positivos antes de ejecutar FFmpeg.

- [ ] **Step 5: Ejecutar tests del preprocesador y regresión FFmpeg**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_audio_preprocessor apps.videos.tests.test_ingestion_v2.IngestionV2Tests.test_ffmpeg_manager_proxy_generation
```

Expected: ambos módulos en verde.

- [ ] **Step 6: Commit**

```bash
git add back/apps/videos/utils/ffmpeg_utils.py back/apps/videos/services/ai/audio_preprocessor.py back/apps/videos/tests/services/test_audio_preprocessor.py
git commit -m "feat(ai): preprocess speech audio for remote transcription"
```

---

## Task 3: Implementar Groq Whisper Large V3 Turbo

**Files:**

- Create: `back/apps/videos/services/ai/groq_transcription.py`
- Modify: `back/apps/videos/services/ai/__init__.py`
- Create: `back/apps/videos/tests/services/test_groq_transcription.py`

- [ ] **Step 1: Escribir tests fallidos con cliente Groq falso**

Cubrir:

- Se envía `model="whisper-large-v3-turbo"`, `language="es"`, `temperature=0.0`, `response_format="verbose_json"` y `timestamp_granularities=["word", "segment"]`.
- Palabras y segmentos de un segundo chunk reciben su offset absoluto.
- Las palabras repetidas por el solapamiento no aparecen dos veces.
- Se calcula `estimated_cost_usd = duration_seconds / 3600 * 0.04`.
- Un 401/403 se traduce a `AIProviderError(retryable=False)`.
- Timeout, conexión, 429 y 5xx se traducen a `AIProviderError(retryable=True)`.
- La ausencia de `GROQ_API_KEY` produce `AIConfigurationError` antes de abrir archivos.
- Los temporales se limpian si el segundo chunk falla.

La aserción central de compatibilidad:

```python
self.assertEqual(
    outcome.transcript.words[1].model_dump(),
    {"start": 598.2, "end": 598.6, "text": "mundo"},
)
self.assertEqual(outcome.usage.model, "whisper-large-v3-turbo")
```

- [ ] **Step 2: Ejecutar y confirmar el fallo**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_groq_transcription
```

Expected: módulo/proveedor inexistente.

- [ ] **Step 3: Definir el protocolo e inyección de cliente**

Definir el protocolo `TranscriptionProvider` con `transcribe(source_path: str) -> TranscriptionOutcome`. `GroqTranscriptionProvider` debe exponer `COST_PER_AUDIO_HOUR_USD = 0.04`, recibir API key, modelo, idioma, timeout, límite de carga, tamaño de chunk, solapamiento y cliente opcional, además de implementar `from_settings()` y `transcribe()`.

El cliente debe configurarse con `Groq(api_key=self.api_key, timeout=float(self.timeout_seconds), max_retries=2)`. El SDK hace reintentos cortos de transporte; Celery conserva los reintentos de trabajo completos.

- [ ] **Step 4: Normalizar la respuesta y unir chunks**

Para cada chunk:

```python
response = self.client.audio.transcriptions.create(
    file=chunk.path,
    model=self.model,
    language=self.language,
    temperature=0.0,
    response_format="verbose_json",
    timestamp_granularities=["word", "segment"],
)
```

Convertir tanto objetos tipados del SDK como diccionarios de test. Aplicar `chunk.offset_seconds` a cada timestamp. Para deduplicar el solapamiento, descartar una palabra o segmento si su `absolute_end <= last_emitted_end + 0.01`. Construir `Transcript.text` uniendo segmentos normalizados, no serializando la respuesta cruda.

Medir latencia con `time.monotonic()`. Guardar `audio_seconds`, costo estimado, cantidad total de intentos lógicos `1` y `request_id` solo si el SDK lo expone sin leer el body dos veces.

- [ ] **Step 5: Ejecutar tests del proveedor**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_groq_transcription
```

Expected: todos los casos en verde y cero llamadas de red.

- [ ] **Step 6: Commit**

```bash
git add back/apps/videos/services/ai/groq_transcription.py back/apps/videos/services/ai/__init__.py back/apps/videos/tests/services/test_groq_transcription.py
git commit -m "feat(ai): add Groq Whisper transcription provider"
```

---

## Task 4: Implementar selección con LiteLLM, Gemini y fallback OpenAI

**Files:**

- Create: `back/apps/videos/services/ai/litellm_selection.py`
- Modify: `back/apps/videos/services/ai/__init__.py`
- Create: `back/apps/videos/tests/services/test_litellm_selection.py`

- [ ] **Step 1: Escribir tests fallidos del selector**

Cubrir:

- El Router primario usa `gemini/gemini-3.8-flash` y el secundario `openai/gpt-4o-mini`.
- La llamada contiene un `response_format` de tipo `json_schema`, generado desde `ClipSelection.model_json_schema()`.
- `fast` envía `reasoning_effort="low"`; `smart`, `"medium"`.
- El prompt contiene título, estilo, duración, target y transcripción, pero no rutas, keys ni metadatos ajenos.
- Una respuesta válida se transforma en `ClipSelectionOutcome`.
- Un fallo de red/rate limit en Gemini llega al fallback configurado por Router.
- Un JSON válido pero incompatible con el esquema dispara una llamada explícita al grupo fallback.
- Si ambos resultados son inválidos, se lanza `AIResponseValidationError` sin adjuntar respuestas crudas.
- `prompt_tokens`, `completion_tokens` y `cache_read_input_tokens` se normalizan a `ProviderUsage`.
- Sin `GEMINI_API_KEY`, la creación falla; sin `OPENAI_API_KEY`, funciona sin fallback.

Ejemplo de expectativa del esquema:

```python
kwargs = fake_router.completion.call_args.kwargs
self.assertEqual(kwargs["model"], "clip-selector-primary")
self.assertEqual(kwargs["response_format"]["type"], "json_schema")
self.assertTrue(kwargs["response_format"]["json_schema"]["strict"])
```

- [ ] **Step 2: Ejecutar y confirmar el fallo**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_litellm_selection
```

Expected: módulo/proveedor inexistente.

- [ ] **Step 3: Construir LiteLLM Router sin Proxy**

La fábrica debe generar grupos diferentes para preservar prioridad:

```python
model_list = [
    {
        "model_name": "clip-selector-primary",
        "litellm_params": {
            "model": settings.AI_CLIP_SELECTION_MODEL,
            "api_key": settings.GEMINI_API_KEY,
        },
    },
]

fallbacks = []
if settings.OPENAI_API_KEY:
    model_list.append({
        "model_name": "clip-selector-fallback",
        "litellm_params": {
            "model": settings.AI_CLIP_SELECTION_FALLBACK_MODEL,
            "api_key": settings.OPENAI_API_KEY,
        },
    })
    fallbacks = [{"clip-selector-primary": ["clip-selector-fallback"]}]

router = Router(
    model_list=model_list,
    fallbacks=fallbacks,
    num_retries=1,
    timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
    allowed_fails=1,
    cooldown_time=30,
)
```

No iniciar servidor, base propia, Redis adicional ni dashboard de LiteLLM.

- [ ] **Step 4: Implementar prompt, salida estructurada y validación local**

Definir el protocolo `ClipSelectionProvider` con el método `select(transcript_text: str, project_title: str, editing_style: str, duration_seconds: float, target_count: int, intelligence_level: str) -> ClipSelectionOutcome`.

La llamada LiteLLM debe usar `temperature=0.2`, timeout configurado, el esquema estricto y un mensaje de sistema corto. Para favorecer el caché implícito en reanálisis del mismo video, mantener estable el mensaje de sistema y colocar la transcripción antes de título/estilo/target variables dentro del mensaje de usuario. Parsear primero `message.content` con `ClipSelection.model_validate_json`, luego ejecutar `validate_for_source`.

No truncar por caracteres. Antes de llamar, usar `litellm.token_counter(model=settings.AI_CLIP_SELECTION_MODEL, text=prompt)` y rechazar de forma tipada si prompt más reserva de salida supera 1.000.000 tokens; el límite deja margen frente al contexto publicado de 1.048.576.

En error de validación del primario, llamar una vez a `clip-selector-fallback` si existe. En error de red/proveedor, permitir que Router aplique su fallback. Normalizar el modelo real desde `response.model` y el proveedor desde el prefijo/modelo resultante.

- [ ] **Step 5: Registrar caché implícito sin activarlo explícitamente**

El extractor de uso debe aceptar estos nombres, en orden:

```python
cached_input_tokens = (
    getattr(usage, "cache_read_input_tokens", None)
    or getattr(usage, "cached_tokens", None)
    or getattr(usage, "total_cached_tokens", None)
    or 0
)
```

Calcular `estimated_cost_usd` con `litellm.completion_cost(completion_response=response)` y guardar `None` si LiteLLM no conoce el precio del modelo; no inventar un costo cero. No enviar `cached_content`, TTL, cache key ni parámetros de caché. La decisión de caché explícito queda en el gate del piloto.

- [ ] **Step 6: Ejecutar tests del selector**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_litellm_selection
```

Expected: tests en verde y cero llamadas reales.

- [ ] **Step 7: Commit**

```bash
git add back/apps/videos/services/ai/litellm_selection.py back/apps/videos/services/ai/__init__.py back/apps/videos/tests/services/test_litellm_selection.py
git commit -m "feat(ai): select clips through LiteLLM with fallback"
```

---

## Task 5: Conectar los proveedores sin romper contratos existentes

**Files:**

- Modify: `back/apps/videos/services/transcription_engine.py:1-119`
- Modify: `back/apps/videos/services/selection_engine.py:10-208`
- Modify: `back/apps/videos/tests/services/test_groq_transcription.py`
- Modify: `back/apps/videos/tests/services/test_litellm_selection.py`
- Modify: `back/tests/unit/test_viral_subtitles.py`

- [ ] **Step 1: Escribir tests fallidos de compatibilidad**

Añadir casos que confirmen:

```python
engine = TranscriptionEngine(provider=fake_provider)
self.assertEqual(
    engine.transcribe("source.mp4", word_timestamps=True),
    [{"start": 0.0, "end": 0.4, "text": "hola"}],
)

clips = SelectionEngine.select_viral_clips(
    transcription_data={"full_text": "texto", "duration": 30.0},
    project_title="Demo",
    duration=30.0,
    intelligence_level="fast",
    provider=fake_provider,
)
self.assertEqual(clips[0]["virality_score"], 90)
```

También verificar que `group_words` conserva exactamente su comportamiento actual.

- [ ] **Step 2: Ejecutar y confirmar el fallo de las nuevas firmas**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services tests.unit.test_viral_subtitles
```

Expected: fallos por argumentos `provider` o métodos detallados inexistentes.

- [ ] **Step 3: Convertir `TranscriptionEngine` en adaptador**

Mantener las firmas públicas `transcribe(self, audio_path: str, word_timestamps: bool = True) -> list[dict]` y `group_words(words: list[dict], max_words: int = 3, emoji_map: dict[str, str] | None = None) -> list[dict]`. Agregar `transcribe_detailed(self, audio_path: str) -> TranscriptionOutcome`.

Cuando `AI_CORE_V2_ENABLED=True`, usar `GroqTranscriptionProvider.from_settings()`. Cuando sea falso, ejecutar el código local actual y envolverlo en `TranscriptionOutcome`. Mover `import whisper` e `import torch` dentro de la creación del proveedor local para que el proceso web no los cargue al importar el módulo.

`transcribe(audio_path, word_timestamps=True)` devuelve `outcome.transcript.words` como dicts. Con `False`, devuelve segmentos. Así se mantiene el contrato actual de subtítulos.

- [ ] **Step 4: Convertir `SelectionEngine` en adaptador**

Agregar `select_viral_clips_detailed(transcription_data: dict, project_title: str, editing_style: str = "dynamic", duration: float | None = None, intelligence_level: str = "fast", provider: ClipSelectionProvider | None = None) -> ClipSelectionOutcome`.

`select_viral_clips` debe llamar al método detallado y devolver solo una lista de dicts. Aceptar `provider=None` en ambos métodos para inyección en tests.

Con feature flag activo, usar `LiteLLMClipSelectionProvider.from_settings()`. Con flag inactivo, conservar las estrategias OpenAI/Gemini existentes y envolver el resultado en un outcome con métricas `provider="legacy"`. No cambiar el cálculo actual de cantidad objetivo.

- [ ] **Step 5: Ejecutar toda la capa de servicios**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services tests.unit.test_viral_subtitles tests.unit.test_subtitles_stress
```

Expected: verde; imports del backend no inicializan Whisper ni clientes de red.

- [ ] **Step 6: Commit**

```bash
git add back/apps/videos/services/transcription_engine.py back/apps/videos/services/selection_engine.py back/apps/videos/tests/services back/tests/unit
git commit -m "refactor(ai): preserve engine contracts with provider adapters"
```

---

## Task 6: Hacer la ingesta recuperable e idempotente por etapa

**Files:**

- Create: `back/apps/videos/services/ai/pipeline_state.py`
- Create: `back/apps/videos/tests/services/test_pipeline_state.py`
- Modify: `back/backend/settings/base.py:135-139`
- Modify: `back/apps/videos/tasks.py:425-525`
- Modify: `back/apps/videos/tests/test_ingestion_v2.py:39-86`

- [ ] **Step 1: Escribir tests fallidos del estado de pipeline**

El JSON objetivo dentro de `VideoProject.metadata` es:

```json
{
  "ai_pipeline": {
    "transcription": {
      "status": "completed",
      "provider": "groq",
      "model": "whisper-large-v3-turbo",
      "latency_ms": 820,
      "attempts": 1,
      "audio_seconds": 1800.0,
      "estimated_cost_usd": 0.02,
      "completed_at": "2026-09-13T12:00:00Z"
    },
    "selection": {
      "status": "completed",
      "provider": "google",
      "model": "gemini-3.8-flash",
      "latency_ms": 950,
      "attempts": 1,
      "prompt_tokens": 6000,
      "completion_tokens": 1200,
      "cached_input_tokens": 0,
      "estimated_cost_usd": 0.009,
      "completed_at": "2026-09-13T12:00:02Z"
    }
  }
}
```

Probar que `mark_stage` preserva claves previas de `metadata`, solo acepta `running`, `completed` o `failed`, agrega timestamp UTC y que `is_stage_complete` no toma `failed` como completado.

- [ ] **Step 2: Ejecutar y confirmar el fallo**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services.test_pipeline_state
```

Expected: módulo inexistente.

- [ ] **Step 3: Implementar helpers inmutables de checkpoint**

Exponer `mark_stage(metadata: dict, stage: str, status: Literal["running", "completed", "failed"], details: dict | None = None) -> dict`, `is_stage_complete(metadata: dict, stage: str) -> bool` y `stage_details(metadata: dict, stage: str) -> dict`.

Usar `copy.deepcopy`; no mutar el diccionario que Django ya tiene en memoria. Para fallos, persistir únicamente `error_type`, `retryable`, `attempts` y timestamp, nunca el texto completo de la excepción.

- [ ] **Step 4: Configurar el cache distribuido que respaldará el lock**

Cuando exista `REDIS_URL`, `base.py` debe configurar:

```python
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}
```

Sin `REDIS_URL`, conservar LocMem para desarrollo y tests. Esto hace que `cache.add` sea realmente distribuido entre workers en producción.

- [ ] **Step 5: Extraer la ejecución síncrona y agregar tests de checkpoints**

En `tasks.py`, separar la lógica síncrona en `run_initial_ingestion(project_id)` y conservar `process_initial_ingestion(self, project_id)` como wrapper Celery con `bind=True` y `max_retries=2`.

Tests a agregar en `test_ingestion_v2.py`:

1. Camino feliz con `transcribe_detailed` y `select_viral_clips_detailed` simulados.
2. La transcripción se guarda antes de invocar selección.
3. Si selección falla y se vuelve a ejecutar `run_initial_ingestion`, Groq no se llama de nuevo.
4. Si `original_r2_key` o `proxy_r2_key` ya existen, no se repiten sus uploads.
5. Si selección y creación de clips completaron, una reejecución no crea duplicados.
6. Dos tareas simultáneas para el mismo proyecto: la segunda devuelve `"Already running"` por `cache.add`.
7. Error de configuración marca proyecto `FAILED` y no solicita retry.
8. Error retryable mantiene `INGESTING`, registra etapa fallida y llama `self.retry(countdown=60)`; el siguiente retry usa `120`.

- [ ] **Step 6: Ejecutar el test de ingesta y confirmar los fallos**

Run:

```bash
cd back
python manage.py test apps.videos.tests.test_ingestion_v2
```

Expected: los nuevos casos fallan antes del refactor de la tarea.

- [ ] **Step 7: Implementar orden y persistencia por etapa**

`run_initial_ingestion` debe ejecutar este orden:

1. Obtener/descargar fuente.
2. Subir original solo si `original_r2_key` está vacío.
3. Generar/subir proxy solo si `proxy_r2_key` está vacío; mantener su fallo como no fatal.
4. Leer duración/resolución solo si no están en `metadata`.
5. Si `transcription` no está completada, marcar `running`, llamar `transcribe_detailed` y guardar de inmediato `transcript_data`, `metadata.full_text` y checkpoint `completed`.
6. Si ya está completada, reconstruir el input desde `transcript_data`/`metadata.full_text` sin llamar a Groq.
7. Si `selection` no está completada, marcar `running`, llamar `select_viral_clips_detailed` y, dentro de una sola transacción, crear clips, guardar `ai_rationale_log` y marcar `completed`.
8. Si `selection` ya está completada, usar los clips persistidos y no llamar al LLM.
9. Conservar la bifurcación actual entre aprobación y render.

Guardar en `ai_rationale_log` solo `suggestions_count`, `raw_ai_output` validado y `provider/model`; no guardar prompt ni respuesta sin validar.

- [ ] **Step 8: Implementar wrapper Celery y clasificación de reintentos**

Antes de correr:

```python
lock_id = f"lock_initial_ingestion_{project_id}"
if not cache.add(lock_id, "locked", timeout=3600):
    return "Already running"
```

Eliminar el lock en `finally`. Para `AIProviderError`, reintentar solo si `retryable=True`; usar backoff `60 * (2 ** self.request.retries)`. Si se agotaron reintentos o el error es no retryable, marcar el proyecto `FAILED` y propagar la excepción. Para un retry pendiente, dejar el proyecto `INGESTING`.

- [ ] **Step 9: Ejecutar servicios e ingesta juntos**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services apps.videos.tests.test_ingestion_v2
```

Expected: verde; el test inducido demuestra que una falla de selección no repite la transcripción.

- [ ] **Step 10: Commit**

```bash
git add back/apps/videos/services/ai/pipeline_state.py back/apps/videos/tests/services/test_pipeline_state.py back/backend/settings/base.py back/apps/videos/tasks.py back/apps/videos/tests/test_ingestion_v2.py
git commit -m "feat(ai): checkpoint ingestion stages and retry safely"
```

---

## Task 7: Exponer métricas de piloto sin agregar infraestructura

**Files:**

- Create: `back/apps/videos/management/__init__.py`
- Create: `back/apps/videos/management/commands/__init__.py`
- Create: `back/apps/videos/management/commands/export_ai_pilot_metrics.py`
- Create: `back/apps/videos/tests/test_ai_pilot_metrics.py`
- Create: `docs/ai-core-pilot-runbook.md`

- [ ] **Step 1: Escribir test fallido del comando**

Crear un proyecto con ambos checkpoints y ejecutar:

```python
output = StringIO()
call_command("export_ai_pilot_metrics", stdout=output)
self.assertIn("project_id,status,transcription_model", output.getvalue())
self.assertIn("whisper-large-v3-turbo", output.getvalue())
self.assertIn("gemini-3.8-flash", output.getvalue())
```

Cubrir también un proyecto sin métricas para que exporte celdas vacías sin fallar.

- [ ] **Step 2: Ejecutar y confirmar que el comando no existe**

Run:

```bash
cd back
python manage.py test apps.videos.tests.test_ai_pilot_metrics
```

Expected: `CommandError` por comando desconocido.

- [ ] **Step 3: Implementar exportación CSV a stdout**

El comando debe aceptar `--since YYYY-MM-DD` y emitir estas columnas en orden:

```text
project_id,status,created_at,duration_seconds,transcription_provider,transcription_model,transcription_latency_ms,audio_seconds,transcription_cost_usd,selection_provider,selection_model,selection_latency_ms,prompt_tokens,completion_tokens,cached_input_tokens,selection_cost_usd,suggestions_count
```

Usar `csv.writer(self.stdout)`, `iterator(chunk_size=500)` y seleccionar solo `id`, `status`, `created_at`, `metadata`, `ai_rationale_log`. No incluir título, transcript ni razonamientos.

- [ ] **Step 4: Documentar el piloto de 20 videos**

`docs/ai-core-pilot-runbook.md` debe fijar:

- 20 videos en español rioplatense: entrevistas limpias, ruido, música, voces superpuestas y duraciones corta/media/larga.
- Datos anonimizados o con autorización para enviarse a Groq y Google.
- Comparación contra Whisper base local en un entorno de evaluación, no en la VPS productiva.
- WER <= 15%.
- Error mediano de timestamps <= 300 ms.
- Al menos 70% de sugerencias aceptadas o editadas mínimamente.
- Costo combinado < USD 0,04 por video equivalente de 30 minutos con tarifas del piloto.
- Cero repetición de una etapa completada en pruebas de fallo inducido.
- Comando de exportación y una tabla manual por video para WER, error temporal y aceptación.
- Rollback: poner `AI_CORE_V2_ENABLED=False` mientras la imagen todavía conserva Whisper; tras retirarlo, redeplegar la imagen etiquetada previa.

- [ ] **Step 5: Agregar el gate de context caching**

El runbook debe indicar que el caché explícito solo entra en una propuesta separada si, durante el piloto:

1. La misma transcripción se vuelve a analizar al menos dos veces dentro de una ventana útil.
2. El input repetido alcanza el mínimo documentado de 4.096 tokens de Gemini 3.8 Flash.
3. El ahorro medido compensa crear/gestionar el caché.

Hasta entonces, usar únicamente `cached_input_tokens` para observar caché implícito.

- [ ] **Step 6: Ejecutar test y una exportación local**

Run:

```bash
cd back
python manage.py test apps.videos.tests.test_ai_pilot_metrics
python manage.py export_ai_pilot_metrics
```

Expected: test verde y CSV con header aunque la base no tenga proyectos.

- [ ] **Step 7: Commit**

```bash
git add back/apps/videos/management back/apps/videos/tests/test_ai_pilot_metrics.py docs/ai-core-pilot-runbook.md
git commit -m "feat(ai): add pilot metrics export and runbook"
```

---

## Task 8: Verificación integral, despliegue gradual y retiro de Whisper local

**Files:**

- Modify: `back/README.md:9-68`
- Modify after pilot approval: `back/requirements/ia.txt`
- Modify after pilot approval: `back/Dockerfile:26-33`
- Modify after pilot approval: `back/apps/videos/services/transcription_engine.py`

- [ ] **Step 1: Ejecutar la suite focalizada antes del piloto**

Run:

```bash
cd back
python manage.py test apps.videos.tests.services apps.videos.tests.test_ingestion_v2 apps.videos.tests.test_api apps.core.tests.test_critical_integrity tests.unit.test_viral_subtitles tests.unit.test_subtitles_stress
python manage.py check --deploy
```

Expected: suite en verde. Revisar por separado warnings de despliegue Django ya existentes; ninguno nuevo puede provenir de la integración IA.

- [ ] **Step 2: Verificar la imagen con ambos caminos durante el piloto**

Run:

```bash
docker build --target worker -t onecreator-worker:ai-pilot ./back
docker run --rm onecreator-worker:ai-pilot python -c "import groq, litellm, whisper, torch; print('ai-pilot-imports-ok')"
```

Expected: `ai-pilot-imports-ok`.

- [ ] **Step 3: Desplegar primero con el feature flag apagado**

Configurar keys, modelos y límites, dejando:

```text
AI_CORE_V2_ENABLED=False
```

Verificar health checks, worker Celery, Redis cache distribuido y un trabajo legacy. No imprimir los valores de las keys en comandos, logs o tickets.

- [ ] **Step 4: Ejecutar el piloto controlado**

En el entorno de piloto, cambiar a `AI_CORE_V2_ENABLED=True`, procesar los 20 videos del runbook, exportar métricas y completar evaluación humana. Inducir un fallo del selector después de una transcripción exitosa y comprobar en logs/métricas que Groq no vuelve a facturarse.

- [ ] **Step 5: Aplicar el gate de aprobación**

Continuar solo si se cumplen conjuntamente los cinco umbrales del runbook. Si falla calidad de audio, repetir manualmente los casos afectados con `whisper-large-v3` como experimento separado; no convertirlo en fallback automático porque duplicaría costo en fallos ambiguos.

- [ ] **Step 6: Activar producción y observar siete días o 100 trabajos**

Usar el hito que ocurra último. Durante la ventana, controlar tasa de error por etapa, p50/p95 de latencia, costo por minuto, modelo efectivo de fallback y `cached_input_tokens`. Mantener disponible la imagen `onecreator-worker:ai-pilot` para rollback.

- [ ] **Step 7: Retirar inferencia local solo después de aprobar la ventana**

Eliminar de `back/requirements/ia.txt`:

```text
openai-whisper
```

Eliminar del Dockerfile:

```dockerfile
RUN pip install --no-cache-dir torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
```

Eliminar el proveedor local y el branch legacy de `transcription_engine.py`. Cambiar el default de `AI_CORE_V2_ENABLED` a `True`, conservando temporalmente la variable como kill switch hacia un error controlado/cola pausada, no hacia Whisper local.

- [ ] **Step 8: Actualizar README**

Documentar Groq, Gemini, LiteLLM SDK, variables obligatorias, fallback OpenAI opcional, ausencia de LiteLLM Proxy y procedimiento de rollback por imagen.

- [ ] **Step 9: Construir y verificar la imagen liviana final**

Run:

```bash
docker build --target worker -t onecreator-worker:ai-v2 ./back
docker run --rm onecreator-worker:ai-v2 python -c "import importlib.util; assert importlib.util.find_spec('whisper') is None; assert importlib.util.find_spec('torch') is None; import groq, litellm; print('ai-v2-imports-ok')"
docker image inspect onecreator-worker:ai-v2 --format '{{.Size}}'
```

Expected: `ai-v2-imports-ok`; tamaño registrado y menor que el de `onecreator-worker:ai-pilot`.

- [ ] **Step 10: Ejecutar la suite final**

Run:

```bash
cd back
python manage.py test apps.videos.tests apps.core.tests.test_critical_integrity tests.unit.test_viral_subtitles tests.unit.test_subtitles_stress
python manage.py check --deploy
```

Expected: todos los tests en verde; ningún test importa Whisper/PyTorch.

- [ ] **Step 11: Commit de documentación y corte**

```bash
git add back/README.md back/requirements/ia.txt back/Dockerfile back/apps/videos/services/transcription_engine.py
git commit -m "chore(ai): remove local Whisper after successful pilot"
```

---

## Acceptance Checklist

- [ ] `TranscriptionEngine.transcribe` conserva el formato `{start, end, text}` consumido por subtítulos.
- [ ] `SelectionEngine.select_viral_clips` conserva la lista de sugerencias consumida por Paper Edit.
- [ ] Groq recibe FLAC mono a 16 kHz y devuelve timestamps de palabra y segmento.
- [ ] Gemini recibe salida estructurada y todos los clips se validan contra la duración real.
- [ ] OpenAI se usa solo como fallback del selector, nunca como ruta aleatoria del balanceador.
- [ ] Una selección fallida no repite una transcripción completada.
- [ ] Logs y exportación excluyen audio, transcripción, prompts y secretos.
- [ ] Métricas registran proveedor/modelo efectivo, latencia, tokens, caché implícito y costo estimado.
- [ ] No existe migración de base de datos para esta entrega.
- [ ] LiteLLM Proxy y caché explícito quedan fuera del despliegue inicial.
- [ ] Whisper/PyTorch solo se retiran después del piloto y la ventana de observación.

## Reference Notes

- Versiones fijadas al 13 de septiembre de 2026: LiteLLM `1.100.1`, Groq SDK `1.7.0`, Pydantic `2.13.5`.
- Groq documenta `whisper-large-v3-turbo`, timestamps `word`/`segment`, carga directa y USD 0,04 por hora.
- LiteLLM se usa por su interfaz común, Router, retry/fallback y normalización de uso; la escala actual no justifica operar el Proxy.
- El identificador de modelo y los precios deben revisarse antes de cada actualización de dependencias o renovación trimestral de presupuesto.
- Google: [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), [context caching](https://ai.google.dev/gemini-api/docs/caching) y [precios](https://ai.google.dev/gemini-api/docs/pricing).
- Groq: [Speech to Text](https://console.groq.com/docs/speech-to-text) y [SDK Python](https://pypi.org/project/groq/).
- LiteLLM: [SDK y Proxy](https://docs.litellm.ai/) y [paquete estable](https://pypi.org/project/litellm/).
