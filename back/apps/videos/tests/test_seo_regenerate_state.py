"""
Gap 8: what process_video_seo leaves for the API's `seo_status` (contract
`social.regeneratePostSeo` and `social.createPost`).

- A request of the API (`seo_requested_at`) ends when the text is stored or
  the generation fails: `seo_status` stops being "pending".
- "Regenerar" replaces the text the user wrote; the first generation does not.
- A DRAFT whose text fails stays a DRAFT (`seo_status: "failed"`), so the E1
  panel can still send it; a post that was going out fails as before.
- A post deleted before its generation ends the task quietly.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan
from apps.users.models import Workspace
from apps.videos.models import ScheduledPost, VideoProject
from apps.videos.services.ai.errors import AIAuthenticationError
from apps.videos.tests.test_seo_task import CAPTION, TAGS, SeoTaskHarness, usage

Status = ScheduledPost.Status


class SeoStateHarness(SeoTaskHarness):
    def setUp(self):
        super().setUp()
        self.project.metadata = {**self.project.metadata, "use_seo": True}
        self.project.save(update_fields=["metadata"])
        Workspace.objects.filter(pk=self.project.workspace_id).update(
            subscription_plan=SubscriptionPlan.objects.get(name="Pro"),
        )

    def set_post(self, **fields):
        ScheduledPost.objects.filter(pk=self.post.pk).update(**fields)
        self.post.refresh_from_db()

    def seo_status(self):
        api = APIClient()
        api.force_authenticate(self.project.workspace.owner)
        response = api.get(
            reverse("scheduled-post-detail", args=[self.post.pk]), HTTP_X_WORKSPACE_ID=str(self.project.workspace_id),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def fail_generation(self):
        error = AIAuthenticationError("bad key")
        error.usage_attempts = [usage(error_code="AIAuthenticationError", estimated_cost_usd=0.0)]
        self.generate.side_effect = error


class RequestStateTests(SeoStateHarness, TestCase):
    def test_a_stored_text_ends_the_request(self):
        self.set_post(status=Status.DRAFT, seo_requested_at=timezone.now())
        self.assertEqual(self.seo_status()["seo_status"], "pending")

        self.assertEqual(self.run_task(), "SEO Success")

        self.post.refresh_from_db()
        self.assertIsNone(self.post.seo_requested_at)
        self.assertEqual(self.seo_status()["seo_status"], "ready")

    def test_a_regeneration_replaces_the_text_the_user_wrote(self):
        self.set_post(
            status=Status.DRAFT, generated_caption="Viejo", generated_hashtags=["viejo"],
            caption="Lo escribí yo", hashtags=["mio"], seo_requested_at=timezone.now(),
        )
        body = self.seo_status()
        # Meanwhile the user's text is what is shown.
        self.assertEqual((body["seo_status"], body["caption"]), ("pending", "Lo escribí yo"))

        self.assertEqual(self.run_task(regenerate=True), "SEO Success")

        body = self.seo_status()
        self.assertEqual((body["seo_status"], body["caption"], body["hashtags"]), ("ready", CAPTION, TAGS))

    def test_the_first_generation_keeps_the_text_the_user_sent(self):
        self.set_post(status=Status.SCHEDULED, caption="Lo escribí yo", seo_requested_at=timezone.now())

        self.run_task()

        self.post.refresh_from_db()
        self.assertEqual((self.post.caption, self.post.generated_caption), ("Lo escribí yo", CAPTION))

    def test_a_failed_regeneration_keeps_the_text_and_ends_the_request(self):
        self.set_post(
            status=Status.SCHEDULED, generated_caption="Viejo", generated_hashtags=["viejo"],
            seo_requested_at=timezone.now(),
        )
        self.fail_generation()

        # Not retryable (a bad key): it fails at once.
        self.assertEqual(self.run_task(regenerate=True), "SEO Failed")

        self.post.refresh_from_db()
        self.assertEqual((self.post.status, self.post.generated_caption), (Status.SCHEDULED, "Viejo"))
        self.assertIsNone(self.post.seo_requested_at)
        self.assertEqual(self.seo_status()["seo_status"], "ready")


class DraftFailureTests(SeoStateHarness, TestCase):
    def test_a_draft_whose_text_fails_stays_a_draft(self):
        self.set_post(status=Status.DRAFT, seo_requested_at=timezone.now())
        self.fail_generation()

        self.assertEqual(self.run_task(), "SEO Failed")

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Status.DRAFT)
        self.assertEqual(self.post.error_log, "seo_generation_failed:AIAuthenticationError")
        self.assertIsNone(self.post.seo_requested_at)
        body = self.seo_status()
        self.assertEqual((body["status"], body["seo_status"], body["error_code"]), ("DRAFT", "failed", None))

    def test_a_post_that_was_going_out_still_fails(self):
        self.set_post(status=Status.SCHEDULED)
        self.fail_generation()

        self.run_task()

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, Status.FAILED)

    def test_a_post_with_the_users_own_caption_keeps_its_status(self):
        # A clip without audio: the AI cannot write it, but the user already did.
        VideoProject.objects.filter(pk=self.project.pk).update(transcript_data=None)
        self.set_post(status=Status.SCHEDULED, caption="Lo escribí yo", seo_requested_at=timezone.now())

        self.assertEqual(self.run_task(), "No Transcript")

        self.post.refresh_from_db()
        self.assertEqual((self.post.status, self.post.caption), (Status.SCHEDULED, "Lo escribí yo"))
        body = self.seo_status()
        self.assertEqual((body["status"], body["seo_status"], body["error_code"]), ("SCHEDULED", "failed", None))

    def test_a_later_text_clears_the_failure_of_the_draft(self):
        self.set_post(status=Status.DRAFT, error_log="seo_no_clip_transcript", seo_requested_at=None)
        self.assertEqual(self.seo_status()["seo_status"], "failed")

        self.ask()  # «Regenerar»
        self.assertEqual(self.run_task(regenerate=True), "SEO Success")

        self.post.refresh_from_db()
        self.assertEqual((self.post.status, self.post.error_log), (Status.DRAFT, None))
        self.assertEqual(self.seo_status()["seo_status"], "ready")


class DeletedPostTests(SeoStateHarness, TestCase):
    def test_a_post_deleted_before_its_generation_ends_the_task_quietly(self):
        post_id = self.post.pk
        ScheduledPost.objects.filter(pk=post_id).delete()

        result = self.run_task()

        self.assertEqual(result, "Post Deleted")
        self.generate.assert_not_called()
        self.assertFalse(ScheduledPost.objects.filter(pk=post_id).exists())

    def test_a_post_deleted_while_waiting_for_the_lock_too(self):
        real_refresh = ScheduledPost.refresh_from_db

        def deleted_meanwhile(post, *args, **kwargs):
            ScheduledPost.objects.filter(pk=post.pk).delete()
            return real_refresh(post, *args, **kwargs)

        with patch.object(ScheduledPost, "refresh_from_db", deleted_meanwhile):
            self.assertEqual(self.run_task(), "Post Deleted")
        self.generate.assert_not_called()


class StaleRequestTests(SeoStateHarness, TestCase):
    def test_a_request_older_than_its_window_is_not_pending(self):
        from apps.videos.services.scheduled_posts import seo_request_window

        self.set_post(
            status=Status.DRAFT, generated_caption="Viejo", generated_hashtags=["viejo"],
            seo_requested_at=timezone.now() - seo_request_window() - timedelta(seconds=1),
        )
        self.assertEqual(self.seo_status()["seo_status"], "ready")


class QueueMarksTheRequestTests(SeoStateHarness, TestCase):
    def test_every_way_of_asking_marks_the_request(self):
        """The signal, the admin action and the API all go through queue_post_seo."""
        from apps.videos.signals import queue_post_seo

        self.set_post(status=Status.DRAFT, generated_caption="Viejo", generated_hashtags=["v"])
        with patch("apps.videos.tasks.process_video_seo.apply_async"):
            queue_post_seo(self.post.pk, regenerate=True)
        self.post.refresh_from_db()
        self.assertIsNotNone(self.post.seo_requested_at)
        self.assertEqual(self.seo_status()["seo_status"], "pending")

    def test_a_post_created_already_failed_is_not_generated(self):
        with patch("apps.videos.tasks.process_video_seo.delay") as delay, self.captureOnCommitCallbacks(execute=True):
            post = ScheduledPost.objects.create(
                video_clip=self.clip, platform="TIKTOK", publish_at=timezone.now(), status=Status.FAILED,
                error_log="seo_no_clip_transcript",
            )
        delay.assert_not_called()
        post.refresh_from_db()
        self.assertIsNone(post.seo_requested_at)
