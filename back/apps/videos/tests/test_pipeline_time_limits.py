"""
Time limits on AI ingestion: a hung run must fail the project and free its
lock instead of being kept alive by the lock heartbeat.
"""

import subprocess
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.videos.models import AIUsageRecord
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _is_retryable_pipeline_error, process_initial_ingestion
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness
from apps.videos.utils.ffmpeg_utils import FFmpegManager


class TaskLimitsTests(SimpleTestCase):
    def test_ingestion_has_soft_and_hard_limits_below_the_visibility_timeout(self):
        soft = process_initial_ingestion.soft_time_limit
        hard = process_initial_ingestion.time_limit
        visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]
        self.assertEqual(soft, settings.PIPELINE_SOFT_TIME_LIMIT)
        self.assertEqual(hard, settings.PIPELINE_TIME_LIMIT)
        self.assertLess(soft, hard)
        self.assertLess(hard, visibility)

    def test_soft_time_limit_is_not_retried(self):
        self.assertFalse(_is_retryable_pipeline_error(SoftTimeLimitExceeded()))


class SoftLimitPipelineTests(IngestionHarness, TestCase):
    def test_soft_limit_during_transcription_fails_the_project_and_frees_the_lock(self):
        self.mock_transcribe.side_effect = SoftTimeLimitExceeded()
        project = self.make_project()

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "SoftTimeLimitExceeded")
        self.mock_transcribe.assert_called_once()
        self.assertEqual(self.redis.held, {})


class FFmpegTimeoutTests(SimpleTestCase):
    def _run(self, method, *args):
        with patch("os.path.exists", return_value=True), patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)
        ) as run:
            with self.assertRaises(RuntimeError) as ctx:
                method(*args)
        return run, ctx.exception

    def test_audio_extraction_is_bounded(self):
        run, exc = self._run(FFmpegManager.extract_flac_audio, "in.mp4", "out.flac")
        self.assertEqual(run.call_args.kwargs["timeout"], FFmpegManager.AUDIO_EXTRACT_TIMEOUT_SECONDS)
        self.assertIn("timed out", str(exc))

    def test_probe_is_bounded(self):
        run, _ = self._run(FFmpegManager.get_media_duration, "in.flac")
        self.assertEqual(run.call_args.kwargs["timeout"], FFmpegManager.PROBE_TIMEOUT_SECONDS)


LEGACY = override_settings(AI_CORE_V2_ENABLED=False, GEMINI_API_KEY="g-key")


class SoftLimitInsideProviderCallTests(IngestionHarness, TestCase):
    """
    The limit fires inside the provider call, below every classifier (the
    selector is NOT mocked here, only the network call of the legacy strategy).
    Method-level overrides: the harness enables V2 in setUp.
    """

    @LEGACY
    def test_soft_limit_inside_the_llm_call_fails_without_retrying(self):
        from apps.videos.services.selection_engine import GeminiFlashStrategy

        project = self.make_project()
        with patch.object(
            GeminiFlashStrategy, "select_clips", side_effect=SoftTimeLimitExceeded()
        ) as llm_call:
            self.run_task(project)

        project.refresh_from_db()
        llm_call.assert_called_once()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "SoftTimeLimitExceeded")
        record = AIUsageRecord.objects.get(project=project, stage=AIUsageRecord.Stage.SELECTION)
        self.assertEqual(record.error_code, "SoftTimeLimitExceeded")
        self.assertIsNone(record.estimated_cost_usd)

    @LEGACY
    def test_answered_but_empty_legacy_call_is_recorded_with_unknown_cost(self):
        from apps.videos.services.selection_engine import GeminiFlashStrategy

        project = self.make_project()
        with patch.object(GeminiFlashStrategy, "select_clips", return_value='{"clips": []}'):
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_error_code, "AIEmptySelectionError")
        record = AIUsageRecord.objects.get(project=project, stage=AIUsageRecord.Stage.SELECTION)
        self.assertFalse(record.success)
        self.assertIsNone(record.estimated_cost_usd)
