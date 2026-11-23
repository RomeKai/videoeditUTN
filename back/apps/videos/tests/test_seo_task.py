"""AICORE-17 (#69): social metadata per ScheduledPost (process_video_seo), ADR-013."""

import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

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
        workspace = Workspace.objects.create(name="Pub", owner=user, ayrshare_profile_key="k")
        project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="P")
        clip = VideoClip.objects.create(
            project=project, title="c", start_time=0, end_time=5, s3_object_key="c.mp4"
        )
        post = ScheduledPost.objects.create(
            video_clip=clip, platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z", status=ScheduledPost.Status.QUEUED,
            generated_caption="Hola", generated_hashtags=["a1", "b2"],
        )
        with patch(
            "apps.videos.services.storage_service.CloudflareR2Manager.generate_presigned_url",
            return_value="https://signed.test/x",
        ), patch(
            "apps.integrations.ayrshare_api.AyrshareClient.send_post", return_value={"id": "1"}
        ) as send:
            upload_to_social_network.apply(args=[str(post.id)])

        self.assertEqual(send.call_args.kwargs["caption"], "Hola\n\n#a1 #b2")


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
