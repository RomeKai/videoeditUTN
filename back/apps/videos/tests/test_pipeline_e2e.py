"""
CORE-06 · Test E2E del pipeline de ingesta.

upload (API) -> reserva de tokens -> ingesta Celery (eager) -> proxy FFmpeg real
-> probe MoviePy real -> transcripción (mock) -> selección IA (mock) -> AWAITING_APPROVAL

A diferencia de test_ingestion_pipeline.py (que mockea storage, proxy y MoviePy para
probar las etapas aisladas), acá solo se simula lo que necesita red o modelos:
los proveedores de IA y el lock de Redis. FFmpeg, MoviePy, storage local, wallet,
máquina de estados y base de datos son reales.
"""
from decimal import Decimal
from unittest.mock import patch

import os

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.payments.models import Transaction
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    TranscriptionResult,
    ViralClip,
    WordTimestamp,
)
from apps.videos.services.ai.errors import AIAuthenticationError
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tests.fakes import FakeRedis

pytestmark = pytest.mark.django_db

PROJECTS_URL = "/api/v1/projects/"
TRANSCRIBE = "apps.videos.services.transcription_engine.TranscriptionEngine.transcribe_detailed"
SELECT = "apps.videos.services.selection_engine.SelectionEngine.select_viral_clips_detailed"
REDIS_CLIENT = "apps.videos.services.ai.project_lock.get_redis_client"
MODERATION = "apps.core.security.AI_Security_Shield.check_content_safety"

WORDS = [
    WordTimestamp(text="Hola", start=0.0, end=0.5),
    WordTimestamp(text="mundo", start=0.5, end=1.0),
    WordTimestamp(text="viral", start=1.0, end=2.0),
]
CLIP = ViralClip(start=0.0, end=1.5, title="Momento viral", virality_score=87, reasoning="Hook fuerte")


@pytest.fixture(autouse=True)
def pipeline_env(settings):
    """
    Ruta V2 de IA y Redis en memoria, sin depender del .env local.
    Celery ya corre eager por backend.settings.ci: no tocar app.conf acá, porque
    escribirlo fija el valor para toda la sesión y pisa los override_settings
    de otros tests.
    """
    settings.AI_CORE_V2_ENABLED = True
    # Ingestion moderates before selection (AICORE-13): the verdict is mocked
    # like every other AI provider here (no OpenAI call in CI).
    with patch(REDIS_CLIENT, return_value=FakeRedis()), patch(MODERATION, return_value=None):
        yield


@pytest.fixture
def fake_ai():
    transcription = AIExecutionResult(
        data=TranscriptionResult(full_text="Hola mundo viral", words=WORDS, duration=2.0),
        usage=ProviderUsage(provider="groq", model="whisper-large-v3-turbo"),
    )
    selection = AIExecutionResult(
        data=ClipSelectionResult(clips=[CLIP]),
        usage=ProviderUsage(provider="litellm", model="gemini"),
    )
    with patch(TRANSCRIBE, return_value=transcription) as transcribe, \
            patch(SELECT, return_value=selection) as select:
        yield transcribe, select


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def video_upload(synthetic_video_path):
    with open(synthetic_video_path, "rb") as f:
        return SimpleUploadedFile("demo.mp4", f.read(), content_type="video/mp4")


def test_upload_to_awaiting_approval(api, wallet_factory, video_upload, fake_ai):
    wallet = wallet_factory("100.00")
    transcribe, select = fake_ai

    res = api.post(PROJECTS_URL, {"title": "Demo E2E", "source_file": video_upload}, format="multipart")

    assert res.status_code == 201, res.data
    project = VideoProject.objects.get(pk=res.data["id"])

    # Estado final del pipeline: todas las etapas completas
    assert project.status == VideoProject.Status.AWAITING_APPROVAL
    assert project.pipeline_stage == PipelineStage.CLIPS_SELECTED
    assert set(project.pipeline_stage_status.values()) == {"completed"}

    # Storage local: original registrado y proxy generado de verdad por FFmpeg
    assert project.original_r2_key
    # Solo prueba que el proxy se generó y registró; que el archivo siga existiendo
    # lo cubre test_local_proxy_file_survives_ingestion (xfail por bug conocido).
    assert project.proxy_r2_key, "el proxy FFmpeg falló (la etapa lo traga sin cortar la ingesta)"

    # Probe real con MoviePy sobre el video sintético (2s, 640x360)
    assert project.metadata["duration"] == pytest.approx(2.0, abs=0.1)
    assert project.metadata["resolution"] == [640, 360]

    # Transcripción y selección persistidas
    assert project.metadata["full_text"] == "Hola mundo viral"
    assert [seg["text"] for seg in project.transcript_data] == ["Hola", "mundo", "viral"]
    transcribe.assert_called_once()
    select.assert_called_once()

    clip = project.clips.get()
    assert clip.title == CLIP.title
    assert clip.start_time == pytest.approx(CLIP.start)
    assert clip.end_time == pytest.approx(CLIP.end)
    assert clip.virality_score == CLIP.virality_score
    assert clip.status == VideoClip.Status.DRAFT

    # Pago: se reservaron tokens por 2s de video (2 * 0.5 = 1.00) y nada más.
    # El monto usa el multiplicador base porque la view pasa aspect_ratio como
    # resolución (ver test_create_prices_with_a_real_resolution).
    txn = Transaction.objects.get(wallet=wallet)
    assert txn.status == Transaction.Status.RESERVED
    assert txn.amount == Decimal("1.00")
    wallet.refresh_from_db()
    assert wallet.available_balance == Decimal("99.00")
    assert wallet.reserved_balance == Decimal("1.00")


def test_upload_without_funds_is_rejected_and_nothing_runs(api, wallet_factory, video_upload, fake_ai):
    wallet_factory("0.00")
    transcribe, select = fake_ai

    res = api.post(PROJECTS_URL, {"title": "Sin saldo", "source_file": video_upload}, format="multipart")

    assert res.status_code == 402
    assert not VideoProject.objects.exists()
    transcribe.assert_not_called()
    select.assert_not_called()


# --- Bugs conocidos: xfail estricto, se ponen en verde cuando se arreglen ------------

def _upload(api, video_upload, title="Demo"):
    res = api.post(PROJECTS_URL, {"title": title, "source_file": video_upload}, format="multipart")
    assert res.status_code == 201, res.data
    return VideoProject.objects.get(pk=res.data["id"])


@pytest.mark.xfail(
    strict=True,
    reason="Bug storage local: con USE_R2=False upload_video devuelve el mismo path "
           "(no contiene 'temp') y _generate_proxy lo borra en su finally, "
           "así que proxy_r2_key apunta a un archivo inexistente.",
)
def test_local_proxy_file_survives_ingestion(api, wallet_factory, video_upload, fake_ai):
    wallet_factory("100.00")
    project = _upload(api, video_upload)

    assert project.proxy_r2_key
    assert os.path.exists(project.proxy_r2_key)


@pytest.mark.xfail(
    strict=True,
    reason="Bug: ningún código productivo llama a rollback_reservation; si la "
           "ingesta falla, los tokens quedan reservados para siempre.",
)
def test_failed_ingestion_releases_the_reservation(api, wallet_factory, video_upload, fake_ai, settings):
    # La falla de la tarea no debe propagarse al request de upload
    settings.CELERY_TASK_EAGER_PROPAGATES = False
    wallet = wallet_factory("100.00")
    _, select = fake_ai
    select.side_effect = AIAuthenticationError("credenciales inválidas")

    project = _upload(api, video_upload, title="Falla IA")

    project.refresh_from_db()
    assert project.status == VideoProject.Status.FAILED
    txn = Transaction.objects.get(wallet=wallet)
    assert txn.status == Transaction.Status.FAILED
    wallet.refresh_from_db()
    assert wallet.available_balance == Decimal("100.00")
    assert wallet.reserved_balance == Decimal("0")


@pytest.mark.xfail(
    strict=True,
    reason="Bug: la view le pasa project.aspect_ratio ('9:16') como resolution al "
           "PricingEngine, que no la reconoce y cae al multiplicador base sin "
           "aplicar el límite de resolución del plan.",
)
def test_create_prices_with_a_real_resolution(api, wallet_factory, workspace, free_plan,
                                              video_upload, fake_ai):
    from apps.payments.services.pricing_engine import PricingEngine

    free_plan.save()
    workspace.subscription_plan = free_plan
    workspace.save()
    wallet_factory("100.00")

    with patch.object(PricingEngine, "calculate_render_cost",
                      wraps=PricingEngine.calculate_render_cost) as spy:
        _upload(api, video_upload, title="Free 9:16")

    resolution = spy.call_args.kwargs["resolution"]
    assert resolution in PricingEngine.RESOLUTION_MULTIPLIERS
