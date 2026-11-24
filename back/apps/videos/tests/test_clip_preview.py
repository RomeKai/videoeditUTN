"""
PTE-03 · Vista previa rápida de un clip (360p, gratis, temporal).

- El render real usa el video sintético de 2 s (FFmpeg + MoviePy), sin IA ni red.
- La billetera nunca se toca.
- Las vistas previas vencen y se borran (fila y archivo).
"""
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payments.models import Transaction
from apps.videos.models import ClipPreview, VideoClip, VideoProject
from apps.videos.services import clip_preview as svc

pytestmark = pytest.mark.django_db

RENDER_TASK = "apps.videos.tasks.render_clip_preview_task.delay"


def url(clip_id):
    return f"/api/v1/clips/{clip_id}/preview/"


@pytest.fixture
def project(user, workspace, synthetic_video_path):
    with open(synthetic_video_path, "rb") as f:
        source = SimpleUploadedFile("demo.mp4", f.read(), content_type="video/mp4")
    return VideoProject.objects.create(
        workspace=workspace,
        uploaded_by=user,
        title="Preview",
        source_file=source,
        aspect_ratio="9:16",
        transcript_data=[
            {"text": "Hola", "start": 0.0, "end": 0.5},
            {"text": "mundo", "start": 0.5, "end": 1.0},
        ],
    )


@pytest.fixture
def clip(project):
    return VideoClip.objects.create(project=project, title="Clip", start_time=0.0, end_time=1.5)


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def make_preview(clip, status=ClipPreview.Status.READY, expires_in=3600, storage_key=""):
    return ClipPreview.objects.create(
        clip=clip,
        status=status,
        storage_key=storage_key,
        expires_at=timezone.now() + timedelta(seconds=expires_in),
    )


# ---------------------------------------------------------------------------
# Dimensiones
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("aspect, expected", [("9:16", (204, 360)), ("1:1", (360, 360)), ("16:9", (640, 360))])
def test_preview_keeps_the_aspect_ratio_at_360p(aspect, expected):
    assert svc.preview_dimensions(aspect) == expected


# ---------------------------------------------------------------------------
# Pedido de vista previa
# ---------------------------------------------------------------------------

def test_request_queues_a_render_after_commit(clip, user, django_capture_on_commit_callbacks):
    with patch(RENDER_TASK) as delay, django_capture_on_commit_callbacks(execute=True):
        preview, created = svc.request_preview(clip, user)

    assert created is True
    assert preview.status == ClipPreview.Status.PENDING
    delay.assert_called_once_with(str(preview.id))


def test_a_preview_in_progress_is_reused(clip, user):
    with patch(RENDER_TASK):
        first, _ = svc.request_preview(clip, user)
        second, created = svc.request_preview(clip, user)

    assert created is False
    assert second.pk == first.pk
    assert ClipPreview.objects.filter(clip=clip).count() == 1


def test_the_workspace_limit_is_enforced(clip, user, settings):
    settings.PREVIEW_MAX_ACTIVE_PER_WORKSPACE = 1
    other = VideoClip.objects.create(project=clip.project, title="Otro", start_time=0, end_time=1)
    with patch(RENDER_TASK):
        svc.request_preview(clip, user)
        with pytest.raises(svc.PreviewLimitExceeded):
            svc.request_preview(other, user)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def test_render_produces_a_360p_file_that_expires_after_the_ttl(clip, settings):
    from moviepy import VideoFileClip

    settings.PREVIEW_TTL_SECONDS = 600
    preview = make_preview(clip, status=ClipPreview.Status.PENDING)

    result = svc.render_preview(preview.id)

    assert result.status == ClipPreview.Status.READY, result.error_code
    assert os.path.isfile(result.storage_key)
    assert os.path.realpath(result.storage_key).startswith(os.path.realpath(str(settings.MEDIA_ROOT)))
    assert f"previews{os.sep}{clip.id}" in result.storage_key
    with VideoFileClip(result.storage_key) as rendered:
        assert tuple(rendered.size) == (204, 360)
        assert rendered.duration == pytest.approx(1.5, abs=0.15)
    assert result.expires_at - result.ready_at == timedelta(seconds=600)


def test_render_never_charges_or_calls_ai(clip, wallet_factory):
    wallet = wallet_factory("100.00")
    preview = make_preview(clip, status=ClipPreview.Status.PENDING)

    with patch("apps.videos.services.transcription_engine.TranscriptionEngine.transcribe_detailed") as ai:
        svc.render_preview(preview.id)

    ai.assert_not_called()
    assert not Transaction.objects.filter(wallet=wallet).exists()
    wallet.refresh_from_db()
    assert wallet.available_balance == Decimal("100.00")


def test_a_failed_render_is_stored_and_never_raises(clip):
    preview = make_preview(clip, status=ClipPreview.Status.PENDING)

    with patch("apps.videos.services.render_engine.RenderEngine.compose_clip",
               side_effect=OSError("boom")):
        result = svc.render_preview(preview.id)

    assert result.status == ClipPreview.Status.FAILED
    assert result.error_code == "OSError"
    assert result.storage_key == ""


def test_a_preview_is_rendered_only_once(clip):
    preview = make_preview(clip, status=ClipPreview.Status.PENDING)
    with patch("apps.videos.services.render_engine.RenderEngine.compose_clip",
               side_effect=OSError("boom")) as compose:
        svc.render_preview(preview.id)
        assert svc.render_preview(preview.id) is None
    compose.assert_called_once()


# ---------------------------------------------------------------------------
# Vencimiento y limpieza
# ---------------------------------------------------------------------------

def test_cleanup_deletes_expired_previews_and_their_files(clip, settings, tmp_path):
    old_file = tmp_path / "old.mp4"
    old_file.write_bytes(b"x")
    expired = make_preview(clip, expires_in=-1, storage_key=str(old_file))
    alive = make_preview(clip, expires_in=3600)

    from apps.videos.tasks import cleanup_expired_previews

    assert cleanup_expired_previews() == 1
    assert not old_file.exists()
    assert not ClipPreview.objects.filter(pk=expired.pk).exists()
    assert ClipPreview.objects.filter(pk=alive.pk).exists()


def test_a_file_that_cannot_be_deleted_keeps_its_row_for_the_next_run(clip):
    expired = make_preview(clip, expires_in=-1, storage_key="previews/x.mp4")
    with patch("apps.videos.services.storage_service.CloudflareR2Manager.delete_object",
               side_effect=OSError("r2 down")):
        assert svc.cleanup_expired_previews_now() == 0
    assert ClipPreview.objects.filter(pk=expired.pk).exists()


def test_an_expired_preview_has_no_url(clip, tmp_path):
    f = tmp_path / "p.mp4"
    f.write_bytes(b"x")
    assert svc.preview_url(make_preview(clip, expires_in=3600, storage_key=str(f)))
    assert svc.preview_url(make_preview(clip, expires_in=-1, storage_key=str(f))) is None


def test_cleanup_runs_on_a_schedule():
    from django.conf import settings

    from backend.celery import app

    entry = settings.CELERY_BEAT_SCHEDULE["cleanup-expired-previews"]
    assert entry["task"] in app.tasks
    assert entry["schedule"] <= 3600


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_post_queues_a_free_preview(api, clip, wallet_factory):
    wallet = wallet_factory("100.00")
    with patch(RENDER_TASK):
        res = api.post(url(clip.id))
        again = api.post(url(clip.id))

    assert res.status_code == 202, res.data
    assert res.data["status"] == "pending"
    assert res.data["cost_tokens"] == 0
    assert res.data["url"] is None
    assert again.status_code == 200
    assert again.data["preview_id"] == res.data["preview_id"]
    assert not Transaction.objects.filter(wallet=wallet).exists()


def test_post_then_get_returns_the_rendered_preview(api, clip, django_capture_on_commit_callbacks):
    # Celery corre eager en CI: el render ocurre al confirmar la transacción.
    with django_capture_on_commit_callbacks(execute=True):
        res = api.post(url(clip.id))
    assert res.status_code == 202

    got = api.get(url(clip.id))

    assert got.status_code == 200
    assert got.data["status"] == "ready"
    assert got.data["url"].startswith("/media/previews/")
    assert got.data["simplified"] == {"height": 360, "face_tracking": False, "silence_removal": False}


def test_get_without_a_preview_is_404(api, clip):
    assert api.get(url(clip.id)).status_code == 404


def test_another_workspace_cannot_see_or_create_previews(clip, django_user_model):
    stranger = django_user_model.objects.create_user(username="otro", email="o@t.com", password="x")
    client = APIClient()
    client.force_authenticate(user=stranger)

    assert client.post(url(clip.id)).status_code == 404
    assert client.get(url(clip.id)).status_code == 404
    assert not ClipPreview.objects.exists()


def test_authentication_is_required(clip):
    assert APIClient().post(url(clip.id)).status_code == 401


def test_a_project_without_source_video_is_409(api, workspace, user):
    project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="URL",
                                          video_url="https://example.invalid/v")
    clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=1)
    assert api.post(url(clip.id)).status_code == 409


def test_post_over_the_workspace_limit_is_429(api, clip, settings):
    settings.PREVIEW_MAX_ACTIVE_PER_WORKSPACE = 1
    other = VideoClip.objects.create(project=clip.project, title="Otro", start_time=0, end_time=1)
    with patch(RENDER_TASK):
        assert api.post(url(clip.id)).status_code == 202
        assert api.post(url(other.id)).status_code == 429


@pytest.mark.parametrize("resolution, target, expected", [
    ([1920, 1080], (204, 360), 360),    # 16:9 -> 9:16: alcanza con el alto
    ([1080, 1920], (360, 360), 640),    # 9:16 -> 1:1: el ancho manda
    ([640, 360], (640, 360), 360),      # mismo formato
    ([320, 180], (640, 360), 180),      # nunca más que la fuente
    (None, (204, 360), 360),            # sin metadata: el alto del preview
])
def test_the_source_is_decoded_just_big_enough(resolution, target, expected):
    project = VideoProject(metadata={"resolution": resolution} if resolution else {})
    assert svc._decode_height(project, *target) == expected
