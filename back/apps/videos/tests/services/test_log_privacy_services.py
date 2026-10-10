"""Log privacy for service-layer loggers (see test_log_privacy for the contract)."""
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.tests.fakes import CapturedTaskFailures
from apps.videos.tasks import render_clip_task

from apps.videos.tests.test_log_privacy import (
    SECRET,
    TEXT_MARKER,
    TITLE_MARKER,
    LogPrivacyAssertions,
    capture_logs,
)


User = get_user_model()


class RenderEngineLogTests(LogPrivacyAssertions, CapturedTaskFailures, TestCase):
    def setUp(self):
        user = User.objects.create_user(username='re', email='re@test.com', password='pw')
        workspace = Workspace.objects.create(name='RE', owner=user)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title=TITLE_MARKER,
            source_file=SimpleUploadedFile('src.mp4', b'x'),
        )
        self.clip = VideoClip.objects.create(
            project=project, title=TITLE_MARKER, start_time=0, end_time=5
        )

    def test_clip_render_engine_failure_does_not_log_message_or_traceback(self):
        tmp = tempfile.mkdtemp()
        with patch(
            "apps.videos.services.render_engine.VideoFileClip",
            side_effect=RuntimeError(f"{SECRET} {TEXT_MARKER}"),
        ), override_settings(MEDIA_ROOT=tmp):
            with capture_logs() as logs:
                result = render_clip_task.apply(args=[str(self.clip.id)])

        self.assertTrue(result.failed())
        self.assert_clean(logs)


class ServiceLogTests(LogPrivacyAssertions, SimpleTestCase):
    def test_seo_engine_failure_and_success_do_not_log_user_content(self):
        from apps.videos.services.seo_engine import SEOOptimizationService

        service = SEOOptimizationService()
        with patch("litellm.completion", side_effect=RuntimeError(f"{SECRET} {TEXT_MARKER}")):
            with capture_logs() as logs:
                with self.assertRaises(RuntimeError):
                    service.generate_metadata(transcript_text="t", target_niche="n")

        self.assert_clean(logs)

    def test_seo_engine_success_does_not_log_generated_title(self):
        from apps.videos.services.seo_engine import SEOOptimizationService

        raw = (
            '{"viral_title": "%s", "description_body": "d", "hashtags": ["a"], '
            '"recommended_publish_hour_utc": 12}' % TITLE_MARKER
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
        )
        service = SEOOptimizationService()
        with patch("litellm.completion", return_value=response):
            with capture_logs() as logs:
                service.generate_metadata(transcript_text="t", target_niche="n")

        self.assert_clean(logs)

    def test_selection_clip_validation_does_not_log_clip_titles(self):
        from apps.videos.services.ai.contracts import ViralClip
        from apps.videos.services.ai.litellm_selection import LiteLLMSelectionProvider

        clips = [
            ViralClip(start=50.0, end=60.0, title=TITLE_MARKER, virality_score=1, reasoning="r"),
            ViralClip(start=1.0, end=70.0, title=TITLE_MARKER, virality_score=1, reasoning="r"),
        ]
        with capture_logs() as logs:
            LiteLLMSelectionProvider._validate_clips(clips, 30.0)

        self.assert_clean(logs)

    def test_subtitle_engine_failure_does_not_log_segment_text(self):
        from apps.videos.services.subtitle_engine import (
            StyleConfig,
            SubtitleEngine,
            SubtitleSegment,
        )

        engine = SubtitleEngine(style_config=StyleConfig(font_path="x.ttf"))
        segment = SubtitleSegment(start=0.0, end=1.0, text=TEXT_MARKER)
        with patch(
            "apps.videos.services.subtitle_engine.TextClip",
            side_effect=RuntimeError(f"{SECRET} {TEXT_MARKER}"),
        ):
            with capture_logs() as logs:
                engine.generate_subtitle_clips([segment], 1080, 1920)

        self.assert_clean(logs)

    def test_storage_delete_failure_does_not_log_provider_message(self):
        from apps.videos.services.storage_service import CloudflareR2Manager

        client = MagicMock()
        client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "X", "Message": SECRET}}, "DeleteObject"
        )
        with patch("apps.videos.services.storage_service._is_local", return_value=False), \
                patch("apps.videos.services.storage_service._get_r2_client", return_value=client):
            with capture_logs() as logs:
                with self.assertRaises(ClientError):
                    CloudflareR2Manager.delete_object("videos/1/2/a.mp4")

        self.assert_clean(logs)

    def test_audio_preprocessor_cleanup_failure_does_not_log_message(self):
        from apps.videos.services.ai.audio_preprocessor import AudioPreprocessor

        ffmpeg = MagicMock()
        ffmpeg.get_media_duration.return_value = 1.0
        with tempfile.NamedTemporaryFile(suffix=".mp4") as src:
            pre = AudioPreprocessor(ffmpeg_manager=ffmpeg)
            with patch("os.remove", side_effect=OSError(SECRET)):
                with capture_logs() as logs:
                    with pre.process(src.name):
                        pass

        self.assert_clean(logs)

    def test_face_tracker_errors_do_not_log_message(self):
        from apps.videos.services.face_tracker import FaceTracker

        tracker = FaceTracker.__new__(FaceTracker)
        tracker.detector = MagicMock()
        clip = MagicMock(size=(100, 100), duration=1.0)
        clip.get_frame.side_effect = RuntimeError(SECRET)
        with capture_logs() as logs:
            tracker.generate_path(clip)

        self.assert_clean(logs)

    def test_saliency_error_does_not_log_message(self):
        from apps.videos.services.saliency_tracker import GameplaySaliencyDetector

        class BrokenClip:
            w = 100

            @property
            def size(self):
                raise RuntimeError(SECRET)

        detector = GameplaySaliencyDetector()
        with capture_logs() as logs:
            detector.find_best_x_center(BrokenClip())

        self.assert_clean(logs)
