"""
PAY-01: a plan with has_watermark renders with the OneCreator logo.

The decision is the plan's, read at render time on the server: nothing the API
receives can switch it off. A workspace without a plan has no limits (as in
PricingEngine), so it renders as before.
"""
import hashlib
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from moviepy import ColorClip, VideoFileClip
from PIL import Image

from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.render_engine import RenderEngine
from apps.videos.services.watermark import (
    WATERMARK_OPACITY,
    WATERMARK_WIDTH_RATIO,
    WatermarkUnavailableError,
    apply_watermark,
    project_needs_watermark,
)
from apps.videos.tasks import render_video_segments
from apps.videos.tests.fakes import FakeRedis

User = get_user_model()

ENGINE = "apps.videos.services.render_engine"
RENDER_TASKS = "apps.videos.tasks.render"
GET_CLIENT = "apps.videos.services.storage_service.CloudflareR2Manager.get_client"
REDIS_CLIENT = "apps.videos.services.ai.project_lock.get_redis_client"

WIDTH, HEIGHT = 360, 640


def black_clip(duration=1):
    return ColorClip((WIDTH, HEIGHT), color=(0, 0, 0), duration=duration)


def make_project(plan_name=None, **fields):
    user = User.objects.create_user(username=f"wm-{plan_name}", email=f"wm-{plan_name}@test.com", password="pw")
    plan = SubscriptionPlan.objects.get(name=plan_name) if plan_name else None
    workspace = Workspace.objects.create(name=f"WM {plan_name}", owner=user, subscription_plan=plan)
    return VideoProject.objects.create(
        workspace=workspace, uploaded_by=user, title="wm", add_subtitles=False,
        source_file="videos/raw/source.mp4", **fields,
    )


class WatermarkAssetTests(SimpleTestCase):
    def test_the_logo_is_committed_as_a_transparent_png(self):
        from django.conf import settings

        self.assertTrue(os.path.isfile(settings.WATERMARK_PATH))
        with Image.open(settings.WATERMARK_PATH) as logo:
            self.assertEqual(logo.mode, "RGBA")
            self.assertIn(0, np.asarray(logo)[..., 3])  # has transparent pixels

    def test_the_asset_lives_where_the_backlog_says(self):
        from django.conf import settings

        self.assertTrue(settings.WATERMARK_PATH.replace("\\", "/").endswith("assets/watermarks/onecreator_logo.png"))


class WhichProjectsAreWatermarkedTests(TestCase):
    def test_the_free_plan_is_watermarked(self):
        self.assertTrue(project_needs_watermark(make_project("Gratis")))

    def test_a_paid_plan_without_watermark_is_not(self):
        self.assertFalse(project_needs_watermark(make_project("Creador")))

    def test_a_workspace_without_a_plan_is_not(self):
        self.assertFalse(project_needs_watermark(make_project(None)))

    def test_the_plan_is_read_at_render_time(self):
        project = make_project("Gratis")
        SubscriptionPlan.objects.filter(name="Gratis").update(has_watermark=False)
        project = VideoProject.objects.get(pk=project.pk)

        self.assertFalse(project_needs_watermark(project))


class ApplyWatermarkTests(SimpleTestCase):
    def changed_pixels(self, frame):
        mask = frame.sum(axis=2) > 0
        ys, xs = np.where(mask)
        return mask, xs, ys

    def test_the_logo_sits_in_the_bottom_right_corner(self):
        frame = apply_watermark(black_clip()).get_frame(0)
        _, xs, ys = self.changed_pixels(frame)

        self.assertGreater(len(xs), 0)
        self.assertGreater(xs.min(), WIDTH * 0.7)
        self.assertGreater(ys.min(), HEIGHT * 0.9)
        self.assertLess(xs.max(), WIDTH)
        self.assertLess(ys.max(), HEIGHT)

    def test_the_rest_of_the_frame_is_untouched(self):
        frame = apply_watermark(black_clip()).get_frame(0)
        mask, xs, ys = self.changed_pixels(frame)

        outside = mask.copy()
        outside[ys.min():ys.max() + 1, xs.min():xs.max() + 1] = False
        self.assertFalse(outside.any())

    def test_the_logo_is_at_most_fifteen_percent_of_the_width(self):
        _, xs, _ = self.changed_pixels(apply_watermark(black_clip()).get_frame(0))

        width = xs.max() - xs.min() + 1
        self.assertLessEqual(width, round(WIDTH * WATERMARK_WIDTH_RATIO))
        self.assertGreaterEqual(width, round(WIDTH * WATERMARK_WIDTH_RATIO) * 0.5)

    def test_the_logo_is_forty_percent_opaque(self):
        self.assertEqual(WATERMARK_OPACITY, 0.4)
        frame = apply_watermark(black_clip()).get_frame(0)

        # A white logo over black can reach at most 40% of 255.
        self.assertLessEqual(frame.max(), round(255 * WATERMARK_OPACITY) + 2)
        self.assertGreater(frame.max(), 40)

    def test_the_logo_lasts_the_whole_clip(self):
        clip = apply_watermark(black_clip(duration=2))

        self.assertAlmostEqual(clip.duration, 2)
        self.assertTrue(clip.get_frame(1.9).any())

    def test_the_size_follows_the_clip(self):
        wide = ColorClip((1280, 720), color=(0, 0, 0), duration=1)
        frame = apply_watermark(wide).get_frame(0)
        _, xs, ys = self.changed_pixels(frame)

        self.assertGreater(xs.min(), 1280 * 0.7)
        self.assertGreater(ys.min(), 720 * 0.85)

    def test_the_audio_is_kept(self):
        from moviepy import AudioClip

        base = black_clip().with_audio(AudioClip(lambda t: np.zeros((np.size(t), 2)), duration=1, fps=22050))

        self.assertIsNotNone(apply_watermark(base).audio)

    def test_without_the_asset_it_fails_instead_of_delivering_unmarked(self):
        with override_settings(WATERMARK_PATH="/nonexistent/onecreator_logo.png"):
            with self.assertRaises(WatermarkUnavailableError):
                apply_watermark(black_clip())


class RenderClipWatermarkTests(TestCase):
    def render(self, project, asset_missing=False):
        clip = VideoClip.objects.create(
            project=project, title="c", start_time=0, end_time=5, status=VideoClip.Status.RENDERING,
        )
        rendered, watermarked = MagicMock(name="rendered"), MagicMock(name="watermarked")
        layout = MagicMock()
        layout.apply.return_value = rendered
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        stamp = patch(f"{ENGINE}.apply_watermark", return_value=watermarked)
        if asset_missing:
            stamp = patch(f"{ENGINE}.apply_watermark", side_effect=WatermarkUnavailableError("missing"))
        with override_settings(MEDIA_ROOT=media.name), stamp as apply, \
                patch(f"{ENGINE}.VideoFileClip") as video, \
                patch(f"{ENGINE}.get_layout_strategy", return_value=layout), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.upload_video",
                      return_value="clips/key.mp4") as upload:
            video.return_value.subclipped.return_value = MagicMock()
            try:
                RenderEngine.render_clip(clip.id)
                error = None
            except WatermarkUnavailableError as exc:
                error = exc
        clip.refresh_from_db()
        return clip, rendered, watermarked, apply, upload, error

    def test_a_watermarked_plan_writes_the_stamped_clip(self):
        clip, rendered, watermarked, apply, _, _ = self.render(make_project("Gratis"))

        apply.assert_called_once_with(rendered)
        watermarked.write_videofile.assert_called_once()
        rendered.write_videofile.assert_not_called()
        self.assertEqual(clip.status, VideoClip.Status.COMPLETED)

    def test_a_plan_without_watermark_writes_the_clip_as_before(self):
        clip, rendered, _, apply, _, _ = self.render(make_project("Creador"))

        apply.assert_not_called()
        rendered.write_videofile.assert_called_once()
        self.assertEqual(clip.status, VideoClip.Status.COMPLETED)

    def test_no_plan_writes_the_clip_as_before(self):
        _, rendered, _, apply, _, _ = self.render(make_project(None))

        apply.assert_not_called()
        rendered.write_videofile.assert_called_once()

    def test_a_missing_asset_delivers_nothing(self):
        clip, rendered, _, _, upload, error = self.render(make_project("Gratis"), asset_missing=True)

        self.assertIsInstance(error, WatermarkUnavailableError)
        rendered.write_videofile.assert_not_called()
        upload.assert_not_called()
        self.assertNotEqual(clip.status, VideoClip.Status.COMPLETED)


class RenderVideoSegmentsWatermarkTests(TestCase):
    """The old approval path still exports one final video: it carries the logo too."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.tmp_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        for patcher in (
            patch(GET_CLIENT, return_value=self._client()),
            patch(REDIS_CLIENT, return_value=FakeRedis()),
            patch.object(tempfile, "tempdir", self.tmp_root),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _client(self):
        client = MagicMock()

        def download(bucket, key, path):
            with open(path, "wb") as fh:
                fh.write(b"\x00" * 1024)

        client.download_file.side_effect = download
        return client

    def run_export(self, project):
        project.status = VideoProject.Status.RENDERING
        project.original_r2_key = "videos/1/original/source.mp4"
        project.approved_segments = [{"start": 0, "end": 2, "text": "hola"}]
        project.save()
        processed, watermarked = MagicMock(name="processed"), MagicMock(name="watermarked")
        watermarked.write_videofile.side_effect = RuntimeError("stop after the write")
        processed.write_videofile.side_effect = RuntimeError("stop after the write")
        layout = MagicMock()
        layout.apply.return_value = processed
        source = MagicMock()
        source.__enter__.return_value = source
        with override_settings(MEDIA_ROOT=self.media_root), \
                patch("moviepy.VideoFileClip", return_value=source), \
                patch("moviepy.concatenate_videoclips", return_value=MagicMock(duration=2)), \
                patch("apps.videos.services.layouts.get_layout_strategy", return_value=layout), \
                patch(f"{RENDER_TASKS}.apply_watermark", return_value=watermarked):
            with self.assertRaises(Exception):
                render_video_segments(project.id)
        return processed, watermarked

    def test_a_watermarked_plan_exports_the_stamped_video(self):
        processed, watermarked = self.run_export(make_project("Gratis"))

        watermarked.write_videofile.assert_called_once()
        processed.write_videofile.assert_not_called()

    def test_a_plan_without_watermark_exports_as_before(self):
        processed, watermarked = self.run_export(make_project("Creador"))

        processed.write_videofile.assert_called_once()
        watermarked.write_videofile.assert_not_called()


class RealRenderTests(TestCase):
    """A real encode: with and without the logo the files differ, and only the corner does."""

    def encode(self, clip, path):
        clip.write_videofile(path, codec="libx264", fps=12, preset="ultrafast", logger=None,
                             ffmpeg_params=["-pix_fmt", "yuv420p"])
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def test_the_output_hash_differs_with_and_without_the_watermark(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is not installed")
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)

        plain = self.encode(black_clip(), os.path.join(folder, "plain.mp4"))
        stamped = self.encode(apply_watermark(black_clip()), os.path.join(folder, "stamped.mp4"))

        self.assertNotEqual(plain, stamped)
        with VideoFileClip(os.path.join(folder, "stamped.mp4")) as video:
            frame = video.get_frame(0.2)
        self.assertGreater(frame[int(HEIGHT * 0.9):, int(WIDTH * 0.7):].max(), 20)  # the logo
        self.assertLess(frame[:int(HEIGHT * 0.8), :].max(), 10)  # nothing else
