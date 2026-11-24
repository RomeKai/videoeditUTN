"""
CORE-04 · Optimización de performance del worker.

- El proxy web corre en su propia tarea, en paralelo con la transcripción.
- El proxy usa el preset 'ultrafast' y tiene timeout propio.
- Los procesos del worker se reciclan (max_tasks_per_child / max_memory_per_child).
- Whisper local se carga una vez por proceso (caché + precarga al iniciar el worker),
  en GPU cuando hay, con fp16 solo en CUDA.
- Cada etapa del pipeline loguea su duración.

Ningún test carga un modelo real ni lanza FFmpeg: se simulan en el límite.
"""
import logging
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.videos.models import VideoProject
from apps.videos.services import transcription_engine as te
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import ingestion
from apps.videos.utils.ffmpeg_utils import FFmpegManager


# ---------------------------------------------------------------------------
# Proxy en paralelo
# ---------------------------------------------------------------------------

@pytest.fixture
def project(db, user, workspace):
    return VideoProject.objects.create(
        workspace=workspace,
        uploaded_by=user,
        title="Perf",
        source_file=SimpleUploadedFile("perf.mp4", b"fake-video"),
    )


@pytest.fixture
def prepare_source_mocks():
    with patch.object(ingestion, "_probe_video", return_value=(10.0, [1920, 1080])), \
            patch("apps.videos.services.storage_service.CloudflareR2Manager.upload_video",
                  return_value="original-key"), \
            patch.object(ingestion.generate_proxy_task, "delay") as delay, \
            patch.object(ingestion, "_generate_proxy") as inline_proxy:
        yield delay, inline_proxy


def test_prepare_source_dispatches_the_proxy_instead_of_waiting_for_it(project, prepare_source_mocks):
    delay, inline_proxy = prepare_source_mocks

    ingestion._stage_prepare_source(project.id)

    delay.assert_called_once_with(str(project.id))
    inline_proxy.assert_not_called()
    project.refresh_from_db()
    assert project.pipeline_stage == PipelineStage.AUDIO_EXTRACTED
    assert project.metadata["duration"] == 10.0


def test_prepare_source_skips_the_proxy_when_it_already_exists(project, prepare_source_mocks):
    delay, _ = prepare_source_mocks
    VideoProject.objects.filter(pk=project.id).update(proxy_r2_key="already-there")

    ingestion._stage_prepare_source(project.id)

    delay.assert_not_called()


def test_proxy_task_builds_the_proxy(project):
    with patch.object(ingestion, "_generate_proxy") as build:
        ingestion.generate_proxy_task(str(project.id))

    build.assert_called_once()
    built_project, source_path, user_id = build.call_args.args
    assert built_project.pk == project.pk
    assert source_path == project.source_file.path
    assert user_id == str(project.uploaded_by.id)


def test_proxy_task_is_idempotent_and_tolerates_missing_projects(project):
    VideoProject.objects.filter(pk=project.id).update(proxy_r2_key="already-there")
    with patch.object(ingestion, "_generate_proxy") as build:
        ingestion.generate_proxy_task(str(project.id))
        ingestion.generate_proxy_task("00000000-0000-0000-0000-000000000000")
    build.assert_not_called()


def test_each_proxy_run_uses_its_own_temp_file(project):
    paths = []

    def fake_encode(source, output):
        paths.append(output)
        return output

    with patch.object(FFmpegManager, "generate_web_proxy", side_effect=fake_encode), \
            patch("apps.videos.services.storage_service.CloudflareR2Manager.upload_video",
                  return_value="proxy-key"):
        ingestion._generate_proxy(project, project.source_file.path, "u1")
        ingestion._generate_proxy(project, project.source_file.path, "u1")

    assert len(set(paths)) == 2
    assert all(str(project.id) in p for p in paths)
    project.refresh_from_db()
    assert project.proxy_r2_key == "proxy-key"


def test_proxy_logs_its_duration(project, caplog):
    with patch.object(FFmpegManager, "generate_web_proxy"), \
            patch("apps.videos.services.storage_service.CloudflareR2Manager.upload_video",
                  return_value="proxy-key"), \
            caplog.at_level(logging.INFO, logger=ingestion.logger.name):
        ingestion._generate_proxy(project, project.source_file.path, "u1")

    record = next(r for r in caplog.records if "pipeline.proxy_completed" in r.getMessage())
    assert record.duration_s >= 0


# ---------------------------------------------------------------------------
# FFmpeg: preset y timeout del proxy
# ---------------------------------------------------------------------------

def test_proxy_uses_the_ultrafast_preset_and_a_timeout(tmp_path):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    with patch("subprocess.run") as run:
        FFmpegManager.generate_web_proxy(str(source), str(tmp_path / "out.mp4"))

    command = run.call_args.args[0]
    assert command[command.index("-preset") + 1] == "ultrafast"
    assert run.call_args.kwargs["timeout"] == FFmpegManager.PROXY_TIMEOUT_SECONDS


def test_a_hung_proxy_encode_becomes_a_runtime_error(tmp_path):
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
        with pytest.raises(RuntimeError, match="timed out"):
            FFmpegManager.generate_web_proxy(str(source), str(tmp_path / "out.mp4"))


# ---------------------------------------------------------------------------
# Reciclaje de procesos del worker
# ---------------------------------------------------------------------------

def test_worker_children_are_recycled_after_50_tasks_by_default():
    from django.conf import settings

    from backend.celery import app

    assert settings.CELERY_WORKER_MAX_TASKS_PER_CHILD == 50
    assert app.conf.worker_max_tasks_per_child == 50


def test_memory_recycling_is_off_unless_configured():
    from django.conf import settings

    assert settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD is None


# ---------------------------------------------------------------------------
# Métricas de tiempo por etapa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fails", [False, True])
def test_stages_log_their_duration(fails, caplog):
    with patch.object(ingestion, "set_stage_status"), \
            caplog.at_level(logging.INFO, logger=ingestion.logger.name):
        try:
            with ingestion._running_stage("p1", PipelineStage.TRANSCRIBED, attempt=1):
                if fails:
                    raise ValueError("boom")
        except ValueError:
            pass

    event = "pipeline.stage_failed" if fails else "pipeline.stage_completed"
    record = next(r for r in caplog.records if event in r.getMessage())
    assert record.duration_s >= 0
    assert "duration_s=" in record.getMessage()


# ---------------------------------------------------------------------------
# Whisper local: dispositivo, caché por proceso y precarga
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_whisper(monkeypatch):
    """Reemplaza whisper y torch por módulos falsos; limpia la caché por proceso."""
    model = MagicMock(name="whisper_model")
    model.transcribe.return_value = {"text": "hola", "segments": []}
    whisper_mod = types.ModuleType("whisper")
    whisper_mod.load_model = MagicMock(return_value=model)
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = types.SimpleNamespace(is_available=MagicMock(return_value=False))
    monkeypatch.setitem(sys.modules, "whisper", whisper_mod)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setattr(te, "_LOCAL_MODELS", {})
    return types.SimpleNamespace(whisper=whisper_mod, torch=torch_mod, model=model)


def test_the_local_model_is_loaded_once_per_process(fake_whisper, settings):
    settings.TRANSCRIPTION_DEVICE = "auto"

    first = te.TranscriptionEngine(model_size="base")
    second = te.TranscriptionEngine(model_size="base")
    assert first.model is second.model

    fake_whisper.whisper.load_model.assert_called_once_with("base", device="cpu")


def test_cuda_is_used_with_fp16_when_a_gpu_is_available(fake_whisper, settings):
    settings.TRANSCRIPTION_DEVICE = "auto"
    fake_whisper.torch.cuda.is_available.return_value = True

    te.TranscriptionEngine(model_size="base")._transcribe_local_raw("a.wav", word_timestamps=True)

    fake_whisper.whisper.load_model.assert_called_once_with("base", device="cuda")
    assert fake_whisper.model.transcribe.call_args.kwargs["fp16"] is True


def test_cpu_inference_never_requests_fp16(fake_whisper, settings):
    settings.TRANSCRIPTION_DEVICE = "cpu"

    te.TranscriptionEngine(model_size="base")._transcribe_local_raw("a.wav", word_timestamps=False)

    assert fake_whisper.model.transcribe.call_args.kwargs["fp16"] is False


def test_forced_cuda_without_a_gpu_falls_back_to_cpu(fake_whisper, settings):
    settings.TRANSCRIPTION_DEVICE = "cuda"
    assert te.resolve_local_device() == "cpu"


def test_an_unknown_device_is_a_configuration_error(settings):
    settings.TRANSCRIPTION_DEVICE = "tpu"
    with pytest.raises(ImproperlyConfigured):
        te.resolve_local_device()


def test_preload_loads_the_model_when_the_local_backend_is_active(fake_whisper, settings):
    settings.AI_CORE_V2_ENABLED = False
    settings.WHISPER_PRELOAD = True
    settings.WHISPER_LOCAL_MODEL_SIZE = "small"
    settings.TRANSCRIPTION_DEVICE = "cpu"

    assert te.preload_local_model() is True
    # Una tarea posterior reutiliza el modelo precargado, sin volver a cargarlo
    te.TranscriptionEngine(model_size="small").model
    fake_whisper.whisper.load_model.assert_called_once_with("small", device="cpu")


@pytest.mark.parametrize("v2, preload", [(True, True), (False, False)])
def test_preload_skips_when_groq_is_active_or_it_is_disabled(fake_whisper, settings, v2, preload):
    settings.AI_CORE_V2_ENABLED = v2
    settings.TRANSCRIPTION_BACKEND = "groq"
    settings.WHISPER_PRELOAD = preload

    assert te.preload_local_model() is False
    fake_whisper.whisper.load_model.assert_not_called()


def test_a_failed_preload_never_breaks_the_worker(fake_whisper, settings):
    settings.AI_CORE_V2_ENABLED = False
    settings.WHISPER_PRELOAD = True
    settings.TRANSCRIPTION_DEVICE = "cpu"
    fake_whisper.whisper.load_model.side_effect = OSError("disk full")

    assert te.preload_local_model() is False


def test_the_worker_preloads_on_process_start():
    from celery.signals import worker_process_init

    from backend import celery as celery_module

    receivers = [ref() for _, ref in worker_process_init.receivers]
    assert celery_module.preload_worker_models in receivers

    with patch.object(te, "preload_local_model") as preload:
        celery_module.preload_worker_models()
    preload.assert_called_once()


def test_a_failed_dispatch_builds_the_proxy_inline(project, prepare_source_mocks):
    delay, inline_proxy = prepare_source_mocks
    delay.side_effect = ConnectionError("broker down")

    ingestion._stage_prepare_source(project.id)

    inline_proxy.assert_called_once()
    project.refresh_from_db()
    assert project.pipeline_stage == PipelineStage.AUDIO_EXTRACTED
