"""Usage capture inside the ingestion pipeline (AICORE-8, plan 3.4)."""

import time
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    TranscriptionResult,
    ViralClip,
    WordTimestamp,
)
from apps.videos.services.ai.errors import AIAuthenticationError, AIServerError, AITimeoutError
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.services.ai.pricing import PRICING_VERSION
from apps.videos.services.ai.usage_recorder import record_usages
from apps.videos.tests.test_ingestion_pipeline import (
    LEGACY_SELECT,
    IngestionHarness,
    selection_result,
    transcription_result,
)
from apps.videos.tests.test_usage_records import SECRET, selection_usage

TRANSCRIPTION = AIUsageRecord.Stage.TRANSCRIPTION
SELECTION = AIUsageRecord.Stage.SELECTION


def groq_result(text="Hello world"):
    return AIExecutionResult(
        data=TranscriptionResult(
            full_text=text, words=[WordTimestamp(text="Hello", start=0.0, end=1.0)], duration=600.0
        ),
        usage=ProviderUsage(
            provider="groq", model="whisper-large-v3-turbo", duration_seconds=3.5,
            audio_seconds=600.0, estimated_cost_usd=0.006667, pricing_version=PRICING_VERSION,
        ),
    )


def llm_result(**usage_overrides):
    clips = [ViralClip(start=0.0, end=5.0, title="Clip", virality_score=90, reasoning="r")]
    return AIExecutionResult(
        data=ClipSelectionResult(clips=clips), usage=selection_usage(**usage_overrides)
    )


def rows(stage=None):
    queryset = AIUsageRecord.objects.order_by("stage", "attempt", "role")
    return list(queryset.filter(stage=stage) if stage else queryset)


def by_role(records):
    """(primary, fallback) ordering regardless of alphabetical role order."""
    return sorted(records, key=lambda r: r.role != "primary")


class UsageCaptureTests(IngestionHarness, TestCase):
    def test_success_writes_one_row_per_stage_with_the_v2_version(self):
        self.mock_transcribe.return_value = groq_result()
        self.mock_select.return_value = llm_result()
        project = self.make_project()

        result = self.run_task(project)

        self.assertTrue(result.successful())
        (selection,) = rows(SELECTION)
        (transcription,) = rows(TRANSCRIPTION)
        self.assertEqual(
            (transcription.pipeline_version, transcription.attempt, transcription.role, transcription.success),
            ("v2", 0, "primary", True),
        )
        self.assertEqual((transcription.provider, transcription.model), ("groq", "whisper-large-v3-turbo"))
        self.assertEqual(transcription.audio_seconds, 600.0)
        self.assertEqual(transcription.estimated_cost_usd, Decimal("0.006667000"))
        self.assertEqual(transcription.pricing_version, PRICING_VERSION)
        self.assertEqual(transcription.project_id, project.id)
        self.assertEqual((selection.pipeline_version, selection.success), ("v2", True))
        self.assertEqual((selection.prompt_tokens, selection.cache_read_tokens), (1000, 200))
        self.assertEqual(selection.resolved_model, "gemini-3.8-flash-001")

    def test_metadata_usage_cache_is_still_written(self):
        project = self.make_project()

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.metadata["transcription_usage"]["provider"], "groq")
        self.assertEqual(project.metadata["selection_usage"]["provider"], "litellm")

    def test_provider_failure_writes_a_failed_row_and_propagates_the_same_exception(self):
        self.mock_select.side_effect = AIAuthenticationError(f"bad key {SECRET}")
        project = self.make_project()

        result = self.run_task(project)

        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, AIAuthenticationError)
        (failed,) = rows(SELECTION)
        self.assertFalse(failed.success)
        self.assertEqual(failed.error_code, "AIAuthenticationError")
        self.assertEqual((failed.prompt_tokens, failed.completion_tokens, failed.total_tokens), (0, 0, 0))
        self.assertEqual(failed.estimated_cost_usd, Decimal(0))
        self.assertGreaterEqual(failed.latency_seconds, 0.0)
        self.assertEqual(failed.pipeline_version, "v2")
        self.assertEqual(failed.attempt, 0)
        self.assertEqual(len(rows(TRANSCRIPTION)), 1)

    def test_failure_latency_is_the_measured_wall_clock_of_the_call(self):
        def slow_failure(*args, **kwargs):
            time.sleep(0.05)
            raise AIAuthenticationError("bad key")

        self.mock_select.side_effect = slow_failure
        project = self.make_project()

        self.run_task(project)

        self.assertGreaterEqual(rows(SELECTION)[0].latency_seconds, 0.05)

    def test_a_retry_records_a_new_attempt_and_never_repeats_the_paid_stage(self):
        self.mock_select.side_effect = [AIServerError("provider down"), selection_result()]
        project = self.make_project()

        result = self.run_task(project)

        self.assertTrue(result.successful())
        failed, answered = rows(SELECTION)
        self.assertEqual((failed.attempt, failed.success, failed.error_code), (0, False, "AIServerError"))
        self.assertEqual((answered.attempt, answered.success), (1, True))
        self.assertEqual(len(rows(TRANSCRIPTION)), 1)

    def test_fallback_after_a_billed_parse_failure_writes_two_rows(self):
        primary = selection_usage(error_code="AIContractValidationError")
        fallback = selection_usage(
            role="fallback", model="gemini/gemini-2.5-flash", prompt_tokens=800,
            completion_tokens=40, cache_read_tokens=0, estimated_cost_usd=0.00034,
        )
        self.mock_select.return_value = AIExecutionResult(
            data=llm_result().data, usage=fallback, attempts=[primary, fallback],
        )
        project = self.make_project()

        self.run_task(project)

        first, second = by_role(rows(SELECTION))
        self.assertEqual((first.role, first.success, first.error_code), ("primary", False, "AIContractValidationError"))
        self.assertEqual((first.prompt_tokens, first.completion_tokens), (1000, 100))
        self.assertEqual(first.estimated_cost_usd, Decimal("0.000000375"))
        self.assertEqual((second.role, second.success, second.model), ("fallback", True, "gemini/gemini-2.5-flash"))
        self.assertEqual(second.prompt_tokens, 800)

    def test_all_attempts_failing_keep_every_billed_attempt(self):
        error = AIAuthenticationError("final")
        error.usage_attempts = [
            selection_usage(error_code="AIContractValidationError"),
            selection_usage(
                role="fallback", prompt_tokens=0, completion_tokens=0, cache_read_tokens=0,
                estimated_cost_usd=0.0, error_code="AIAuthenticationError",
            ),
        ]
        self.mock_select.side_effect = error
        project = self.make_project()

        result = self.run_task(project)

        self.assertIs(result.result, error)
        primary, fallback = by_role(rows(SELECTION))
        self.assertEqual((primary.success, primary.prompt_tokens), (False, 1000))
        self.assertEqual((fallback.success, fallback.error_code), (False, "AIAuthenticationError"))

    def test_transcription_failure_after_billed_chunks_keeps_the_partial_usage(self):
        error = AIAuthenticationError("chunk 3 failed")
        error.usage_attempts = [ProviderUsage(
            provider="groq", model="whisper-large-v3-turbo", duration_seconds=7.0,
            audio_seconds=1200.0, estimated_cost_usd=0.013333, pricing_version=PRICING_VERSION,
            error_code="AIAuthenticationError",
        )]
        self.mock_transcribe.side_effect = error
        project = self.make_project()

        result = self.run_task(project)

        self.assertIs(result.result, error)
        (partial,) = rows(TRANSCRIPTION)
        self.assertFalse(partial.success)
        self.assertEqual(partial.audio_seconds, 1200.0)
        self.assertEqual(partial.estimated_cost_usd, Decimal("0.013333000"))
        self.assertEqual(partial.latency_seconds, 7.0)
        self.assertEqual(rows(SELECTION), [])

    def test_transcription_failure_without_usage_writes_a_zero_row_with_the_groq_identity(self):
        self.mock_transcribe.side_effect = AIAuthenticationError("no key")
        project = self.make_project()

        self.run_task(project)

        (failed,) = rows(TRANSCRIPTION)
        self.assertEqual(
            (failed.provider, failed.model, failed.success),
            ("groq", "whisper-large-v3-turbo", False),
        )
        self.assertEqual(failed.pipeline_version, "v2")

    def test_a_timeout_leaves_the_failed_row_cost_unknown(self):
        self.mock_select.side_effect = [AITimeoutError()] * 20
        project = self.make_project()

        self.run_task(project)

        self.assertTrue(rows(SELECTION))
        self.assertTrue(all(row.estimated_cost_usd is None for row in rows(SELECTION)))

    def test_a_metrics_write_failure_never_fails_the_stage(self):
        project = self.make_project()

        with patch.object(
            AIUsageRecord.objects, "create", side_effect=RuntimeError("metrics db down")
        ):
            with self.assertLogs("apps.videos.services.ai.usage_recorder", level="WARNING") as logs:
                result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(AIUsageRecord.objects.count(), 0)
        self.assertEqual(sum("metrics.write_failed" in r.getMessage() for r in logs.records), 2)

    def test_a_metrics_write_failure_does_not_mask_the_provider_error(self):
        self.mock_select.side_effect = AIAuthenticationError("bad key")
        project = self.make_project()

        with patch.object(
            AIUsageRecord.objects, "create", side_effect=RuntimeError("metrics db down")
        ):
            result = self.run_task(project)

        self.assertIsInstance(result.result, AIAuthenticationError)


class PipelineVersionCaptureTests(IngestionHarness, TestCase):
    def legacy_clips(self):
        return [{"start": 0.0, "end": 5.0, "title": "Legacy", "virality_score": 80, "reasoning": "r"}]

    @override_settings(AI_CORE_V2_ENABLED=False)
    def test_legacy_path_records_legacy_with_unknown_selection_cost(self):
        self.mock_transcribe.return_value = AIExecutionResult(
            data=transcription_result().data,
            usage=ProviderUsage(
                provider="local", model="whisper-base", duration_seconds=12.0,
                estimated_cost_usd=0.0, audio_seconds=600.0, pricing_version=PRICING_VERSION,
            ),
        )
        project = self.make_project()

        with patch(LEGACY_SELECT, return_value=self.legacy_clips()):
            result = self.run_task(project)

        self.assertTrue(result.successful())
        (transcription,) = rows(TRANSCRIPTION)
        (selection,) = rows(SELECTION)
        self.assertEqual(transcription.pipeline_version, "legacy")
        self.assertEqual((transcription.provider, transcription.model), ("local", "whisper-base"))
        # Local Whisper has no API charge: a real zero, with audio and pricing version.
        self.assertEqual(transcription.estimated_cost_usd, Decimal(0))
        self.assertEqual(transcription.audio_seconds, 600.0)
        self.assertEqual(transcription.pricing_version, PRICING_VERSION)
        self.assertEqual(selection.pipeline_version, "legacy")
        # The legacy selector is a paid LLM call whose usage is not captured: unknown, not 0.
        self.assertIsNone(selection.estimated_cost_usd)
        self.assertEqual((selection.provider, selection.model), ("gemini", "gemini/gemini-3.8-flash"))
        self.assertTrue(selection.success)

    @override_settings(AI_CORE_V2_ENABLED=False)
    def test_legacy_selection_failure_is_recorded_as_legacy(self):
        project = self.make_project()

        with patch(LEGACY_SELECT, side_effect=ValueError("no key")):
            self.run_task(project)

        (failed,) = rows(SELECTION)
        self.assertEqual(
            (failed.pipeline_version, failed.success, failed.error_code), ("legacy", False, "ValueError")
        )

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="local")
    def test_transcription_version_follows_the_engines_resolved_backend(self):
        project = self.make_project()

        self.run_task(project)

        (transcription,) = rows(TRANSCRIPTION)
        (selection,) = rows(SELECTION)
        self.assertEqual(transcription.pipeline_version, "legacy")
        self.assertEqual(selection.pipeline_version, "v2")

    def test_the_version_is_not_re_read_from_the_flag_after_the_call(self):
        def flip_flag_during_the_call(*args, **kwargs):
            override = override_settings(AI_CORE_V2_ENABLED=False)
            override.enable()
            self.addCleanup(override.disable)
            return selection_result()

        self.mock_select.side_effect = flip_flag_during_the_call
        project = self.make_project()

        self.run_task(project)

        (selection,) = rows(SELECTION)
        self.assertEqual(selection.pipeline_version, "v2")


class UsageCapturePrivacyTests(IngestionHarness, TestCase):
    def test_no_user_text_reaches_any_column_or_the_metrics_logs(self):
        secret_error = AIServerError(f"SECRET_REASON_X echoed {SECRET}")
        secret_error.usage_attempts = [selection_usage(error_code="AIServerError")]
        self.mock_transcribe.return_value = AIExecutionResult(
            data=TranscriptionResult(
                full_text="SECRET_TRANSCRIPT_X",
                words=[WordTimestamp(text="SECRET_TRANSCRIPT_X", start=0.0, end=1.0)],
            ),
            usage=ProviderUsage(provider="groq", model="whisper-large-v3-turbo"),
        )
        self.mock_select.side_effect = [secret_error, AIAuthenticationError("SECRET_REASON_X again")]
        project = self.make_project()
        VideoProject.objects.filter(pk=project.id).update(title="SECRET_TITLE_X")

        with self.assertLogs("", level="DEBUG") as logs:
            self.run_task(project)

        dumped = []
        for record in AIUsageRecord.objects.all():
            dumped.extend(str(getattr(record, f.attname)) for f in AIUsageRecord._meta.concrete_fields)
        self.assertTrue(dumped)
        self.assertNotIn("SECRET_", " ".join(dumped))
        usage_logs = [r for r in logs.records if r.name == "apps.videos.services.ai.usage_recorder"]
        for record in usage_logs:
            self.assertNotIn("SECRET_", record.getMessage())
            self.assertIsNone(record.exc_info)


class RecordingOrderTests(IngestionHarness, TransactionTestCase):
    """TransactionTestCase: TestCase would wrap everything in an atomic block."""

    def test_rows_are_written_after_the_artifact_and_outside_any_transaction(self):
        project = self.make_project()
        observed = []
        real = record_usages

        def spy(project_id, stage, version, usages, attempt=None):
            row = VideoProject.objects.get(pk=project_id)
            observed.append(
                (stage, connection.in_atomic_block, row.pipeline_stage, bool(row.transcript_data))
            )
            return real(project_id, stage, version, usages, attempt=attempt)

        with patch("apps.videos.tasks.record_usages", side_effect=spy):
            self.run_task(project)

        self.assertEqual(
            observed,
            [
                ("transcription", False, PipelineStage.TRANSCRIBED, True),
                ("selection", False, PipelineStage.CLIPS_SELECTED, True),
            ],
        )

    def test_no_transaction_is_open_during_provider_calls_with_capture_enabled(self):
        observed = {}

        def probe(name, value):
            def inner(*args, **kwargs):
                observed[name] = connection.in_atomic_block
                return value
            return inner

        self.mock_transcribe.side_effect = probe("transcribe", transcription_result())
        self.mock_select.side_effect = probe("select", selection_result())
        project = self.make_project()

        self.run_task(project)

        self.assertEqual(observed, {"transcribe": False, "select": False})
        self.assertEqual(AIUsageRecord.objects.count(), 2)

    def test_failure_rows_are_written_outside_any_transaction_too(self):
        self.mock_select.side_effect = AIAuthenticationError("bad key")
        project = self.make_project()
        observed = []
        real = record_usages

        def spy(project_id, stage, version, usages, attempt=None):
            observed.append((stage, connection.in_atomic_block))
            return real(project_id, stage, version, usages, attempt=attempt)

        with patch("apps.videos.tasks.record_usages", side_effect=spy):
            self.run_task(project)

        self.assertEqual(observed, [("transcription", False), ("selection", False)])
