"""
Log privacy: no user text, provider message or traceback may reach the logs.

Every test raises an exception whose message carries ``SECRET_LOG_X`` (and seeds
user-facing fields with markers) and asserts the marker is absent from every
rendered form of every captured record: message, args, exc_text, extras and the
output of a Formatter (which renders ``exc_info`` tracebacks).
"""
import logging
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase, override_settings

from apps.integrations.ayrshare_api import AyrshareAPIError
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.services.ai.errors import NonRetryableAIError
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import (
    ClipRenderTask,
    _generate_proxy,
    _record_pipeline_failure,
    _resume_render_dispatch,
    process_initial_ingestion,
    process_video_seo,
    render_clip_task,
    render_video_segments,
    upload_to_social_network,
)
from apps.videos.tests.fakes import CapturedTaskFailures, FakeRedis
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness

User = get_user_model()

SECRET = "SECRET_LOG_X"
TITLE_MARKER = "SECRET_TITLE_X"
TEXT_MARKER = "SECRET_CAPTION_X"
MARKERS = (SECRET, TITLE_MARKER, TEXT_MARKER)


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.rendered = []
        self._formatter = logging.Formatter("%(name)s %(levelname)s %(message)s")

    def emit(self, record):
        # Celery's own trace logger prints the repr and traceback of any unhandled
        # task exception and of task return values. That is framework output, not
        # something application code controls, so it is out of scope here.
        if record.name.startswith("celery."):
            return
        formatted = self._formatter.format(record)  # renders exc_info into the output
        parts = [
            formatted,
            record.getMessage(),
            str(record.args),
            record.exc_text or "",
            str(record.__dict__),
        ]
        self.rendered.append("\n".join(parts))


@contextmanager
def capture_logs():
    """Attaches a collector to the root logger and to every ``apps`` logger."""
    collector = _Collector()
    attached = [logging.getLogger()]
    attached += [
        logging.getLogger(name)
        for name in list(logging.root.manager.loggerDict)
        if name.startswith("apps")
    ]
    previous = [(lg, lg.level) for lg in attached]
    for lg in attached:
        lg.addHandler(collector)
        lg.setLevel(logging.DEBUG)
    try:
        yield collector
    finally:
        for lg, level in previous:
            lg.removeHandler(collector)
            lg.setLevel(level)


class LogPrivacyAssertions:
    def assert_clean(self, collector, markers=MARKERS):
        self.assertTrue(collector.rendered, "no log record captured: vacuous test")
        for rendered in collector.rendered:
            for marker in markers:
                self.assertNotIn(marker, rendered)


# --- Pipeline / tasks -------------------------------------------------------


class PipelineFailureLogTests(LogPrivacyAssertions, IngestionHarness, TestCase):
    def make_secret_project(self, **kwargs):
        project = self.make_project(**kwargs)
        VideoProject.objects.filter(pk=project.pk).update(title=TITLE_MARKER)
        return project

    def test_stage_failure_does_not_log_message_or_traceback(self):
        project = self.make_secret_project()
        self.mock_transcribe.side_effect = NonRetryableAIError(
            f"provider echoed: {SECRET}", provider="groq"
        )

        with capture_logs() as logs:
            result = self.run_task(project)

        self.assertTrue(result.failed())
        self.assert_clean(logs)

    def test_download_does_not_log_the_user_url(self):
        project = self.make_secret_project(video_url=f"https://example.test/{TEXT_MARKER}")
        VideoProject.objects.filter(pk=project.pk).update(source_file="")

        with patch("apps.videos.tasks.download_from_youtube", side_effect=RuntimeError(SECRET)):
            with capture_logs() as logs:
                result = self.run_task(project)

        self.assertTrue(result.failed())
        self.assert_clean(logs)

    def test_skipped_pipeline_does_not_log_exception_text(self):
        from apps.videos.services.ai.pipeline_state import PipelineConflictError

        project = self.make_secret_project()
        with patch(
            "apps.videos.tasks._run_ingestion", side_effect=PipelineConflictError(SECRET)
        ):
            with capture_logs() as logs:
                self.run_task(project)

        self.assert_clean(logs)

    def test_failure_recording_error_does_not_log_traceback(self):
        project = self.make_secret_project()
        with patch("apps.videos.tasks.record_error", side_effect=DatabaseError(SECRET)):
            with capture_logs() as logs:
                _record_pipeline_failure(str(project.id), RuntimeError(SECRET), 1, final=False)

        self.assert_clean(logs)

    def test_status_write_failure_does_not_log_traceback(self):
        project = self.make_secret_project()
        self.mock_transcribe.side_effect = NonRetryableAIError(SECRET, provider="groq")
        calls = {"n": 0}

        def flaky_status(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise DatabaseError(SECRET)

        with patch("apps.videos.tasks.set_stage_status", side_effect=flaky_status):
            with capture_logs() as logs:
                self.run_task(project)

        self.assert_clean(logs)

    def test_proxy_failure_does_not_log_traceback(self):
        project = self.make_secret_project()
        self.mock_proxy.side_effect = RuntimeError(SECRET)

        with capture_logs() as logs:
            _generate_proxy(project, "/tmp/does-not-matter.mp4", "u1")

        self.assert_clean(logs)

    def test_render_redispatch_failure_does_not_log_traceback(self):
        project = self.make_secret_project()
        VideoClip.objects.create(project=project, title=TITLE_MARKER, start_time=0, end_time=5)
        self.mock_render.side_effect = RuntimeError(SECRET)

        with capture_logs() as logs:
            with self.assertRaises(RuntimeError):
                _resume_render_dispatch(project.id)

        self.assert_clean(logs)


class RenderTaskLogTests(LogPrivacyAssertions, CapturedTaskFailures, TestCase):
    def setUp(self):
        user = User.objects.create_user(username="lp", email="lp@test.com", password="pw")
        self.user = user
        self.workspace = Workspace.objects.create(
            name="LP", owner=user, ayrshare_profile_key="profile-key"
        )
        self.project = VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=user,
            title=TITLE_MARKER,
            status=VideoProject.Status.RENDERING,
            pipeline_stage=PipelineStage.RENDER_DISPATCHED,
            original_r2_key="videos/1/original/source.mp4",
            approved_segments=[{"start": 0, "end": 2, "text": TEXT_MARKER}],
            add_subtitles=False,
        )
        self.clip = VideoClip.objects.create(
            project=self.project,
            title=TITLE_MARKER,
            start_time=0,
            end_time=5,
            s3_object_key="clips/a.mp4",
        )

    def test_final_render_failure_does_not_log_message_or_traceback(self):
        client = MagicMock()
        client.download_file.side_effect = lambda b, k, p: open(p, "wb").close()
        tmp = tempfile.mkdtemp()
        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.get_client",
            return_value=client,
        ), patch("moviepy.VideoFileClip", side_effect=RuntimeError(f"{SECRET} {TEXT_MARKER}")), \
                override_settings(MEDIA_ROOT=tmp):
            with capture_logs() as logs:
                with self.assertRaises(Exception):
                    render_video_segments(self.project.id)

        self.assert_clean(logs)

    def test_on_failure_recording_error_does_not_log_traceback(self):
        with patch(
            "apps.videos.tasks.VideoClip.objects.get", side_effect=DatabaseError(SECRET)
        ):
            with capture_logs() as logs:
                ClipRenderTask().on_failure(
                    RuntimeError(SECRET), "tid", [str(self.clip.id)], {}, None
                )

        self.assert_clean(logs)

    def test_orphan_delete_failure_does_not_log_traceback(self):
        VideoClip.objects.filter(pk=self.clip.pk).update(status=VideoClip.Status.DRAFT)

        def completing_render(clip_id):
            VideoClip.objects.filter(pk=clip_id).update(
                status=VideoClip.Status.COMPLETED, s3_object_key="clips/new.mp4"
            )

        with patch(
            "apps.videos.services.render_engine.RenderEngine.render_clip",
            side_effect=completing_render,
        ), patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.delete_object",
            side_effect=OSError(SECRET),
        ):
            with capture_logs() as logs:
                result = render_clip_task.apply(args=[str(self.clip.id)])

        self.assertTrue(result.successful())
        self.assert_clean(logs)


class SocialTaskLogTests(LogPrivacyAssertions, CapturedTaskFailures, TestCase):
    def setUp(self):
        user = User.objects.create_user(username="sl", email="sl@test.com", password="pw")
        workspace = Workspace.objects.create(
            name="SL", owner=user, ayrshare_profile_key="profile-key"
        )
        project = VideoProject.objects.create(
            workspace=workspace,
            uploaded_by=user,
            title=TITLE_MARKER,
            metadata={"full_text": f"transcript {TEXT_MARKER}"},
        )
        clip = VideoClip.objects.create(
            project=project, title=TITLE_MARKER, start_time=0, end_time=5, s3_object_key="c.mp4"
        )
        self.post = ScheduledPost.objects.create(
            video_clip=clip,
            platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z",
            status=ScheduledPost.Status.QUEUED,
            generated_caption=TEXT_MARKER,
        )

    def test_generic_publish_failure_does_not_log_message(self):
        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            side_effect=RuntimeError(f"{SECRET} {TEXT_MARKER}"),
        ):
            with capture_logs() as logs:
                result = upload_to_social_network.apply(args=[str(self.post.id)])

        self.assertTrue(result.failed())
        self.assert_clean(logs)

    def test_ayrshare_error_does_not_log_message(self):
        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://signed.test/x",
        ), patch(
            "apps.integrations.ayrshare_api.AyrshareClient.send_post",
            side_effect=AyrshareAPIError(f"{SECRET} {TEXT_MARKER}"),
        ):
            with capture_logs() as logs:
                upload_to_social_network.apply(args=[str(self.post.id)])

        self.assert_clean(logs)

    def test_seo_safety_violation_does_not_log_message(self):
        from apps.core.security import UnsafeContentError

        with patch(
            "apps.core.security.AI_Security_Shield.check_content_safety",
            side_effect=UnsafeContentError(f"{SECRET} {TEXT_MARKER}"),
        ):
            with capture_logs() as logs:
                process_video_seo.apply(args=[self.post.id])

        self.assert_clean(logs)
