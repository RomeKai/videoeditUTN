"""
The paper-edit approval must hand its segments to the final render through ONE
source of truth: the ``VideoProject.approved_segments`` column. Projects approved
before that fix only have ``metadata['approved_segments']`` and must still render.
"""
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.services.ai.errors import NonRetryableAIError
from apps.videos.services.segments import InvalidSegmentsError, validate_approved_segments
from apps.videos.tasks import render_video_segments

User = get_user_model()

GET_CLIENT = "apps.videos.services.storage_service.CloudflareR2Manager.get_client"
UPLOAD = "apps.videos.services.storage_service.CloudflareR2Manager.upload_video"
VIDEO_FILE_CLIP = "moviepy.VideoFileClip"
CONCATENATE = "moviepy.concatenate_videoclips"
LAYOUT = "apps.videos.services.layouts.get_layout_strategy"
RESERVE = "apps.videos.views.wallet_service.reserve_funds"
DELAY = "apps.videos.tasks.render_video_segments.delay"

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "private words"},
    {"start": 5, "end": 7.5, "text": "more private words"},
]


def _fake_download(bucket, key, path):
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 16)


def _fake_write(path, **kwargs):
    with open(path, "wb") as fh:
        fh.write(b"rendered")


class ValidateApprovedSegmentsTests(TestCase):
    def test_accepts_numeric_segments_and_returns_floats(self):
        result = validate_approved_segments([{"start": 1, "end": 2.5, "text": "x"}])
        self.assertEqual(result, [{"start": 1.0, "end": 2.5, "text": "x"}])

    def test_rejects_invalid_shapes(self):
        invalid = [
            None,
            [],
            "nope",
            {"start": 0, "end": 1},
            ["not-a-dict"],
            [{"start": 0}],
            [{"start": "a", "end": 1}],
            [{"start": True, "end": 2}],
            [{"start": -1, "end": 1}],
            [{"start": 2, "end": 2}],
            [{"start": 3, "end": 2}],
            [{"start": float("nan"), "end": 2}],
            [{"start": 0, "end": float("inf")}],
            [{"start": 0, "end": 1, "text": 5}],
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidSegmentsError):
                    validate_approved_segments(raw)


class ApprovePaperEditStoresColumnTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="appr", email="appr@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Approve WS", owner=self.user)
        self.workspace.members.add(self.user)
        self.client.force_authenticate(user=self.user)
        self.project = VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=self.user,
            title="Approve",
            original_r2_key="videos/1/original/source.mp4",
            status=VideoProject.Status.AWAITING_APPROVAL,
            add_subtitles=False,
        )
        self.url = reverse("project-approve-paper-edit", args=[self.project.id])

    def _approve(self, segments):
        with patch(DELAY) as delay, patch(RESERVE) as reserve:
            reserve.return_value = MagicMock(id=1)
            res = self.client.post(self.url, {"approved_segments": segments}, format="json")
        return res, delay, reserve

    def test_approval_populates_the_column(self):
        res, delay, _ = self._approve(SEGMENTS)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.approved_segments, SEGMENTS)
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.metadata["final_duration"], 4.5)
        delay.assert_called_once_with(self.project.id)

    def test_invalid_segments_are_rejected_before_charging(self):
        for bad in (
            [{"start": -1, "end": 2}],
            [{"start": "nan", "end": 2}],
            ["not-a-dict"],
        ):
            with self.subTest(bad=bad):
                res, delay, reserve = self._approve(bad)
                self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
                reserve.assert_not_called()
                delay.assert_not_called()
                self.project.refresh_from_db()
                self.assertEqual(self.project.status, VideoProject.Status.AWAITING_APPROVAL)
                self.assertIsNone(self.project.approved_segments)

    def test_task_renders_a_project_approved_through_the_view(self):
        self._approve(SEGMENTS)
        source, processed = _render_mocks()

        with _patched_render(source, processed) as (_, upload):
            render_video_segments(self.project.id)

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(self.project.final_export_r2_key, "final/key.mp4")
        upload.assert_called_once()
        self.assertEqual(
            [c.args for c in source.subclipped.call_args_list], [(0.0, 2.0), (5.0, 7.5)]
        )


def _render_mocks():
    processed = MagicMock()
    processed.write_videofile.side_effect = _fake_write
    layout = MagicMock()
    layout.apply.return_value = processed
    source = MagicMock()
    source.__enter__.return_value = source
    source._layout = layout
    return source, processed


@contextmanager
def _patched_render(source, processed):
    """Patches R2, MoviePy and the layout so the task runs without real media."""
    client = MagicMock()
    client.download_file.side_effect = _fake_download
    media_root = tempfile.mkdtemp()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch(GET_CLIENT, return_value=client))
            stack.enter_context(patch(VIDEO_FILE_CLIP, return_value=source))
            stack.enter_context(patch(CONCATENATE, return_value=MagicMock(duration=4.5)))
            stack.enter_context(patch(LAYOUT, return_value=source._layout))
            upload = stack.enter_context(patch(UPLOAD, return_value="final/key.mp4"))
            stack.enter_context(override_settings(MEDIA_ROOT=media_root))
            yield client, upload
    finally:
        shutil.rmtree(media_root, ignore_errors=True)


class RenderVideoSegmentsSourceTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="src", email="src@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Src WS", owner=user)
        self.user = user

    def _project(self, **extra):
        return VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=self.user,
            title="Src",
            status=VideoProject.Status.RENDERING,
            original_r2_key="videos/1/original/source.mp4",
            add_subtitles=False,
            **extra,
        )

    def test_legacy_project_with_only_metadata_still_renders(self):
        project = self._project(metadata={"approved_segments": SEGMENTS, "final_duration": 4.5})
        source, processed = _render_mocks()

        with _patched_render(source, processed):
            render_video_segments(project.id)

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(
            [c.args for c in source.subclipped.call_args_list], [(0.0, 2.0), (5.0, 7.5)]
        )

    def test_column_wins_over_legacy_metadata(self):
        project = self._project(
            approved_segments=[{"start": 1, "end": 3, "text": ""}],
            metadata={"approved_segments": SEGMENTS},
        )
        source, processed = _render_mocks()

        with _patched_render(source, processed):
            render_video_segments(project.id)

        self.assertEqual([c.args for c in source.subclipped.call_args_list], [(1.0, 3.0)])

    def _assert_fails_without_retry(self, project, code):
        source, processed = _render_mocks()
        with _patched_render(source, processed) as (client, _):
            with patch.object(render_video_segments, "retry") as retry:
                with self.assertRaises(NonRetryableAIError):
                    render_video_segments(project.id)
        retry.assert_not_called()
        client.download_file.assert_not_called()
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_error_code, code)

    def test_missing_segments_fail_without_retry(self):
        self._assert_fails_without_retry(self._project(), "no_approved_segments")

    def test_invalid_segments_fail_without_retry(self):
        project = self._project(approved_segments=[{"start": 5, "end": 2, "text": "secret"}])
        self._assert_fails_without_retry(project, "invalid_approved_segments")

    def test_invalid_legacy_segments_fail_without_retry(self):
        project = self._project(metadata={"approved_segments": "garbage"})
        self._assert_fails_without_retry(project, "invalid_approved_segments")

    def test_failure_does_not_log_segment_text(self):
        project = self._project(approved_segments=[{"start": 5, "end": 2, "text": "SECRETWORDS"}])
        source, processed = _render_mocks()
        with _patched_render(source, processed):
            with patch.object(render_video_segments, "retry"):
                with self.assertLogs("apps.videos.tasks", level="DEBUG") as logs:
                    with self.assertRaises(NonRetryableAIError):
                        render_video_segments(project.id)
        self.assertNotIn("SECRETWORDS", "\n".join(logs.output))
