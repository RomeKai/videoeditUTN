"""
AICORE-11 (#60): the ingestion pipeline waits for provider capacity instead of
running into the providers' limits.

The token buckets themselves are tested against Valkey in
``services/test_capacity_store.py``; here an in-memory store grants or denies
on demand so the pipeline flow (pending_capacity, re-queue, timeout,
settlement) can be checked stage by stage.
"""

from collections import defaultdict
from unittest.mock import patch

import redis
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.videos.models import VideoProject
from apps.videos.services.ai.contracts import AIExecutionResult, ProviderUsage
from apps.videos.services.ai.errors import AIServerError, AITimeoutError
from apps.videos.services.ai.pipeline_state import STAGE_ORDER, PipelineStage
from apps.videos.tests.test_ingestion_pipeline import (
    IngestionHarness,
    selection_result,
    transcription_result,
)

PRIMARY = "gemini/gemini-3.8-flash"
FALLBACK = "gemini/gemini-3.5-flash-lite"
GROQ = "groq/whisper-large-v3-turbo"
LEGACY_SELECT = "apps.videos.services.selection_engine.SelectionEngine.select_viral_clips"
REQUEUE = "apps.videos.tasks.process_initial_ingestion.apply_async"

LIMITS = {
    GROQ: {"audio_seconds_per_hour": 5760, "audio_seconds_per_day": 23040, "requests_per_minute": 16},
    PRIMARY: {"tokens_per_minute": 160000, "requests_per_minute": 400},
    FALLBACK: {"tokens_per_minute": 160000, "requests_per_minute": 400},
}

# The harness video lasts 10 s: one Whisper chunk, and the LLM estimate for one
# minute of audio = 456 + 1500 prompt + 4000 output tokens.
LLM_ESTIMATE = 456 + 1500 + 4000


def key(model, bucket):
    return f"capacity:v1:{model}:{bucket}"


class FakeCapacityStore:
    """Grants unless a denial or a failure is queued; tracks the net drawn per bucket."""

    def __init__(self):
        self.drawn = defaultdict(float)
        self.denials = []   # wait seconds answered by the next reserve calls
        self.failures = []  # exceptions raised by the next reserve calls
        self.reserves = []

    def reserve(self, demands):
        self.reserves.append({bucket.key: amount for bucket, amount in demands})
        if self.failures:
            raise self.failures.pop(0)
        if self.denials:
            return False, self.denials.pop(0)
        for bucket, amount in demands:
            self.drawn[bucket.key] += amount
        return True, 0.0

    def adjust(self, bucket, delta):
        self.drawn[bucket.key] += delta
        return 0.0


class CapacityHarness(IngestionHarness):
    def setUp(self):
        super().setUp()
        capacity = override_settings(
            AI_CAPACITY_ENABLED=True,
            AI_CAPACITY_LIMITS=LIMITS,
            AI_DEFAULT_LLM_MODEL=PRIMARY,
            AI_FALLBACK_LLM_MODEL=FALLBACK,
            AI_DEFAULT_TRANSCRIPTION_MODEL="whisper-large-v3-turbo",
            TRANSCRIPTION_BACKEND="groq",
            GEMINI_API_KEY="test-key",
        )
        capacity.enable()
        self.addCleanup(capacity.disable)
        self.store = FakeCapacityStore()
        self._patch("apps.videos.services.ai.capacity.default_store", return_value=self.store)
        self.requeue = self._patch(REQUEUE)

    def usage_rows(self, project):
        return project.ai_usage_records.count()


class CapacityWaitTests(CapacityHarness, TestCase):
    def test_no_room_parks_the_stage_and_requeues_without_a_failed_attempt(self):
        project = self.make_project()
        self.store.denials = [30.0]

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.mock_transcribe.assert_not_called()
        self.assertEqual(project.pipeline_stage, PipelineStage.AUDIO_EXTRACTED)
        self.assertEqual(project.pipeline_stage_status["transcribed"], "pending_capacity")
        self.assertNotEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_attempts, 0)
        self.assertFalse(project.pipeline_error_code)
        self.assertEqual(self.usage_rows(project), 0)

        self.requeue.assert_called_once()
        self.assertEqual(self.requeue.call_args.kwargs["args"], [str(project.id)])
        countdown = self.requeue.call_args.kwargs["countdown"]
        self.assertGreaterEqual(countdown, 30.0)
        self.assertLessEqual(countdown, 36.0)  # at most +20 % jitter
        wait = project.metadata["capacity_wait"]
        self.assertEqual(wait["stage"], "transcribed")
        self.assertAlmostEqual(wait["planned_s"], countdown, places=1)

    def test_requeued_task_resumes_where_it_waited_and_records_the_wait(self):
        project = self.make_project()
        self.store.denials = [30.0]
        self.run_task(project)

        result = self.run_task(project)  # what the re-queued message does

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.mock_transcribe.assert_called_once()
        self.assertEqual(self.mock_upload.call_count, 2)  # source prepared once
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.pipeline_stage_status["transcribed"], "completed")
        self.assertEqual(project.pipeline_attempts, 0)
        self.assertIsNone(project.metadata["capacity_wait"])
        self.assertIn("transcribed", project.metadata["capacity_waited_s"])
        self.assertGreaterEqual(project.metadata["capacity_waited_s"]["transcribed"], 0)

    def test_selection_waits_without_repeating_transcription(self):
        project = self.make_project()
        self.store.denials = []
        original_reserve = self.store.reserve

        def deny_selection_once(demands):
            if any(bucket.model == PRIMARY for bucket, _ in demands) and not self.requeue.called:
                self.store.reserves.append({})
                return False, 5.0
            return original_reserve(demands)

        self.store.reserve = deny_selection_once

        self.run_task(project)
        project.refresh_from_db()
        # The stage right before selection (transcribed, or moderated once AICORE-13 lands).
        self.assertEqual(
            project.pipeline_stage, STAGE_ORDER[STAGE_ORDER.index(PipelineStage.CLIPS_SELECTED) - 1]
        )
        self.assertEqual(project.pipeline_stage_status["transcribed"], "completed")
        self.assertEqual(project.pipeline_stage_status["clips_selected"], "pending_capacity")
        self.assertTrue(project.transcript_data)
        self.mock_select.assert_not_called()

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.mock_transcribe.assert_called_once()
        self.mock_select.assert_called_once()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertIn("clips_selected", project.metadata["capacity_waited_s"])

    def test_waiting_beyond_the_maximum_fails_with_capacity_timeout(self):
        project = self.make_project()
        self.store.denials = [80.0, 80.0]

        with override_settings(AI_CAPACITY_MAX_WAIT_SECONDS=100):
            self.run_task(project)
            result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())  # handled: no Celery retry, no exception
        self.requeue.assert_called_once()  # only the first wait was re-queued
        self.mock_transcribe.assert_not_called()
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "CapacityTimeoutError")
        self.assertEqual(project.pipeline_stage_status["transcribed"], "failed")
        self.assertEqual(project.pipeline_attempts, 1)
        self.assertIsNone(project.metadata["capacity_wait"])

    def test_the_countdown_is_capped_and_waits_accumulate(self):
        project = self.make_project()
        self.store.denials = [1000.0, 1000.0]

        with override_settings(AI_CAPACITY_MAX_COUNTDOWN_SECONDS=300):
            self.run_task(project)
            self.run_task(project)

        first, second = (c.kwargs["countdown"] for c in self.requeue.call_args_list)
        for countdown in (first, second):
            self.assertGreaterEqual(countdown, 300.0)
            self.assertLessEqual(countdown, 360.0)
        project.refresh_from_db()
        self.assertAlmostEqual(
            project.metadata["capacity_wait"]["planned_s"], first + second, places=0
        )

    def test_valkey_down_is_retryable_and_never_calls_the_provider_unreserved(self):
        project = self.make_project()
        self.store.failures = [redis.exceptions.ConnectionError("valkey down")]

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())  # Celery retry succeeded once Valkey answered
        self.mock_transcribe.assert_called_once()
        self.assertEqual(len([r for r in self.store.reserves if key(GROQ, "requests_per_minute") in r]), 2)
        self.assertEqual(project.pipeline_attempts, 1)  # the failed attempt counts
        self.requeue.assert_not_called()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    def test_a_model_without_limits_fails_before_being_called(self):
        project = self.make_project()
        limits = {GROQ: LIMITS[GROQ]}  # no LLM limits configured

        with override_settings(AI_CAPACITY_LIMITS=limits):
            result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.failed())
        self.mock_select.assert_not_called()
        self.assertEqual(project.pipeline_error_code, "ImproperlyConfigured")
        self.assertEqual(project.status, VideoProject.Status.FAILED)

    @override_settings(AI_CORE_V2_ENABLED=False)
    def test_legacy_selector_waits_for_capacity_too(self):
        project = self.make_project()
        self.store.denials = [10.0]
        legacy = [{"start": 0.0, "end": 5.0, "title": "L", "virality_score": 80, "reasoning": "r"}]

        with patch(LEGACY_SELECT, return_value=legacy) as legacy_select:
            self.run_task(project)
            project.refresh_from_db()
            # Local Whisper calls no provider: only the selection reserves.
            self.assertEqual(project.pipeline_stage_status["clips_selected"], "pending_capacity")
            legacy_select.assert_not_called()
            self.run_task(project)

        legacy_select.assert_called_once()
        self.assertEqual(self.store.drawn[key(PRIMARY, "tokens_per_minute")], LLM_ESTIMATE)
        self.assertEqual(self.store.drawn[key(PRIMARY, "requests_per_minute")], 1)
        self.assertNotIn(key(GROQ, "requests_per_minute"), self.store.drawn)
        self.assertNotIn(key(FALLBACK, "tokens_per_minute"), self.store.drawn)


class CapacitySettlementTests(CapacityHarness, TestCase):
    def test_transcription_reserves_the_duration_and_settles_to_the_billed_seconds(self):
        project = self.make_project()
        result = transcription_result()
        result.usage.audio_seconds = 12.0  # Groq bills a 10 s minimum per chunk, etc.
        self.mock_transcribe.return_value = result

        self.run_task(project)

        reserved = self.store.reserves[0]
        self.assertEqual(reserved[key(GROQ, "audio_seconds_per_hour")], 10.0)
        self.assertEqual(reserved[key(GROQ, "requests_per_minute")], 1)
        self.assertEqual(self.store.drawn[key(GROQ, "audio_seconds_per_hour")], 12.0)
        self.assertEqual(self.store.drawn[key(GROQ, "audio_seconds_per_day")], 12.0)
        self.assertEqual(self.store.drawn[key(GROQ, "requests_per_minute")], 1)  # unknown: kept

    def test_unknown_billed_seconds_keep_the_reservation(self):
        project = self.make_project()  # the default fake usage carries no audio_seconds

        self.run_task(project)

        self.assertEqual(self.store.drawn[key(GROQ, "audio_seconds_per_hour")], 10.0)

    def test_the_unused_fallback_is_refunded_and_the_primary_settled(self):
        project = self.make_project()
        result = selection_result()
        result.usage.total_tokens = 3000
        result.usage.model = PRIMARY
        self.mock_select.return_value = result

        self.run_task(project)

        selection = next(r for r in self.store.reserves if key(PRIMARY, "tokens_per_minute") in r)
        self.assertEqual(selection[key(PRIMARY, "tokens_per_minute")], LLM_ESTIMATE)
        self.assertEqual(selection[key(FALLBACK, "tokens_per_minute")], LLM_ESTIMATE)
        self.assertEqual(self.store.drawn[key(PRIMARY, "tokens_per_minute")], 3000)
        self.assertEqual(self.store.drawn[key(PRIMARY, "requests_per_minute")], 1)
        self.assertEqual(self.store.drawn[key(FALLBACK, "tokens_per_minute")], 0)
        self.assertEqual(self.store.drawn[key(FALLBACK, "requests_per_minute")], 0)

    def test_a_fallback_answer_charges_both_models(self):
        project = self.make_project()
        failed = ProviderUsage(
            provider="gemini", model=PRIMARY, error_code="AIServerError", estimated_cost_usd=0.0
        )
        answered = ProviderUsage(provider="gemini", model=FALLBACK, total_tokens=2500, role="fallback")
        self.mock_select.return_value = AIExecutionResult(
            data=selection_result().data, usage=answered, attempts=[failed, answered]
        )

        self.run_task(project)

        self.assertEqual(self.store.drawn[key(PRIMARY, "tokens_per_minute")], 0)
        self.assertEqual(self.store.drawn[key(PRIMARY, "requests_per_minute")], 1)
        self.assertEqual(self.store.drawn[key(FALLBACK, "tokens_per_minute")], 2500)
        self.assertEqual(self.store.drawn[key(FALLBACK, "requests_per_minute")], 1)

    def test_a_failure_that_never_reached_the_model_is_refunded(self):
        project = self.make_project()
        self.mock_select.side_effect = [AIServerError("down"), selection_result()]

        self.run_task(project)

        # First attempt refunded (5xx: no charge), second settled to 0 real tokens.
        self.assertEqual(self.store.drawn[key(PRIMARY, "tokens_per_minute")], 0)
        self.assertEqual(self.store.drawn[key(FALLBACK, "tokens_per_minute")], 0)

    def test_a_timeout_may_have_been_billed_so_its_reservation_is_kept(self):
        project = self.make_project()
        self.mock_select.side_effect = [AITimeoutError("slow"), selection_result()]

        self.run_task(project)

        self.assertEqual(self.store.drawn[key(PRIMARY, "tokens_per_minute")], LLM_ESTIMATE)
        self.assertEqual(self.store.drawn[key(FALLBACK, "tokens_per_minute")], LLM_ESTIMATE)

    def test_without_the_fallback_key_only_the_primary_is_reserved(self):
        project = self.make_project()

        with override_settings(AI_FALLBACK_LLM_MODEL="openai/gpt-4o-mini", OPENAI_API_KEY=None):
            self.run_task(project)

        selection = next(r for r in self.store.reserves if key(PRIMARY, "tokens_per_minute") in r)
        self.assertEqual(
            sorted(selection), [key(PRIMARY, "requests_per_minute"), key(PRIMARY, "tokens_per_minute")]
        )


class CapacityDisabledTests(IngestionHarness, TestCase):
    @override_settings(AI_CAPACITY_ENABLED=False)
    def test_disabled_never_touches_valkey_for_capacity(self):
        project = self.make_project()

        with patch(
            "apps.videos.services.ai.capacity.default_store",
            side_effect=AssertionError("capacity store used while disabled"),
        ):
            result = self.run_task(project)

        self.assertTrue(result.successful())


class CapacitySettingsTests(SimpleTestCase):
    def test_workers_prefetch_one_task_at_a_time(self):
        # Re-queued waits must not pile up in a worker's prefetch buffer.
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)

    def test_capacity_is_off_unless_configured(self):
        self.assertFalse(settings.AI_CAPACITY_ENABLED)
        self.assertGreater(settings.AI_CAPACITY_MAX_WAIT_SECONDS, settings.AI_CAPACITY_MAX_COUNTDOWN_SECONDS)


class SeoCapacityTests(TestCase):
    """SEO metadata calls the same LLM as selection: it reserves capacity too."""

    CHECK = "apps.core.security.AI_Security_Shield.check_content_safety"
    SERVICE = "apps.videos.services.seo_engine.SEOOptimizationService"
    SEO_REQUEUE = "apps.videos.tasks.process_video_seo.apply_async"

    def setUp(self):
        from django.contrib.auth import get_user_model

        from apps.users.models import Workspace
        from apps.videos.models import ScheduledPost, VideoClip

        user = get_user_model().objects.create_user(username="seo-cap", email="s@t.com", password="pw")
        workspace = Workspace.objects.create(name="SEO capacity", owner=user)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="S", metadata={"full_text": "x" * 400}
        )
        clip = VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5)
        self.post = ScheduledPost.objects.create(
            video_clip=clip, platform=ScheduledPost.Platform.TIKTOK,
            publish_at="2030-01-01T00:00:00Z", status=ScheduledPost.Status.QUEUED,
        )
        self.ScheduledPost = ScheduledPost
        self.store = FakeCapacityStore()
        for target, kwargs in (
            ("apps.videos.services.ai.capacity.default_store", {"return_value": self.store}),
            (self.CHECK, {}),
            (self.SEO_REQUEUE, {}),
        ):
            patcher = patch(target, **kwargs)
            setattr(self, target.rsplit(".", 1)[-1], patcher.start())
            self.addCleanup(patcher.stop)
        settings_override = override_settings(
            AI_CAPACITY_ENABLED=True, AI_CAPACITY_LIMITS=LIMITS, AI_DEFAULT_LLM_MODEL=PRIMARY,
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)

    def run_task(self, **kwargs):
        from apps.videos.tasks import process_video_seo

        process_video_seo.push_request(retries=0)
        try:
            return process_video_seo.run(str(self.post.id), **kwargs)
        finally:
            process_video_seo.pop_request()

    def test_seo_reserves_the_llm_before_calling_it(self):
        with patch(self.SERVICE) as service:
            generate = service.return_value.generate_metadata
            generate.return_value.hashtags = ["a"]
            generate.return_value.description_body = "d"
            generate.return_value.viral_title = "t"
            self.run_task()

        generate.assert_called_once()
        self.assertEqual(self.store.drawn[key(PRIMARY, "tokens_per_minute")], 100 + 1500 + 4000)
        self.assertEqual(self.store.drawn[key(PRIMARY, "requests_per_minute")], 1)

    def test_no_room_requeues_without_spending_a_retry(self):
        self.store.denials = [20.0]

        with patch(self.SERVICE) as service:
            result = self.run_task()
        generate = service.return_value.generate_metadata

        generate.assert_not_called()
        self.assertEqual(result, "Waiting for capacity")
        call = self.apply_async.call_args
        self.assertEqual(call.kwargs["args"], [str(self.post.id)])
        self.assertGreaterEqual(call.kwargs["countdown"], 20.0)
        self.assertEqual(call.kwargs["kwargs"]["capacity_waited_s"], round(call.kwargs["countdown"], 1))
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, self.ScheduledPost.Status.QUEUED)

    def test_waiting_beyond_the_maximum_fails_the_post_with_a_static_code(self):
        self.store.denials = [120.0]

        with override_settings(AI_CAPACITY_MAX_WAIT_SECONDS=600), patch(self.SERVICE) as service:
            result = self.run_task(capacity_waited_s=550.0)
        generate = service.return_value.generate_metadata

        generate.assert_not_called()
        self.assertEqual(result, "Capacity Timeout")
        self.apply_async.assert_not_called()
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, self.ScheduledPost.Status.FAILED)
        self.assertEqual(self.post.error_log, "capacity_timeout")
