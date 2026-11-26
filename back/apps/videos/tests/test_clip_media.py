"""
What the web app shows of a rendered clip (services/clip_media.py):
``preview_url`` plays its file and ``thumbnail_url`` is one frame of it.

- Both are temporary URLs built from the clip row: the absolute /media/ URL
  with local files, a presigned GET in R2. No query and no network call per
  clip, so a project with many clips costs what one with a single clip does.
- The thumbnail is made at the end of the render, best effort: a broken
  ffmpeg never fails the render.
- It goes with the file it shows: a re-render and a delete remove it.
"""
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.serializers import VideoClipSerializer, VideoProjectSerializer
from apps.videos.services import clip_media
from apps.videos.tasks import render_clip_task
from apps.videos.tests.test_clip_actions import DELETE_OBJECT, ClipActionsHarness, clip_url
from apps.videos.tests.test_render_clip_claims import ClaimHarness, take_over
from apps.videos.tests.test_render_tasks import RenderHarness

RUN = "apps.videos.services.clip_media.subprocess.run"
DONE = VideoClip.Status.COMPLETED


def fake_ffmpeg(command, **kwargs):
    """What ffmpeg leaves: a file at the last argument."""
    with open(command[-1], "wb") as handle:
        handle.write(b"\xff\xd8 not a real jpeg")


def write_bytes(path, **kwargs):
    with open(path, "wb") as handle:
        handle.write(b"not a real video")


class UrlHarness(RenderHarness):
    def setUp(self):
        super().setUp()
        self.request = RequestFactory().get("/api/v1/projects/")

    def serialized(self, clip):
        return VideoClipSerializer(clip, context={"request": self.request}).data

    def rendered(self, number=1, **fields):
        # With local files a storage key is the absolute path of the file, under MEDIA_ROOT.
        key = os.path.join(str(settings.MEDIA_ROOT), "videos", "u", "p", f"clip-{number}.mp4")
        return self.make_clip(
            status=DONE, s3_object_key=key, thumbnail_object_key=clip_media.thumbnail_key_for(key), **fields,
        )


@override_settings(USE_R2=False, MEDIA_URL="/media/", MEDIA_PUBLIC_BASE_URL="")
class LocalUrlTests(UrlHarness, TestCase):
    def test_a_completed_clip_gets_the_urls_of_its_file_and_its_thumbnail(self):
        data = self.serialized(self.rendered())

        self.assertEqual(data["preview_url"], "http://testserver/media/videos/u/p/clip-1.mp4")
        self.assertEqual(data["thumbnail_url"], "http://testserver/media/videos/u/p/clip-1.jpg")
        self.assertNotIn("thumbnail_object_key", data)

    def test_a_published_clip_plays_too(self):
        clip = self.rendered()
        VideoClip.objects.filter(pk=clip.pk).update(status=VideoClip.Status.PUBLISHED)
        clip.refresh_from_db()

        self.assertEqual(self.serialized(clip)["preview_url"], "http://testserver/media/videos/u/p/clip-1.mp4")

    def test_a_draft_gets_none(self):
        data = self.serialized(self.make_clip())

        self.assertIsNone(data["preview_url"])
        self.assertIsNone(data["thumbnail_url"])

    def test_a_clip_that_is_not_ready_does_not_play_the_file_it_keeps(self):
        # Rendered again: a draft (then rendering, or failed) that keeps its old file until the new one is saved.
        for status in (VideoClip.Status.DRAFT, VideoClip.Status.RENDERING, VideoClip.Status.FAILED):
            clip = self.rendered()
            VideoClip.objects.filter(pk=clip.pk).update(status=status)
            clip.refresh_from_db()
            self.assertIsNone(self.serialized(clip)["preview_url"], status)

    def test_a_completed_clip_without_a_thumbnail_gets_none_for_it(self):
        clip = self.make_clip(status=DONE, s3_object_key=os.path.join(str(settings.MEDIA_ROOT), "videos", "clip-1.mp4"))

        data = self.serialized(clip)

        self.assertTrue(data["preview_url"])
        self.assertIsNone(data["thumbnail_url"])

    def test_the_project_takes_the_thumbnail_of_its_first_clip_that_has_one(self):
        self.make_clip()
        self.rendered(number=2)
        self.rendered(number=3)

        data = VideoProjectSerializer(self.project, context={"request": self.request}).data

        self.assertEqual(data["thumbnail_url"], "http://testserver/media/videos/u/p/clip-2.jpg")

    def test_a_project_without_thumbnails_gets_none(self):
        self.make_clip()

        self.assertIsNone(VideoProjectSerializer(self.project, context={"request": self.request}).data["thumbnail_url"])

    def test_a_project_with_five_clips_costs_the_queries_of_one_with_a_single_clip(self):
        def queries():
            project = VideoProject.objects.prefetch_related("clips__layers").get(pk=self.project.pk)
            with CaptureQueriesContext(connection) as captured:
                data = VideoProjectSerializer(project, context={"request": self.request}).data
            return len(captured), data

        self.rendered(number=1)
        one, _ = queries()
        for number in range(2, 6):
            self.rendered(number=number)
        five, data = queries()

        self.assertEqual(len(data["clips"]), 5)
        self.assertTrue(all(clip["preview_url"] and clip["thumbnail_url"] for clip in data["clips"]))
        self.assertTrue(data["thumbnail_url"])
        self.assertEqual(five, one)
        self.assertEqual(five, 0)


@override_settings(USE_R2=True, CLOUDFLARE_R2_BUCKET_NAME="bucket", CLIP_DOWNLOAD_URL_TTL_SECONDS=1234)
class R2UrlTests(UrlHarness, TestCase):
    def test_the_urls_are_presigned_gets_that_last_what_a_download_link_does(self):
        clip = self.make_clip(status=DONE, s3_object_key="videos/u/p/a.mp4", thumbnail_object_key="videos/u/p/a.jpg")

        with patch("apps.videos.services.storage_service._get_r2_client") as get_client:
            get_client.return_value.generate_presigned_url.side_effect = (
                lambda method, Params, ExpiresIn: f"https://r2.test/{Params['Key']}?X-Amz-Expires={ExpiresIn}"
            )
            data = self.serialized(clip)

        self.assertEqual(data["preview_url"], "https://r2.test/videos/u/p/a.mp4?X-Amz-Expires=1234")
        self.assertEqual(data["thumbnail_url"], "https://r2.test/videos/u/p/a.jpg?X-Amz-Expires=1234")
        # Played in the page, not downloaded: no Content-Disposition is asked for.
        for call in get_client.return_value.generate_presigned_url.call_args_list:
            self.assertEqual(call.kwargs["Params"], {"Bucket": "bucket", "Key": call.kwargs["Params"]["Key"]})

    def test_one_client_signs_every_clip_of_the_response(self):
        for number in range(5):
            self.make_clip(
                status=DONE, s3_object_key=f"videos/u/p/{number}.mp4", thumbnail_object_key=f"videos/u/p/{number}.jpg",
            )

        with patch("apps.videos.services.storage_service._get_r2_client") as get_client:
            get_client.return_value.generate_presigned_url.return_value = "https://r2.test/x"
            VideoProjectSerializer(self.project, context={"request": self.request}).data

        self.assertEqual(get_client.call_count, 1)
        self.assertEqual(get_client.return_value.generate_presigned_url.call_count, 10)

    def test_a_url_that_cannot_be_signed_is_none_and_the_clip_is_still_sent(self):
        clip = self.make_clip(status=DONE, s3_object_key="videos/u/p/a.mp4")

        with patch("apps.videos.services.storage_service._get_r2_client", side_effect=RuntimeError("no keys")):
            data = self.serialized(clip)

        self.assertIsNone(data["preview_url"])
        self.assertEqual(data["status"], "completed")


class ThumbnailTimeTests(TestCase):
    def test_the_frame_is_at_one_second_or_the_middle_of_a_short_clip(self):
        self.assertEqual(clip_media.thumbnail_second(30), 1.0)
        self.assertEqual(clip_media.thumbnail_second(2), 1.0)
        self.assertEqual(clip_media.thumbnail_second(1.5), 0.75)
        self.assertEqual(clip_media.thumbnail_second(0), 0.0)


class RenderThumbnailTests(ClaimHarness, TestCase):
    """The real engine and task; MoviePy leaves a file and ffmpeg is the one of the test."""

    def setUp(self):
        super().setUp()
        self.layout.apply.return_value.write_videofile.side_effect = write_bytes

    def test_the_render_stores_a_thumbnail_next_to_the_clip_file(self):
        clip = self.make_clip()

        with self.engine() as r2, patch(RUN, side_effect=fake_ffmpeg) as run:
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        clip.refresh_from_db()
        self.assertEqual(clip.status, DONE)
        video_key, thumbnail_key = self.uploaded_keys()
        self.assertEqual(clip.s3_object_key, video_key)
        self.assertEqual(clip.thumbnail_object_key, thumbnail_key)
        self.assertEqual(thumbnail_key, video_key[:-len(".mp4")] + ".jpg")
        self.assertEqual(r2.upload_file.call_args_list[1].kwargs["ExtraArgs"], {"ContentType": "image/jpeg"})
        # One ffmpeg call, with a timeout of a few seconds, and nothing of it left on the disk.
        (call,) = run.call_args_list
        self.assertLessEqual(call.kwargs["timeout"], 10)
        self.assertEqual(call.args[0][call.args[0].index("-ss") + 1], "1.000")
        self.assertFalse(os.path.exists(call.args[0][-1]))

    def test_a_broken_ffmpeg_still_completes_the_render_without_a_thumbnail(self):
        failures = (
            subprocess.CalledProcessError(1, "ffmpeg"), subprocess.TimeoutExpired("ffmpeg", 8),
            FileNotFoundError("ffmpeg"),
        )
        for failure in failures:
            clip = self.make_clip()
            with self.engine(), patch(RUN, side_effect=failure), self.assertLogs(clip_media.logger, "WARNING") as logs:
                result = render_clip_task.apply(args=[str(clip.id)])

            self.assertTrue(result.successful(), failure)
            clip.refresh_from_db()
            self.assertEqual(clip.status, DONE)
            self.assertTrue(clip.s3_object_key)
            self.assertIsNone(clip.thumbnail_object_key)
            self.assertIsNone(VideoClipSerializer(clip).data["thumbnail_url"])
            self.assertEqual(
                logs.output, [f"WARNING:{clip_media.logger.name}:thumbnail.failed clip_id={clip.id} error={type(failure).__name__}"],
            )

    def test_a_thumbnail_that_cannot_be_stored_still_completes_the_render(self):
        clip = self.make_clip()

        def upload(path, bucket, key, **kwargs):
            if key.endswith(".jpg"):
                raise OSError("r2")

        with self.engine() as r2, patch(RUN, side_effect=fake_ffmpeg):
            r2.upload_file.side_effect = upload
            result = render_clip_task.apply(args=[str(clip.id)])

        self.assertTrue(result.successful())
        clip.refresh_from_db()
        self.assertEqual((clip.status, clip.thumbnail_object_key), (DONE, None))

    def test_a_rerender_deletes_the_old_file_and_its_thumbnail(self):
        clip = self.make_clip(s3_object_key="videos/old.mp4", thumbnail_object_key="videos/old.jpg")

        with self.engine() as r2, patch(RUN, side_effect=fake_ffmpeg):
            render_clip_task.apply(args=[str(clip.id)])

        clip.refresh_from_db()
        self.assertNotIn(clip.thumbnail_object_key, (None, "videos/old.jpg"))
        self.assertEqual(
            sorted(call.kwargs["Key"] for call in r2.delete_object.call_args_list), ["videos/old.jpg", "videos/old.mp4"],
        )

    def test_a_rerender_whose_thumbnail_fails_does_not_keep_the_old_one(self):
        clip = self.make_clip(s3_object_key="videos/old.mp4", thumbnail_object_key="videos/old.jpg")

        with self.engine() as r2, patch(RUN, side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            render_clip_task.apply(args=[str(clip.id)])

        clip.refresh_from_db()
        self.assertIsNone(clip.thumbnail_object_key)  # never the picture of the file that went
        self.assertIn("videos/old.jpg", [call.kwargs["Key"] for call in r2.delete_object.call_args_list])

    def test_a_copy_that_lost_the_clip_deletes_its_thumbnail_too(self):
        clip = self.make_clip()

        def upload(path, bucket, key, **kwargs):
            if key.endswith(".jpg"):
                take_over(clip.id)

        with self.engine() as r2, patch(RUN, side_effect=fake_ffmpeg):
            r2.upload_file.side_effect = upload
            render_clip_task.apply(args=[str(clip.id)])

        clip.refresh_from_db()
        self.assertEqual((clip.status, clip.s3_object_key, clip.thumbnail_object_key), ("rendering", None, None))
        self.assertEqual(
            sorted(call.kwargs["Key"] for call in r2.delete_object.call_args_list), sorted(self.uploaded_keys()),
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
class RealFfmpegTests(TestCase):
    def frame_of(self, folder, size, seconds):
        video = os.path.join(folder, "clip.mp4")
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10:duration={seconds}",
             "-pix_fmt", "yuv420p", video],
            check=True, capture_output=True,
        )
        with override_settings(USE_R2=False):
            key = clip_media.make_clip_thumbnail("clip-id", video, video, seconds)
        return video, key

    def test_it_writes_a_jpeg_of_360_on_the_long_side(self):
        from PIL import Image

        for size, expected in (("720x1280", (202, 360)), ("1280x720", (360, 202)), ("640x640", (360, 360))):
            with self.subTest(size=size):
                folder = self.enterContext(__import__("tempfile").TemporaryDirectory())
                video, key = self.frame_of(folder, size, 3)
                self.assertEqual(key, os.path.join(folder, "clip.jpg"))
                with Image.open(key) as image:
                    self.assertEqual((image.format, image.size), ("JPEG", expected))
                self.assertEqual(sorted(os.listdir(folder)), ["clip.jpg", "clip.mp4"])

    def test_a_clip_shorter_than_two_seconds_gets_its_middle_frame(self):
        folder = self.enterContext(__import__("tempfile").TemporaryDirectory())

        _video, key = self.frame_of(folder, "320x240", 0.6)

        self.assertTrue(key and os.path.getsize(key) > 0)


class DeleteTests(ClipActionsHarness, APITestCase):
    def test_deleting_a_clip_deletes_its_thumbnail(self):
        _project, (clip, _failed) = self.finished()
        VideoClip.objects.filter(pk=clip.pk).update(thumbnail_object_key="videos/old-0.jpg")

        with patch(DELETE_OBJECT) as delete, self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(clip_url(clip))

        self.assertEqual(response.status_code, 204, response.content[:300])
        self.assertEqual(sorted(call.args[0] for call in delete.call_args_list), ["videos/old-0.jpg", "videos/old-0.mp4"])

    def test_a_thumbnail_that_cannot_be_deleted_does_not_fail_the_delete(self):
        _project, (clip, _failed) = self.finished()
        VideoClip.objects.filter(pk=clip.pk).update(thumbnail_object_key="videos/old-0.jpg")

        with patch(DELETE_OBJECT, side_effect=OSError("r2")), self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(clip_url(clip))

        self.assertEqual(response.status_code, 204, response.content[:300])

    @override_settings(USE_R2=False, MEDIA_URL="/media/", MEDIA_PUBLIC_BASE_URL="")
    def test_the_api_sends_the_urls_in_the_clip_the_project_and_the_list(self):
        project, (clip, failed) = self.finished()
        key = os.path.join(str(settings.MEDIA_ROOT), "videos", "u", "clip.mp4")
        VideoClip.objects.filter(pk=clip.pk).update(s3_object_key=key, thumbnail_object_key=key[:-4] + ".jpg")
        video, image = "http://testserver/media/videos/u/clip.mp4", "http://testserver/media/videos/u/clip.jpg"

        detail = self.client.get(clip_url(clip)).data
        in_project = self.client.get(f"/api/v1/projects/{project.pk}/").data
        listed = next(row for row in self.client.get("/api/v1/projects/").data if row["id"] == str(project.pk))

        self.assertEqual((detail["preview_url"], detail["thumbnail_url"]), (video, image))
        for body in (in_project, listed):
            clips = {row["id"]: row for row in body["clips"]}
            self.assertEqual((clips[str(clip.pk)]["preview_url"], clips[str(clip.pk)]["thumbnail_url"]), (video, image))
            self.assertEqual((clips[str(failed.pk)]["preview_url"], clips[str(failed.pk)]["thumbnail_url"]), (None, None))
            self.assertEqual(body["thumbnail_url"], image)

    @override_settings(USE_R2=False, MEDIA_URL="/media/", MEDIA_PUBLIC_BASE_URL="")
    def test_a_post_shows_the_thumbnail_of_its_clip(self):
        from datetime import timedelta

        from django.utils import timezone

        _project, (clip, _failed) = self.finished()
        key = os.path.join(str(settings.MEDIA_ROOT), "videos", "u", "clip.jpg")
        VideoClip.objects.filter(pk=clip.pk).update(thumbnail_object_key=key)
        ScheduledPost.objects.create(video_clip=clip, platform="TIKTOK", publish_at=timezone.now() + timedelta(days=1))

        (post,) = self.client.get(reverse("scheduled-post-list")).data["results"]

        self.assertEqual(post["clip"]["thumbnail_url"], "http://testserver/media/videos/u/clip.jpg")
