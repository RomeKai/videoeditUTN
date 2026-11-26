"""
DIST-03: the beat dispatches the scheduled posts every 5 minutes.

`dispatch_scheduled_posts_batch` moves a SCHEDULED post to QUEUED and sends it to
the worker with its publish time as ETA. A post whose time already passed (the beat
or the worker was down) goes out at once instead of waiting forever.
"""
import os
import re
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from backend.celery import app as celery_app
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject
from apps.videos.tasks.social import dispatch_scheduled_posts_batch

User = get_user_model()

UPLOAD = "apps.videos.tasks.social.upload_to_social_network.apply_async"
COMPOSE = os.path.join(settings.BASE_DIR, "..", "docker-compose.yml")


class BeatScheduleTests(SimpleTestCase):
    def entry(self):
        entries = [e for e in settings.CELERY_BEAT_SCHEDULE.values()
                   if e["task"] == "apps.videos.tasks.dispatch_scheduled_posts_batch"]
        self.assertEqual(len(entries), 1, "the dispatcher must be scheduled exactly once")
        return entries[0]

    def test_the_dispatcher_runs_every_five_minutes(self):
        self.assertEqual(self.entry()["schedule"], 5 * 60)

    def test_the_scheduled_task_is_registered_in_celery(self):
        celery_app.loader.import_default_modules()
        self.assertIn(self.entry()["task"], celery_app.tasks)

    def test_every_scheduled_task_is_registered_in_celery(self):
        celery_app.loader.import_default_modules()
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            self.assertIn(entry["task"], celery_app.tasks, name)

    def test_compose_runs_one_beat_process(self):
        if not os.path.isfile(COMPOSE):
            self.skipTest("docker-compose.yml is not next to this checkout")
        with open(COMPOSE, encoding="utf-8") as handle:
            text = handle.read()

        beat = re.search(r"^  beat:\n(?:    .*\n|\n)+", text, flags=re.M)
        self.assertIsNotNone(beat, "docker-compose.yml has no `beat` service")
        self.assertIn("celery -A backend beat", beat.group(0))
        self.assertEqual(len(re.findall(r"celery -A backend beat", text)), 1)

    def test_the_beat_schedule_file_stays_out_of_the_checkout(self):
        if not os.path.isfile(COMPOSE):
            self.skipTest("docker-compose.yml is not next to this checkout")
        with open(COMPOSE, encoding="utf-8") as handle:
            text = handle.read()

        self.assertIn("--schedule=/tmp/", text)


class DispatchScheduledPostsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="beat", email="beat@test.com", password="pw")
        workspace = Workspace.objects.create(name="Beat", owner=user)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="Beat", source_file="videos/raw/source.mp4",
        )
        self.clip = VideoClip.objects.create(
            project=project, title="c", start_time=0, end_time=5, status=VideoClip.Status.COMPLETED,
        )

    def post(self, minutes, status=ScheduledPost.Status.SCHEDULED):
        return ScheduledPost.objects.create(
            video_clip=self.clip, platform=ScheduledPost.Platform.TIKTOK, status=status,
            publish_at=timezone.now() + timedelta(minutes=minutes),
        )

    def run_batch(self):
        with patch(UPLOAD) as upload:
            dispatch_scheduled_posts_batch()
        return upload

    def test_a_post_due_within_the_hour_is_queued_with_its_publish_time_as_eta(self):
        post = self.post(20)

        upload = self.run_batch()

        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledPost.Status.QUEUED)
        upload.assert_called_once()
        self.assertEqual(upload.call_args.kwargs["eta"], post.publish_at)
        self.assertEqual(upload.call_args.kwargs["args"], [post.id])

    def test_a_post_beyond_the_hour_waits(self):
        post = self.post(180)

        upload = self.run_batch()

        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledPost.Status.SCHEDULED)
        upload.assert_not_called()

    def test_an_overdue_post_goes_out_at_once(self):
        post = self.post(-30)

        upload = self.run_batch()

        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledPost.Status.QUEUED)
        upload.assert_called_once()

    def test_only_scheduled_posts_are_dispatched(self):
        drafts = [self.post(10, ScheduledPost.Status.DRAFT), self.post(-10, ScheduledPost.Status.DRAFT)]
        others = [self.post(10, status) for status in (
            ScheduledPost.Status.QUEUED, ScheduledPost.Status.PROCESSING,
            ScheduledPost.Status.PUBLISHED, ScheduledPost.Status.FAILED,
        )]
        before = {post.id: post.status for post in drafts + others}

        upload = self.run_batch()

        upload.assert_not_called()
        for post in drafts + others:
            post.refresh_from_db()
            self.assertEqual(post.status, before[post.id])

    def test_two_runs_queue_a_post_once(self):
        self.post(5)

        with patch(UPLOAD) as upload:
            dispatch_scheduled_posts_batch()
            dispatch_scheduled_posts_batch()

        upload.assert_called_once()

    def test_several_posts_are_all_queued(self):
        posts = [self.post(1), self.post(30), self.post(-5)]

        upload = self.run_batch()

        self.assertEqual(upload.call_count, 3)
        for post in posts:
            post.refresh_from_db()
            self.assertEqual(post.status, ScheduledPost.Status.QUEUED)
