"""AICORE-17 (#69): social metadata per ScheduledPost (process_video_seo), ADR-013."""

import json
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from celery.exceptions import Retry
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.security import ModerationUnavailableError, UnsafeContentError
from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, ScheduledPost, VideoClip, VideoProject
from apps.videos.services.ai.contracts import AIExecutionResult, ProviderUsage
from apps.videos.services.ai.errors import AIAuthenticationError, AIServerError
from apps.videos.services.seo_engine import SocialMetadata
from apps.videos.tasks import process_video_seo
from apps.videos.tests.fakes import CapturedTaskFailures, FakeRedis

User = get_user_model()

CHECK = "apps.core.security.AI_Security_Shield.check_content_safety"
SERVICE = "apps.videos.services.seo_engine.SEOOptimizationService"
REDIS_CLIENT = "apps.videos.services.ai.project_lock.get_redis_client"
OUTSIDE = "FUERA-DEL-CLIP"
CAPTION = "SECRET-CAPTION-X"
TAGS = ["Gaming", "Clips", "Humor", "Twitch", "Viral"]


def usage(role="primary", model="gemini/gemini-3.8-flash", **extra):
    values = dict(
        provider="gemini", model=model, role=role, prompt_tokens=300, completion_tokens=80,
        total_tokens=380, estimated_cost_usd=0.0004,
    )
    values.update(extra)
    return ProviderUsage(**values)


def generated(caption=CAPTION, hashtags=TAGS, attempts=None):
    attempts = attempts or [usage()]
    return AIExecutionResult(
        data=SocialMetadata(caption=caption, hashtags=hashtags), usage=attempts[-1], attempts=attempts,
    )


class SeoTaskHarness(CapturedTaskFailures):
    def setUp(self):
        super().setUp()
        user = User.objects.create_user(username="seo-task", email="st@test.com", password="pw")
        workspace = Workspace.objects.create(name="SEO task", owner=user)
        self.project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="P",
            metadata={"full_text": f"todo el video {OUTSIDE}", "niche": "gaming"},
            transcript_data=[
                {"start": 1.0, "end": 1.5, "text": OUTSIDE},
                {"start": 10.0, "end": 10.5, "text": "le"},
                {"start": 10.6, "end": 11.0, "text": "ganamos"},
                {"start": 40.0, "end": 40.5, "text": OUTSIDE},
            ],
        )
        self.clip = VideoClip.objects.create(
            project=self.project, title="La remontada", start_time=10.0, end_time=25.0
        )
        self.post = ScheduledPost.objects.create(
            video_clip=self.clip, platform=ScheduledPost.Platform.INSTAGRAM_REELS,
            publish_at="2030-01-01T00:00:00Z", status=ScheduledPost.Status.QUEUED,
            seo_requested_at=timezone.now(),  # asked for, as queue_post_seo leaves every post whose message runs
        )
        self.redis = FakeRedis()
        self.check = self._patch(CHECK)
        self._patch(REDIS_CLIENT, return_value=self.redis)
        self.service = self._patch(SERVICE)
        self.generate = self.service.return_value.generate_metadata
        self.generate.return_value = generated()

    def _patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def ask(self):
        """A new request, as «Regenerar» (signals.queue_post_seo) stamps it before its message."""
        ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=timezone.now())

    def run_task(self, retries=0, **kwargs):
        process_video_seo.push_request(retries=retries)
        try:
            return process_video_seo.run(str(self.post.id), **kwargs)
        finally:
            process_video_seo.pop_request()

    def seo_rows(self):
        return AIUsageRecord.objects.filter(project=self.project, stage=AIUsageRecord.Stage.SEO)


class GenerationTests(SeoTaskHarness, TestCase):
    def test_success_stores_caption_and_hashtags_apart_and_records_usage(self):
        result = self.run_task()

        self.post.refresh_from_db()
        self.assertEqual(result, "SEO Success")
        self.assertEqual(self.post.generated_caption, CAPTION)
        self.assertEqual(self.post.generated_hashtags, TAGS)
        row = self.seo_rows().get()
        self.assertEqual((row.role, row.total_tokens, row.success), ("primary", 380, True))
        self.assertEqual(row.pipeline_version, AIUsageRecord.PipelineVersion.V2)

    def test_the_llm_receives_only_the_clip_transcript_and_the_post_platform(self):
        self.run_task()

        sent = self.generate.call_args.args[0]
        self.assertEqual(sent.clip_transcript, "le ganamos")
        self.assertNotIn(OUTSIDE, sent.clip_transcript)
        self.assertEqual(sent.platform, "INSTAGRAM_REELS")
        self.assertEqual(sent.clip_title, "La remontada")
        self.assertEqual(sent.target_niche, "gaming")

    def test_a_fallback_answer_writes_one_row_per_attempt(self):
        failed = usage(error_code="AIServerError", estimated_cost_usd=0.0)
        answered = usage(role="fallback", model="gemini/gemini-3.5-flash-lite")
        self.generate.return_value = generated(attempts=[failed, answered])

        self.run_task()

        self.assertEqual(sorted(self.seo_rows().values_list("role", flat=True)), ["fallback", "primary"])

    def test_without_a_timed_clip_transcript_the_project_transcript_is_never_sent(self):
        VideoProject.objects.filter(pk=self.project.pk).update(transcript_data=None)

        result = self.run_task()

        self.generate.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(result, "No Transcript")
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "seo_no_clip_transcript")


class IdempotencyTests(SeoTaskHarness, TestCase):
    def test_a_post_with_metadata_is_not_sent_to_the_llm_again(self):
        self.run_task()
        self.generate.reset_mock()

        result = self.run_task()

        self.assertEqual(result, "SEO Already Generated")
        self.generate.assert_not_called()
        self.assertEqual(self.seo_rows().count(), 1)

    def test_an_explicit_regeneration_replaces_it(self):
        self.run_task()
        self.generate.return_value = generated(caption="otra versión")
        self.ask()

        self.run_task(regenerate=True)

        self.post.refresh_from_db()
        self.assertEqual(self.post.generated_caption, "otra versión")
        self.assertEqual(self.generate.call_count, 2)

    def test_a_post_being_generated_by_another_worker_is_skipped(self):
        self.redis.lock(f"lock:seo:post:{self.post.id}").acquire()

        result = self.run_task()

        self.assertEqual(result, "SEO Busy")
        self.generate.assert_not_called()

    def test_redis_down_is_retried_and_nothing_is_called(self):
        from apps.videos.tests.fakes import DownRedis

        with patch(REDIS_CLIENT, return_value=DownRedis()), patch.object(
            process_video_seo, "retry", side_effect=Retry()
        ) as retry:
            with self.assertRaises(Retry):
                self.run_task()

        self.generate.assert_not_called()
        self.assertIn("countdown", retry.call_args.kwargs)


class ModerationTests(SeoTaskHarness, TestCase):
    def test_the_clip_transcript_and_title_are_moderated_before_the_call(self):
        self.check.side_effect = [UnsafeContentError("x")]

        result = self.run_task()

        self.assertEqual(result, "Security Abort")
        payload = self.check.call_args.args[0]
        self.assertIn("le ganamos", payload)
        self.assertIn("La remontada", payload)
        self.assertNotIn(OUTSIDE, payload)
        self.generate.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(self.post.error_log, "content_blocked")

    def test_blocked_generated_text_is_never_stored(self):
        self.check.side_effect = [None, UnsafeContentError("x")]

        result = self.run_task()

        self.assertEqual(result, "Security Abort")
        self.assertIn(CAPTION, self.check.call_args.args[0])  # the output was moderated
        self.post.refresh_from_db()
        self.assertIsNone(self.post.generated_caption)
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "generated_content_blocked")
        self.assertEqual(self.seo_rows().count(), 1)  # the call was paid: still recorded

    def test_a_moderation_retry_after_generating_does_not_pay_again(self):
        self.check.side_effect = [None, ModerationUnavailableError()]
        with patch.object(process_video_seo, "retry", side_effect=Retry()):
            with self.assertRaises(Retry):
                self.run_task()
        self.post.refresh_from_db()
        self.assertIsNone(self.post.generated_caption)

        self.check.side_effect = None
        result = self.run_task(retries=1)

        self.assertEqual(result, "SEO Success")
        self.generate.assert_called_once()  # the pending generation was reused
        self.post.refresh_from_db()
        self.assertEqual(self.post.generated_caption, CAPTION)
        self.assertEqual(self.redis.values, {})


class FailureTests(SeoTaskHarness, TestCase):
    def test_a_non_retryable_error_fails_the_post_at_once_with_a_static_code(self):
        error = AIAuthenticationError("bad key")
        error.usage_attempts = [usage(error_code="AIAuthenticationError", estimated_cost_usd=0.0)]
        self.generate.side_effect = error

        with patch.object(process_video_seo, "retry") as retry:
            result = self.run_task()

        retry.assert_not_called()
        self.assertEqual(result, "SEO Failed")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "seo_generation_failed:AIAuthenticationError")
        row = self.seo_rows().get()
        self.assertFalse(row.success)

    def test_a_retryable_error_backs_off_then_fails_when_retries_run_out(self):
        self.generate.side_effect = AIServerError("down")

        with patch.object(process_video_seo, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                self.run_task(retries=0)
        countdown = retry.call_args.kwargs["countdown"]
        self.assertGreaterEqual(countdown, 15)
        self.assertLessEqual(countdown, 30)

        with patch.object(process_video_seo, "retry") as retry:
            result = self.run_task(retries=process_video_seo.max_retries)
        retry.assert_not_called()
        self.assertEqual(result, "SEO Failed")
        self.post.refresh_from_db()
        self.assertEqual(self.post.error_log, "seo_generation_failed:AIServerError")

    def test_logs_carry_no_caption_title_or_transcript(self):
        self.check.side_effect = [None, UnsafeContentError(CAPTION)]
        with self.assertLogs("apps.videos", level="DEBUG") as logs:
            self.run_task()

        output = "\n".join(logs.output)
        for secret in (CAPTION, "La remontada", "ganamos", OUTSIDE):
            self.assertNotIn(secret, output)


class PublishCaptionTests(TestCase):
    def test_the_publisher_adds_the_hashtags_to_the_caption(self):
        from apps.videos.tasks import upload_to_social_network

        user = User.objects.create_user(username="pub", email="pub@test.com", password="pw")
        from apps.integrations.tests.helpers import PUBLISH, PUBLISHED, give_social_profile

        workspace = Workspace.objects.create(name="Pub", owner=user)
        give_social_profile(workspace, "k")
        project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="P")
        clip = VideoClip.objects.create(
            project=project, title="c", start_time=0, end_time=5, s3_object_key="c.mp4"
        )
        post = ScheduledPost.objects.create(
            video_clip=clip, platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z", status=ScheduledPost.Status.QUEUED,
            generated_caption="Hola", generated_hashtags=["a1", "b2"], tiktok={"privacy_level": "SELF_ONLY"},
        )
        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://signed.test/x",
        ), patch(
            PUBLISH, return_value=PUBLISHED
        ) as send:
            upload_to_social_network.apply(args=[str(post.id)])

        self.assertEqual(send.call_args.args[1].text, "Hola\n\n#a1 #b2")


class ExportTests(TestCase):
    def test_seo_rows_get_their_own_columns_and_stay_out_of_the_ingestion_cost(self):
        user = User.objects.create_user(username="exp", email="exp@test.com", password="pw")
        workspace = Workspace.objects.create(name="Exp", owner=user)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="P", metadata={"duration": 1800}
        )
        for stage, cost in (("selection", "0.010000"), ("seo", "0.000400"), ("seo", "0.000300")):
            AIUsageRecord.objects.create(
                project=project, stage=stage, pipeline_version="v2", attempt=1, role="primary",
                provider="gemini", model="gemini-3.8-flash", success=True, latency_seconds=0.5,
                total_tokens=100, estimated_cost_usd=Decimal(cost),
            )
        out = StringIO()
        call_command(
            "export_ai_pilot_metrics", "--since=2000-01-01", "--level=project", "--format=jsonl",
            stdout=out,
        )

        row = json.loads(out.getvalue().strip())
        self.assertEqual(row["records_count"], 1)
        self.assertEqual(Decimal(row["total_cost_usd"]), Decimal("0.010000"))
        self.assertEqual(row["total_tokens"], 100)
        self.assertEqual(row["seo_records"], 2)
        self.assertEqual(Decimal(row["seo_cost_usd"]), Decimal("0.000700"))

        record_out = StringIO()
        call_command(
            "export_ai_pilot_metrics", "--since=2000-01-01", "--level=record", "--format=jsonl",
            stdout=record_out,
        )
        stages = [json.loads(line)["stage"] for line in record_out.getvalue().splitlines()]
        self.assertEqual(sorted(stages), ["selection", "seo", "seo"])


class ReviewRegressionTests(SeoTaskHarness, TestCase):
    """Edge cases found in review (AICORE-17)."""

    def test_a_regeneration_survives_a_capacity_wait(self):
        from apps.videos.tests.test_capacity_pipeline import LIMITS, FakeCapacityStore

        ScheduledPost.objects.filter(pk=self.post.pk).update(
            generated_caption="OLD", generated_hashtags=TAGS
        )
        store = FakeCapacityStore()
        store.denials = [5.0]
        requeue = self._patch("apps.videos.tasks.process_video_seo.apply_async")
        self._patch("apps.videos.services.ai.capacity.default_store", return_value=store)
        with self.settings(AI_CAPACITY_ENABLED=True, AI_CAPACITY_LIMITS=LIMITS):
            self.assertEqual(self.run_task(regenerate=True), "Waiting for capacity")
            kwargs = requeue.call_args.kwargs["kwargs"]
            self.assertTrue(kwargs["regenerate"])

            result = self.run_task(retries=0, **kwargs)  # what the re-queued message runs

        self.assertEqual(result, "SEO Success")
        self.post.refresh_from_db()
        self.assertEqual(self.post.generated_caption, CAPTION)

    def test_a_final_seo_failure_never_touches_a_post_that_is_publishing_or_published(self):
        from apps.videos.tasks import _fail_seo_post

        for status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED):
            with self.subTest(status=status):
                ScheduledPost.objects.filter(pk=self.post.pk).update(
                    status=status, error_log="Ayrshare ID: 123"
                )
                _fail_seo_post(self.post.id, "seo_generation_failed:AIAuthenticationError")
                self.post.refresh_from_db()
                self.assertEqual((self.post.status, self.post.error_log), (status, "Ayrshare ID: 123"))

    def test_a_successful_run_recovers_a_post_failed_by_seo_as_a_draft(self):
        ScheduledPost.objects.filter(pk=self.post.pk).update(
            status=ScheduledPost.Status.FAILED, error_log="moderation_unavailable"
        )

        self.run_task()

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.DRAFT)  # never re-scheduled alone
        self.assertIsNone(self.post.error_log)
        self.assertEqual(self.post.generated_caption, CAPTION)

    def test_a_publishing_failure_is_not_cleared_by_seo(self):
        ScheduledPost.objects.filter(pk=self.post.pk).update(
            status=ScheduledPost.Status.FAILED, error_log="Critical: something"
        )

        self.run_task()

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "Critical: something")

    def test_metadata_stored_by_another_run_before_the_lock_is_not_paid_again(self):
        from contextlib import contextmanager

        @contextmanager
        def lock_after_another_run_finished(post_id, served=None):
            ScheduledPost.objects.filter(pk=post_id).update(
                generated_caption="otro worker", generated_hashtags=TAGS
            )
            yield True

        with patch("apps.videos.tasks.social._seo_post_lock", lock_after_another_run_finished):
            result = self.run_task()

        self.assertEqual(result, "SEO Already Generated")
        self.generate.assert_not_called()

    def test_the_shared_schema_is_never_handed_to_litellm(self):
        from apps.videos.services.seo_engine import _SOCIAL_METADATA_SCHEMA, SEOOptimizationService

        payload = SEOOptimizationService._response_format("gemini/gemini-3.8-flash")
        payload["response_schema"].pop("additionalProperties")  # what LiteLLM does for Gemini

        self.assertIn("additionalProperties", _SOCIAL_METADATA_SCHEMA)

    def test_the_export_flags_unpriced_seo_rows(self):
        AIUsageRecord.objects.create(
            project=self.project, stage="seo", pipeline_version="v2", attempt=1, role="primary",
            provider="gemini", model="m", success=False, latency_seconds=1, total_tokens=0,
            estimated_cost_usd=None,
        )
        out = StringIO()
        call_command(
            "export_ai_pilot_metrics", "--since=2000-01-01", "--level=project", "--format=jsonl",
            stdout=out,
        )
        row = json.loads(out.getvalue().strip())
        self.assertEqual(row["seo_records"], 1)
        self.assertFalse(row["seo_cost_complete"])


class DispatchReviewRegressionTests(SeoTaskHarness, TestCase):
    """Edge cases found in the review of the SEO dispatch."""

    def test_a_failed_regeneration_keeps_the_caption_and_the_post_publishable(self):
        ScheduledPost.objects.filter(pk=self.post.pk).update(
            status=ScheduledPost.Status.SCHEDULED, generated_caption="OLD", generated_hashtags=TAGS,
        )
        error = AIAuthenticationError("bad key")
        error.usage_attempts = [usage(error_code="AIAuthenticationError", estimated_cost_usd=0.0)]
        self.generate.side_effect = error

        with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            result = self.run_task(regenerate=True)

        self.assertEqual(result, "SEO Failed")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.SCHEDULED)
        self.assertEqual((self.post.generated_caption, self.post.generated_hashtags), ("OLD", TAGS))
        self.assertIsNone(self.post.error_log)
        self.assertTrue(any("seo.regenerate_failed" in line for line in logs.output))

    def test_a_blocked_regeneration_keeps_the_previous_caption(self):
        ScheduledPost.objects.filter(pk=self.post.pk).update(
            status=ScheduledPost.Status.SCHEDULED, generated_caption="OLD", generated_hashtags=TAGS,
        )
        self.check.side_effect = [None, UnsafeContentError("x")]  # the new output is blocked

        self.assertEqual(self.run_task(regenerate=True), "Security Abort")

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.SCHEDULED)
        self.assertEqual(self.post.generated_caption, "OLD")

    def test_a_regeneration_never_touches_a_post_that_is_publishing_or_published(self):
        for status in (ScheduledPost.Status.PROCESSING, ScheduledPost.Status.PUBLISHED):
            with self.subTest(status=status):
                # Each one with its own request (the one before ended the previous one).
                ScheduledPost.objects.filter(pk=self.post.pk).update(
                    status=status, generated_caption="OLD", generated_hashtags=TAGS, seo_requested_at=timezone.now(),
                )

                with self.assertLogs("apps.videos.tasks", level="INFO") as logs:
                    self.assertEqual(self.run_task(regenerate=True), "SEO Skipped")

                # The frozen status stopped it (and ended its request), not a missing request.
                self.assertIn(f"reason={status}", "\n".join(logs.output))
                self.post.refresh_from_db()
                self.assertEqual(
                    (self.post.status, self.post.generated_caption, self.post.seo_requested_at), (status, "OLD", None),
                )
        self.generate.assert_not_called()

    def test_publishing_that_starts_during_the_generation_keeps_its_caption(self):
        def publishing_starts(_request):
            ScheduledPost.objects.filter(pk=self.post.pk).update(status=ScheduledPost.Status.PROCESSING)
            return generated()

        self.generate.side_effect = publishing_starts

        self.assertEqual(self.run_task(), "SEO Skipped")

        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ScheduledPost.Status.PROCESSING)
        self.assertIsNone(self.post.generated_caption)
        self.assertIsNone(self.redis.get(f"seo:pending:{self.post.id}"))  # not reused later


class RegenerarWhileItFailsTests(SeoTaskHarness, TestCase):
    """
    Review of #194: a «Regenerar» that lands while an older run fails is a newer request. The run's
    failure ended it too (the new run had found the lock taken, «SEO Busy»): the click was lost and the
    post showed failed. A run now ends only the request it served.
    """

    def setUp(self):
        super().setUp()
        self.served = timezone.now() - timedelta(minutes=2)
        ScheduledPost.objects.filter(pk=self.post.pk).update(
            status=ScheduledPost.Status.SCHEDULED, seo_requested_at=self.served,
        )

    def regenerar_meanwhile(self):
        """A «Regenerar» lands right before _fail_seo_post writes (the race of LostAiTextRacesTests)."""
        from apps.videos.tasks import social as social_tasks

        real = social_tasks._fail_seo_post
        self.asked = timezone.now()

        def racing(post_id, code, **kwargs):
            ScheduledPost.objects.filter(pk=post_id).update(seo_requested_at=self.asked)
            return real(post_id, code, **kwargs)

        return patch.object(social_tasks, "_fail_seo_post", racing)

    @contextmanager
    def failing(self, name):
        """The run fails one way (every _fail_seo_post of process_video_seo), only inside the block."""
        from apps.videos.tests.test_capacity_pipeline import LIMITS, FakeCapacityStore

        def side_effect(mock, effect):
            mock.side_effect = effect
            stack.callback(setattr, mock, "side_effect", None)

        with ExitStack() as stack:
            if name == "generation":
                error = AIAuthenticationError("bad key")
                error.usage_attempts = [usage(error_code="AIAuthenticationError", estimated_cost_usd=0.0)]
                side_effect(self.generate, error)
            elif name == "moderation":
                side_effect(self.check, ModerationUnavailableError())
            elif name == "input_blocked":
                side_effect(self.check, [UnsafeContentError("x")])
            elif name == "output_blocked":
                side_effect(self.check, [None, UnsafeContentError("x")])
            elif name == "no_transcript":
                VideoProject.objects.filter(pk=self.project.pk).update(transcript_data=None)
            elif name == "capacity_timeout":
                store = FakeCapacityStore()
                store.denials = [5.0]
                stack.enter_context(patch("apps.videos.services.ai.capacity.default_store", return_value=store))
                stack.enter_context(self.settings(AI_CAPACITY_ENABLED=True, AI_CAPACITY_LIMITS=LIMITS))
            elif name == "unexpected":
                side_effect(self.generate, ValueError("x"))
            yield

    # Each failure: what the run answers (or raises), and the task's arguments for it.
    FAILURES = {
        "generation": ("SEO Failed", {}),
        "moderation": ("Moderation Failed", {"retries": process_video_seo.max_retries}),
        "input_blocked": ("Security Abort", {}),
        "output_blocked": ("Security Abort", {}),
        "no_transcript": ("No Transcript", {}),
        "capacity_timeout": ("Capacity Timeout", {"capacity_waited_s": settings.AI_CAPACITY_MAX_WAIT_SECONDS}),
        "unexpected": (ValueError, {}),
    }

    def test_a_regenerar_that_lands_while_it_fails_is_not_ended(self):
        for name, (answer, kwargs) in self.FAILURES.items():
            with self.subTest(name):
                ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=self.served)
                VideoProject.objects.filter(pk=self.project.pk).update(transcript_data=self.project.transcript_data)

                with self.failing(name), self.regenerar_meanwhile():
                    if isinstance(answer, type):
                        with self.assertRaises(answer):
                            self.run_task(**kwargs)
                    else:
                        self.assertEqual(self.run_task(**kwargs), answer)

                self.post.refresh_from_db()
                # Still waiting for the new text, as the «Regenerar» left it.
                self.assertEqual(
                    (self.post.status, self.post.error_log, self.post.seo_requested_at),
                    (ScheduledPost.Status.SCHEDULED, None, self.asked),
                )

    def test_a_caller_that_served_no_request_does_not_end_a_new_one(self):
        # cutoff=None: the caller served none (a send whose post is gone). Only a post with no request
        # takes the failure; a «Regenerar» that landed meanwhile stays on its way.
        from apps.videos.tasks.social import _fail_seo_post

        asked = timezone.now()
        ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=asked)
        self.assertFalse(_fail_seo_post(self.post.pk, "seo_failed:ValueError", cutoff=None))
        self.post.refresh_from_db()
        self.assertEqual(
            (self.post.status, self.post.error_log, self.post.seo_requested_at),
            (ScheduledPost.Status.SCHEDULED, None, asked),
        )

        ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=None)
        self.assertTrue(_fail_seo_post(self.post.pk, "seo_failed:ValueError", cutoff=None))
        self.post.refresh_from_db()
        self.assertEqual((self.post.status, self.post.error_log), (ScheduledPost.Status.FAILED, "seo_failed:ValueError"))

    def test_with_no_newer_request_it_ends_the_one_it_served(self):
        self.generate.side_effect = ValueError("x")

        with self.assertRaises(ValueError):
            self.run_task()

        self.post.refresh_from_db()
        self.assertEqual(
            (self.post.status, self.post.error_log, self.post.seo_requested_at),
            (ScheduledPost.Status.FAILED, "seo_failed:ValueError", None),
        )


class RegenerarWhileAnotherRunHoldsTheLockTests(SeoTaskHarness, TestCase):
    """
    Second review of #198: the message of a «Regenerar» that lands while an older run holds the lock
    found it taken and stopped («SEO Busy»): the request sat `pending` until its window passed, with
    «Regenerar» refused. A newer request now waits for the lock, and an older run that succeeds ends
    only the request it served, so the newer one is not absorbed.
    """

    def setUp(self):
        super().setUp()
        self.served = timezone.now() - timedelta(minutes=2)
        ScheduledPost.objects.filter(pk=self.post.pk).update(
            status=ScheduledPost.Status.SCHEDULED, seo_requested_at=self.served,
        )
        self.requeue = self._patch("apps.videos.tasks.process_video_seo.apply_async")
        self.answers = []

    def regenerar_lands(self):
        """A «Regenerar» lands and its message runs, while the run that called this holds the lock."""
        self.asked = timezone.now()
        ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=self.asked)
        self.answers.append(self.run_task(regenerate=True))

    def held_by_a_run_serving(self, served):
        self.redis.lock(f"lock:seo:post:{self.post.id}").acquire(token=f"otro|{served.isoformat()}")

    def state(self):
        self.post.refresh_from_db()
        return self.post.status, self.post.error_log, self.post.seo_requested_at

    def test_a_regenerar_while_the_old_run_fails_runs_after_it(self):
        def regenerar_then_the_old_call_fails(_request):
            self.regenerar_lands()
            error = AIAuthenticationError("bad key")
            error.usage_attempts = [usage(error_code="AIAuthenticationError", estimated_cost_usd=0.0)]
            raise error

        self.generate.side_effect = regenerar_then_the_old_call_fails

        self.assertEqual(self.run_task(regenerate=True), "SEO Failed")  # the old run

        self.assertEqual(self.answers, ["Waiting for lock"])
        queued = self.requeue.call_args.kwargs
        self.assertEqual(queued["args"], [str(self.post.id)])
        self.assertEqual(
            queued["kwargs"],
            {"lock_waited_s": round(queued["countdown"], 1), "request_at": self.asked.isoformat(), "regenerate": True},
        )
        self.assertGreaterEqual(queued["countdown"], 20)
        self.assertLessEqual(queued["countdown"], 24)
        self.assertEqual(self.state(), (ScheduledPost.Status.SCHEDULED, None, self.asked))

        # The lock is free: the re-queued message generates the new text.
        self.generate.side_effect = None
        self.generate.return_value = generated(caption="Nuevo")
        self.assertEqual(self.run_task(**queued["kwargs"]), "SEO Success")

        self.assertEqual(self.state(), (ScheduledPost.Status.SCHEDULED, None, None))
        self.assertEqual(self.post.generated_caption, "Nuevo")

    def test_a_regenerar_while_the_old_run_succeeds_is_not_absorbed(self):
        def regenerar_then_the_old_call_answers(_request):
            self.regenerar_lands()
            return generated(caption="Viejo")

        self.generate.side_effect = regenerar_then_the_old_call_answers

        self.assertEqual(self.run_task(regenerate=True), "SEO Success")  # the old run

        # Its text is stored, and the newer request is still on its way.
        self.assertEqual(self.state(), (ScheduledPost.Status.SCHEDULED, None, self.asked))
        self.assertEqual(self.post.generated_caption, "Viejo")
        self.generate.side_effect = None
        self.generate.return_value = generated(caption="Nuevo")
        self.assertEqual(self.run_task(**self.requeue.call_args.kwargs["kwargs"]), "SEO Success")
        self.assertEqual(self.state(), (ScheduledPost.Status.SCHEDULED, None, None))
        self.assertEqual(self.post.generated_caption, "Nuevo")

    def test_a_text_saved_after_the_old_request_stays_when_the_old_run_answers(self):
        ScheduledPost.objects.filter(pk=self.post.pk).update(caption="viejo mío")

        def saved_then_regenerar(_request):
            ScheduledPost.objects.filter(pk=self.post.pk).update(caption="Mío", text_edited_at=timezone.now())
            ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=timezone.now())
            return generated(caption="Viejo")

        self.generate.side_effect = saved_then_regenerar

        self.assertEqual(self.run_task(regenerate=True), "SEO Success")

        self.post.refresh_from_db()
        # Saved after the request this run served: it stays (the newer «Regenerar» decides for itself).
        self.assertEqual((self.post.caption, self.post.generated_caption), ("Mío", "Viejo"))

    def test_it_waits_until_the_budget_and_then_the_newer_request_fails(self):
        from apps.videos.services.scheduled_posts import seo_request_pending

        budget = settings.AI_SEO_LOCK_WAIT_SECONDS
        # Review of #198: the wait must outlast one generation of the old run, the primary and the
        # fallback call and both moderations, or a post waiting for the new text fails meanwhile.
        self.assertGreater(budget, 2 * settings.AI_LLM_TIMEOUT_SECONDS + 2 * settings.MODERATION_TIMEOUT_SECONDS)
        self.held_by_a_run_serving(self.served)
        asked = timezone.now()
        ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=asked)

        just_before = self.run_task(regenerate=True, lock_waited_s=budget - 0.1, request_at=asked.isoformat())

        self.assertEqual(just_before, "Waiting for lock")
        self.assertGreaterEqual(self.requeue.call_args.kwargs["kwargs"]["lock_waited_s"], budget + 19.9)
        self.assertEqual(self.state(), (ScheduledPost.Status.SCHEDULED, None, asked))
        self.requeue.reset_mock()

        result = self.run_task(regenerate=True, lock_waited_s=budget, request_at=asked.isoformat())

        self.assertEqual(result, "Lock Timeout")
        self.requeue.assert_not_called()
        self.generate.assert_not_called()
        self.assertEqual(self.state(), (ScheduledPost.Status.FAILED, "seo_busy", None))
        self.assertFalse(seo_request_pending(self.post))  # «Regenerar» asks again

    @override_settings(AI_SEO_LOCK_WAIT_SECONDS=60)
    def test_the_budget_is_a_setting(self):
        self.held_by_a_run_serving(self.served)
        ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=timezone.now())
        self.post.refresh_from_db()

        result = self.run_task(regenerate=True, lock_waited_s=60, request_at=self.post.seo_requested_at.isoformat())

        self.assertEqual(result, "Lock Timeout")

    def test_another_message_of_the_request_being_generated_still_stops(self):
        self.held_by_a_run_serving(self.served)

        self.assertEqual(self.run_task(regenerate=True), "SEO Busy")

        self.requeue.assert_not_called()
        self.assertEqual(self.state(), (ScheduledPost.Status.SCHEDULED, None, self.served))

    def test_a_wait_whose_request_is_over_stops_without_generating(self):
        # The run that held the lock served it (or a newer request replaced it, with its own message).
        for now_on_record in (None, timezone.now()):
            with self.subTest(now_on_record=now_on_record):
                ScheduledPost.objects.filter(pk=self.post.pk).update(seo_requested_at=now_on_record)

                result = self.run_task(regenerate=True, lock_waited_s=20.0, request_at=self.served.isoformat())

                self.assertEqual(result, "SEO Skipped")
        self.generate.assert_not_called()
        self.requeue.assert_not_called()


class DeliveredAgainTests(SeoTaskHarness, TestCase):
    """
    Review of #198: the tasks use acks_late, so a message can be delivered again after it ran. Every
    message comes from queue_post_seo, which stamps a request: a post without one has nothing for
    the message to serve, and the run generates nothing.
    """

    def test_a_regenerar_delivered_again_after_it_succeeded_pays_nothing(self):
        self.assertEqual(self.run_task(regenerate=True), "SEO Success")

        self.assertEqual(self.run_task(regenerate=True), "SEO Skipped")  # the same message again

        self.generate.assert_called_once()
        self.assertEqual(self.seo_rows().count(), 1)
        self.post.refresh_from_db()
        self.assertEqual(self.post.generated_caption, CAPTION)

    def test_a_message_delivered_again_after_its_text_failed_stores_nothing(self):
        error = AIAuthenticationError("bad key")
        error.usage_attempts = [usage(error_code="AIAuthenticationError", estimated_cost_usd=0.0)]
        self.generate.side_effect = error
        self.assertEqual(self.run_task(), "SEO Failed")
        self.generate.side_effect = None

        self.assertEqual(self.run_task(), "SEO Skipped")

        self.generate.assert_called_once()
        self.post.refresh_from_db()
        self.assertEqual(
            (self.post.status, self.post.error_log, self.post.generated_caption),
            (ScheduledPost.Status.FAILED, "seo_generation_failed:AIAuthenticationError", None),
        )

    def test_a_request_another_run_served_before_the_lock_is_not_paid_again(self):
        @contextmanager
        def lock_after_another_run_served_it(post_id, served=None):
            ScheduledPost.objects.filter(pk=post_id).update(
                generated_caption="otro worker", generated_hashtags=TAGS, seo_requested_at=None,
            )
            yield True

        with patch("apps.videos.tasks.social._seo_post_lock", lock_after_another_run_served_it):
            self.assertEqual(self.run_task(regenerate=True), "SEO Skipped")

        self.generate.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(self.post.generated_caption, "otro worker")

    def test_an_argument_of_a_later_release_is_ignored(self):
        # A rolling deploy: a message with an argument this worker does not know still runs.
        with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            result = self.run_task(regenerate=True, something_new="value-not-logged")

        self.assertEqual(result, "SEO Success")
        output = "\n".join(logs.output)
        self.assertIn("seo.unknown_arguments", output)
        self.assertIn("something_new", output)
        self.assertNotIn("value-not-logged", output)
