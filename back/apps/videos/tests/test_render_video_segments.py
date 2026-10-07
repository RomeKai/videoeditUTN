"""
Bug 4: render_video_segments must not leak the downloaded source or the
rendered output on disk when any step after the download fails.
"""
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.tasks import render_video_segments

User = get_user_model()

GET_CLIENT = "apps.videos.services.storage_service.CloudflareR2Manager.get_client"
VIDEO_FILE_CLIP = "moviepy.VideoFileClip"
CONCATENATE = "moviepy.concatenate_videoclips"
LAYOUT = "apps.videos.services.layouts.get_layout_strategy"


def _fake_download(bucket, key, path):
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 1024)


class RenderVideoSegmentsCleanupTests(TestCase):
    def setUp(self):
        self.tmp_root = tempfile.mkdtemp()
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

        user = User.objects.create_user(username="seg", email="seg@test.com", password="pw")
        workspace = Workspace.objects.create(name="Segments Workspace", owner=user)
        self.project = VideoProject.objects.create(
            workspace=workspace,
            uploaded_by=user,
            title="Segments",
            status=VideoProject.Status.RENDERING,
            original_r2_key="videos/1/original/source.mp4",
            approved_segments=[{"start": 0, "end": 2, "text": "hola"}],
            add_subtitles=False,
        )

        client = MagicMock()
        client.download_file.side_effect = _fake_download
        patcher = patch(GET_CLIENT, return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Route every tempfile created by the task into an isolated directory.
        tempdir_patcher = patch.object(tempfile, "tempdir", self.tmp_root)
        tempdir_patcher.start()
        self.addCleanup(tempdir_patcher.stop)

    def _run(self):
        with override_settings(MEDIA_ROOT=self.media_root):
            with self.assertRaises(Exception):
                render_video_segments(self.project.id)

    def _leftover_files(self, root):
        return [os.path.join(d, f) for d, _, files in os.walk(root) for f in files]

    def test_failure_after_download_removes_the_source_copy(self):
        with patch(VIDEO_FILE_CLIP, side_effect=RuntimeError("corrupt source")):
            self._run()

        self.assertEqual(self._leftover_files(self.tmp_root), [])

    def test_failure_during_export_removes_source_and_partial_output(self):
        def write_then_fail(path, **kwargs):
            with open(path, "wb") as fh:
                fh.write(b"partial")
            raise RuntimeError("encoder crashed")

        processed = MagicMock()
        processed.write_videofile.side_effect = write_then_fail
        layout = MagicMock()
        layout.apply.return_value = processed
        source = MagicMock()
        source.__enter__.return_value = source

        with patch(VIDEO_FILE_CLIP, return_value=source), \
                patch(CONCATENATE, return_value=MagicMock(duration=2)), \
                patch(LAYOUT, return_value=layout):
            self._run()

        self.assertEqual(self._leftover_files(self.tmp_root), [])
        self.assertEqual(self._leftover_files(self.media_root), [])
