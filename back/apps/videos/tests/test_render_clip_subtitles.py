"""
RenderEngine.render_clip never calls a transcription provider: without the
project's transcript the clip renders without subtitles. The old fallback
transcribed again with no capacity reservation and no AIUsageRecord.
"""

import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoClip, VideoProject
from apps.videos.services.render_engine import RenderEngine

User = get_user_model()

ENGINE = "apps.videos.services.render_engine"


class RenderClipSubtitleSourceTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="sub", email="sub@test.com", password="pw")
        workspace = Workspace.objects.create(name="Subs", owner=user)
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="Subs",
            source_file="videos/raw/source.mp4", add_subtitles=True, transcript_data=None,
        )
        # RENDERING, as render_clip_task's claim leaves it: the engine only saves over a claimed clip.
        self.clip = VideoClip.objects.create(
            project=self.project, title="c", start_time=0, end_time=5, status=VideoClip.Status.RENDERING,
        )

    def render(self):
        rendered = MagicMock()
        layout = MagicMock()
        layout.apply.return_value = rendered
        with override_settings(MEDIA_ROOT=self.media.name), \
                patch(f"{ENGINE}.VideoFileClip") as video, \
                patch(f"{ENGINE}.get_layout_strategy", return_value=layout), \
                patch(f"{ENGINE}.SubtitleEngine") as subtitles, \
                patch(f"{ENGINE}.TranscriptionEngine.transcribe") as transcribe, \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.upload_video",
                      return_value="clips/key.mp4"):
            video.return_value.subclipped.return_value = MagicMock()
            RenderEngine.render_clip(self.clip.id)
        return transcribe, subtitles, rendered

    def test_without_a_transcript_no_provider_is_called_and_no_subtitles_are_burned(self):
        with self.assertLogs(ENGINE, level="WARNING") as logs:
            transcribe, subtitles, rendered = self.render()

        transcribe.assert_not_called()
        subtitles.return_value.add_subtitles.assert_not_called()
        rendered.write_videofile.assert_called_once()  # the clip is still delivered
        self.assertTrue(any("render.subtitles_skipped" in line for line in logs.output))
        self.clip.refresh_from_db()
        self.assertEqual(self.clip.status, VideoClip.Status.COMPLETED)
        self.assertEqual(AIUsageRecord.objects.count(), 0)

    def test_with_the_project_transcript_the_subtitles_come_from_it(self):
        VideoProject.objects.filter(pk=self.project.pk).update(transcript_data=[
            {"text": "hola", "start": 0.0, "end": 0.5}, {"text": "mundo", "start": 0.5, "end": 1.0},
        ])

        transcribe, subtitles, _ = self.render()

        transcribe.assert_not_called()
        subtitles.return_value.add_subtitles.assert_called_once()
