"""CORE-05 (#12): an upload must really be a video the pipeline can process."""

import logging
import subprocess
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Transaction, Wallet
from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.services.media_validation import InvalidUploadError, validate_upload

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _ffmpeg(*args):
    from conftest import _ffmpeg_exe

    subprocess.run([_ffmpeg_exe(), "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="module")
def media_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("upload_validation")


@pytest.fixture(scope="module")
def audio_only_mp4(media_dir):
    path = media_dir / "audio.mp4"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(path))
    return path


@pytest.fixture(scope="module")
def tiny_mp4(media_dir):
    path = media_dir / "tiny.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )
    return path


def upload_of(path, name="video.mp4"):
    return SimpleUploadedFile(name, path.read_bytes() if hasattr(path, "read_bytes") else open(path, "rb").read())


def code_of(upload):
    with pytest.raises(InvalidUploadError) as exc:
        validate_upload(upload)
    return exc.value.code


class TestValidateUpload:
    def test_a_real_video_is_described(self, synthetic_video_path):
        info = validate_upload(upload_of(synthetic_video_path))
        assert info.mime == "video/mp4"
        assert (info.width, info.height) == (640, 360)
        assert info.duration_s == pytest.approx(2.0, abs=0.1)

    def test_a_large_upload_kept_on_disk_works_too(self, synthetic_video_path):
        data = open(synthetic_video_path, "rb").read()
        upload = TemporaryUploadedFile("video.mp4", "video/mp4", len(data), None)
        upload.write(data)
        upload.seek(0)
        try:
            assert validate_upload(upload).width == 640
        finally:
            upload.close()

    def test_the_file_is_left_ready_to_be_saved(self, synthetic_video_path):
        upload = upload_of(synthetic_video_path)
        validate_upload(upload)
        assert upload.read(8) == open(synthetic_video_path, "rb").read(8)

    def test_bytes_that_are_not_a_video_are_rejected_whatever_the_name(self):
        assert code_of(SimpleUploadedFile("video.mp4", b"\x00\x01garbage" * 100)) == "not_a_video"
        assert code_of(SimpleUploadedFile("video.mp4", b"<html><script>x</script></html>")) == "not_a_video"
        assert code_of(SimpleUploadedFile("video.mp4", b"")) == "not_a_video"

    def test_an_mp4_without_video_is_rejected(self, audio_only_mp4):
        assert code_of(upload_of(audio_only_mp4)) in {"not_a_video", "no_video_stream"}

    def test_a_truncated_video_is_unreadable(self, synthetic_video_path):
        head = open(synthetic_video_path, "rb").read()[:4096]
        assert code_of(SimpleUploadedFile("video.mp4", head)) in {"unreadable", "no_video_stream", "no_duration"}

    def test_system_limits(self, synthetic_video_path, tiny_mp4, settings):
        settings.AI_UPLOAD_MAX_LONG_SIDE_PX = 320
        assert code_of(upload_of(synthetic_video_path)) == "too_large"
        settings.AI_UPLOAD_MAX_LONG_SIDE_PX = 4096
        assert code_of(upload_of(tiny_mp4)) == "too_small"  # 160x120 < 240
        settings.AI_UPLOAD_MAX_DURATION_SECONDS = 1
        with pytest.raises(InvalidUploadError) as exc:
            validate_upload(upload_of(synthetic_video_path))
        assert exc.value.code == "too_long"
        assert "1 s" in exc.value.message

    def test_a_hung_probe_is_unreadable(self, synthetic_video_path):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 60)):
            assert code_of(upload_of(synthetic_video_path)) == "unreadable"

    def test_logs_carry_no_file_name_or_probe_output(self, caplog):
        caplog.set_level(logging.DEBUG, logger="apps.videos.services.media_validation")
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ffprobe", stderr="secreto-cliente")):
            with patch("magic.from_buffer", return_value="video/mp4"):
                code_of(SimpleUploadedFile("secreto-cliente.mp4", b"x" * 64))
        assert caplog.records
        assert "secreto" not in caplog.text


@pytest.mark.django_db
class TestCreateEndpoint:
    @pytest.fixture
    def api(self, django_user_model, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        user = django_user_model.objects.create_user(username="up", email="up@test.com", password="pw")
        if not user.workspaces.exists():
            Workspace.objects.create(name="W", owner=user).members.add(user)
        for workspace in user.workspaces.all():
            wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
            Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("100.00"))
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def post(self, api, upload):
        with patch("apps.videos.views.process_initial_ingestion.delay") as ingest:
            response = api.post(reverse("project-list"), {"title": "Subido", "source_file": upload}, format="multipart")
        return response, ingest

    def test_a_fake_video_is_rejected_before_saving_or_charging(self, api):
        response, ingest = self.post(api, SimpleUploadedFile("video.mp4", b"no soy un video" * 50))
        assert response.status_code == 400
        assert "no es un video válido" in str(response.data["source_file"][0])
        assert not VideoProject.objects.exists()
        assert not Transaction.objects.exists()
        ingest.assert_not_called()

    def test_a_real_video_is_priced_with_the_probed_duration(self, api, synthetic_video_path):
        with patch("moviepy.VideoFileClip", side_effect=AssertionError("decoded twice")):
            response, ingest = self.post(api, upload_of(synthetic_video_path))
        assert response.status_code == 201, response.content[:300]
        project = VideoProject.objects.get(pk=response.data["id"])
        assert project.metadata["source_media"] == {
            "duration_s": pytest.approx(2.0, abs=0.1), "width": 640, "height": 360, "mime": "video/mp4",
        }
        txn = Transaction.objects.get(pk=project.metadata["reservation_id"])
        assert txn.amount == Decimal("1.00")  # 2 s x 0.5 tokens, no plan
        ingest.assert_called_once()
