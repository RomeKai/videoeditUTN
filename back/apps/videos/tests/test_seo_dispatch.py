"""SEO dispatch (ADR-013): a new ScheduledPost of a project that asked for SEO queues process_video_seo."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan, Wallet
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoClip, VideoProject

User = get_user_model()

DELAY = "apps.videos.tasks.process_video_seo.delay"
APPLY_ASYNC = "apps.videos.tasks.process_video_seo.apply_async"


class SeoDispatchHarness(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seo-dispatch", email="sd@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="SEO dispatch", owner=self.user)

    def make_clip(self, metadata=None):
        project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="P", metadata=metadata or {},
        )
        return VideoClip.objects.create(project=project, title="C", start_time=0.0, end_time=20.0)

    def make_post(self, clip, **fields):
        values = dict(
            video_clip=clip, platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z",
        )
        values.update(fields)
        return ScheduledPost.objects.create(**values)

    def set_plan(self, has_seo):
        plan = SubscriptionPlan.objects.create(
            name=f"plan-{has_seo}", has_seo_optimization=has_seo, max_video_duration_seconds=3600,
        )
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=plan)
        self.workspace.refresh_from_db()


class NewPostDispatchTests(SeoDispatchHarness):
    def test_new_post_of_a_seo_project_queues_the_generation_after_commit(self):
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY) as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                post = self.make_post(clip)
            delay.assert_not_called()  # never before the post is committed
            for callback in callbacks:
                callback()
        delay.assert_called_once_with(str(post.pk))

    def test_project_without_seo_is_never_sent_to_the_llm(self):
        for metadata in ({}, {"use_seo": False}, {"use_seo": "true"}):
            clip = self.make_clip(metadata)
            with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
                self.make_post(clip)
            delay.assert_not_called()

    def test_plan_without_seo_is_not_dispatched(self):
        self.set_plan(has_seo=False)
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            self.make_post(clip)
        delay.assert_not_called()

    def test_plan_with_seo_is_dispatched(self):
        self.set_plan(has_seo=True)
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            self.make_post(clip)
        delay.assert_called_once()

    def test_each_post_gets_its_own_generation(self):
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            tiktok = self.make_post(clip)
            reels = self.make_post(clip, platform=ScheduledPost.Platform.INSTAGRAM_REELS)
        self.assertEqual(
            [c.args for c in delay.call_args_list], [(str(tiktok.pk),), (str(reels.pk),)],
        )

    def test_editing_a_post_never_regenerates(self):
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY):
            with self.captureOnCommitCallbacks(execute=True):
                post = self.make_post(clip)
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            post.status = ScheduledPost.Status.SCHEDULED
            post.save()
        delay.assert_not_called()

    def test_post_created_with_its_metadata_is_not_regenerated(self):
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            self.make_post(clip, generated_caption="Hecho a mano", generated_hashtags=["a"] * 5)
        delay.assert_not_called()

    def test_post_already_publishing_or_published_is_skipped(self):
        clip = self.make_clip({"use_seo": True})
        for status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED):
            with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
                self.make_post(clip, status=status)
            delay.assert_not_called()

    def test_rolled_back_post_is_never_dispatched(self):
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY) as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                try:
                    from django.db import transaction

                    with transaction.atomic():
                        self.make_post(clip)
                        raise RuntimeError("rollback")
                except RuntimeError:
                    pass
            self.assertEqual(callbacks, [])
        delay.assert_not_called()

    def test_broker_error_is_logged_and_keeps_the_post(self):
        clip = self.make_clip({"use_seo": True})
        with patch(DELAY, side_effect=ConnectionError("broker down")):
            with self.assertLogs("apps.videos.signals", level="ERROR") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    post = self.make_post(clip)
        self.assertTrue(ScheduledPost.objects.filter(pk=post.pk).exists())
        self.assertIn("seo.dispatch_failed", logs.output[0])
        self.assertIn("ConnectionError", logs.output[0])
        self.assertNotIn("broker down", logs.output[0])

    def test_fixtures_never_call_the_provider(self):
        clip = self.make_clip({"use_seo": True})
        post = self.make_post(clip, generated_caption="x", generated_hashtags=["a"] * 5)
        post.refresh_from_db()
        from django.core import serializers

        data = serializers.serialize("json", [post])
        ScheduledPost.objects.filter(pk=post.pk).delete()
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fixture:
            fixture.write(data.replace('"generated_caption": "x"', '"generated_caption": null'))
        self.addCleanup(os.unlink, fixture.name)
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            call_command("loaddata", fixture.name, verbosity=0)
        self.assertTrue(ScheduledPost.objects.filter(pk=post.pk).exists())
        delay.assert_not_called()


class AdminRegenerateTests(SeoDispatchHarness):
    def setUp(self):
        super().setUp()
        admin = User.objects.create_superuser(username="admin-seo", email="as@test.com", password="pw")
        self.client.force_login(admin)

    def test_action_regenerates_only_posts_not_yet_publishing(self):
        clip = self.make_clip()
        draft = self.make_post(clip)
        published = self.make_post(clip, status=ScheduledPost.Status.PUBLISHED)
        with patch(APPLY_ASYNC) as apply_async, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:videos_scheduledpost_changelist"),
                {"action": "regenerate_ai_metadata", "_selected_action": [draft.pk, published.pk]},
            )
        self.assertEqual(response.status_code, 302)
        apply_async.assert_called_once_with(args=[str(draft.pk)], kwargs={"regenerate": True})


class CreateWithSeoTests(TestCase):
    """use_seo is accepted at creation, stored in metadata and charged by the pricing."""

    def setUp(self):
        self.user = User.objects.create_user(username="seo-api", email="sa@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="W", owner=self.user).members.add(self.user)
        for workspace in self.user.workspaces.all():
            wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
            Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("100.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def create(self, **payload):
        body = {"title": "Remoto", "video_url": "https://www.youtube.com/watch?v=x"}
        body.update(payload)
        with patch("apps.videos.views.process_initial_ingestion.delay"):
            return self.client.post(reverse("project-list"), body, format="json")

    def test_use_seo_is_stored_in_metadata(self):
        response = self.create(use_seo=True)
        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertIs(project.metadata["use_seo"], True)
        self.assertNotIn("use_seo", response.data)  # write-only

    def test_without_use_seo_nothing_is_stored(self):
        response = self.create()
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertNotIn("use_seo", project.metadata)

    def test_use_seo_cannot_be_turned_on_after_creation(self):
        response = self.create()
        project_id = response.data["id"]
        self.client.patch(
            reverse("project-detail", args=[project_id]), {"use_seo": True}, format="json",
        )
        self.assertNotIn("use_seo", VideoProject.objects.get(pk=project_id).metadata)

    def test_upload_with_seo_on_a_plan_without_it_is_rejected_by_the_pricing(self):
        import tempfile
        from unittest.mock import MagicMock

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        plan = SubscriptionPlan.objects.create(
            name="sin-seo", has_seo_optimization=False, max_video_duration_seconds=3600,
        )
        Workspace.objects.filter(members=self.user).update(subscription_plan=plan)
        clip = MagicMock()
        clip.__enter__.return_value.duration = 30
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("moviepy.VideoFileClip", return_value=clip), \
                patch("apps.videos.views.process_initial_ingestion.delay") as ingest:
            response = self.client.post(reverse("project-list"), {
                "title": "Subido", "use_seo": "true",
                "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
            }, format="multipart")
        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertFalse(VideoProject.objects.filter(title="Subido").exists())
        ingest.assert_not_called()
