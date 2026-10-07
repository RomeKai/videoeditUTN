from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    TranscriptionResult,
    ViralClip,
    WordTimestamp,
)
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIServerError,
    RetryableAIError,
)
from apps.videos.services.ai.pipeline_state import PipelineStage, advance
from apps.videos.services.ai.pipeline_state import PipelineConflictError
from apps.videos.tasks import _merge_metadata, _retry_countdown, process_initial_ingestion
from apps.videos.tests.fakes import DownRedis, FakeRedis

User = get_user_model()

TRANSCRIBE = "apps.videos.services.transcription_engine.TranscriptionEngine.transcribe_detailed"
SELECT = "apps.videos.services.selection_engine.SelectionEngine.select_viral_clips_detailed"
UPLOAD = "apps.videos.services.storage_service.CloudflareR2Manager.upload_video"
PROXY = "apps.videos.utils.ffmpeg_utils.FFmpegManager.generate_web_proxy"
REDIS_CLIENT = "apps.videos.services.ai.project_lock.get_redis_client"


def transcription_result():
    words = [
        WordTimestamp(text="Hello", start=0.0, end=1.0),
        WordTimestamp(text="world", start=1.0, end=2.0),
    ]
    return AIExecutionResult(
        data=TranscriptionResult(full_text="Hello world", words=words, duration=2.0),
        usage=ProviderUsage(provider="groq", model="whisper-large-v3-turbo"),
    )


def selection_result(count=1):
    clips = [
        ViralClip(
            start=float(i), end=float(i) + 5.0, title=f"Clip {i}",
            virality_score=90, reasoning="Test",
        )
        for i in range(count)
    ]
    return AIExecutionResult(
        data=ClipSelectionResult(clips=clips),
        usage=ProviderUsage(provider="litellm", model="gemini"),
    )


class IngestionHarness:
    """Shared fixtures: every external dependency mocked, Redis replaced by a fake."""

    def setUp(self):
        super().setUp()
        user = User.objects.create_user(username="pipe", email="pipe@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Pipe Workspace", owner=user)
        self.user = user
        self.redis = FakeRedis()

        self.mock_transcribe = self._patch(TRANSCRIBE, return_value=transcription_result())
        self.mock_select = self._patch(SELECT, return_value=selection_result())
        self.mock_upload = self._patch(UPLOAD, return_value="r2_key")
        self.mock_proxy = self._patch(PROXY)
        self._patch(REDIS_CLIENT, return_value=self.redis)
        self.mock_render = self._patch("apps.videos.tasks.render_clip_task.delay")

        mock_vfc = self._patch("apps.videos.tasks.VideoFileClip", create=True)
        instance = MagicMock(duration=10.0, size=[1920, 1080])
        instance.__enter__.return_value = instance
        mock_vfc.return_value = instance

    def _patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def make_project(self, **kwargs):
        return VideoProject.objects.create(
            workspace=self.workspace,
            uploaded_by=self.user,
            title="Pipeline",
            source_file=SimpleUploadedFile("pipe.mp4", b"content"),
            **kwargs,
        )

    def run_task(self, project):
        return process_initial_ingestion.apply(args=[str(project.id)])


class StageResumeTests(IngestionHarness, TestCase):
    def test_happy_path_persists_every_stage(self):
        project = self.make_project()

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(
            project.pipeline_stage_status,
            {"audio_extracted": "completed", "transcribed": "completed", "clips_selected": "completed"},
        )
        self.assertEqual(project.transcript_data[0], {"start": 0.0, "end": 1.0, "text": "Hello"})
        self.assertEqual(project.metadata["full_text"], "Hello world")
        self.assertEqual(project.metadata["duration"], 10.0)
        self.assertEqual(project.clips.count(), 1)
        self.assertEqual(project.ai_rationale_log["suggestions_count"], 1)
        self.mock_transcribe.assert_called_once()
        self.mock_select.assert_called_once()

    def test_selection_failure_keeps_transcript_and_retry_only_selects(self):
        project = self.make_project()
        seen_at_selection = []

        def flaky_select(*args, **kwargs):
            row = VideoProject.objects.get(pk=project.id)
            seen_at_selection.append((row.transcript_data, row.pipeline_stage))
            if len(seen_at_selection) == 1:
                raise AIServerError("provider down")
            return selection_result()

        self.mock_select.side_effect = flaky_select

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        # The transcript was already in the DB when selection started, both times.
        for transcript, stage in seen_at_selection:
            self.assertTrue(transcript)
            self.assertEqual(stage, PipelineStage.TRANSCRIBED)
        self.assertEqual(self.mock_transcribe.call_count, 1)
        self.assertEqual(self.mock_select.call_count, 2)
        # Source preparation is not repeated either: original + proxy uploaded once.
        self.assertEqual(self.mock_upload.call_count, 2)
        self.assertEqual(project.clips.count(), 1)
        self.assertEqual(project.pipeline_attempts, 1)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    def test_transcription_failure_retries_from_transcription_not_source(self):
        project = self.make_project()
        self.mock_transcribe.side_effect = [AIServerError("down"), transcription_result()]

        result = self.run_task(project)

        self.assertTrue(result.successful())
        self.assertEqual(self.mock_transcribe.call_count, 2)
        self.assertEqual(self.mock_select.call_count, 1)
        self.assertEqual(self.mock_upload.call_count, 2)

    def test_non_retryable_error_fails_fast_without_retrying(self):
        project = self.make_project()
        self.mock_select.side_effect = AIAuthenticationError("bad key")

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, AIAuthenticationError)
        self.assertEqual(self.mock_select.call_count, 1)
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "AIAuthenticationError")
        self.assertIsNotNone(project.pipeline_error_at)
        self.assertEqual(project.pipeline_stage_status["transcribed"], "completed")
        self.assertEqual(project.pipeline_stage_status["clips_selected"], "failed")

    def test_retryable_error_marks_project_failed_once_retries_are_exhausted(self):
        project = self.make_project()
        self.mock_select.side_effect = AIServerError("still down")

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(self.mock_select.call_count, process_initial_ingestion.max_retries + 1)
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(self.mock_transcribe.call_count, 1)

    def test_redispatching_a_failed_project_resumes_at_the_failed_stage(self):
        project = self.make_project()
        self.mock_select.side_effect = AIAuthenticationError("bad key")
        self.run_task(project)

        self.mock_select.side_effect = None
        self.mock_select.return_value = selection_result()
        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertEqual(self.mock_transcribe.call_count, 1)
        self.assertEqual(self.mock_select.call_count, 2)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.pipeline_error_code, "")

    def test_redispatching_a_project_failed_after_selection_reaches_awaiting_approval(self):
        project = self.make_project(transcript_data=[{"text": "hi", "start": 0.0, "end": 1.0}])
        VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5)
        VideoProject.objects.filter(pk=project.id).update(
            pipeline_stage=PipelineStage.FAILED,
            status=VideoProject.Status.FAILED,
            pipeline_stage_status={
                "audio_extracted": "completed",
                "transcribed": "completed",
                "clips_selected": "completed",
            },
        )

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.mock_select.assert_not_called()

    def test_retry_countdown_is_exponential_with_bounded_jitter(self):
        for retries in range(5):
            ceiling = min(30 * 2 ** retries, 600)
            for _ in range(20):
                countdown = _retry_countdown(retries)
                self.assertGreaterEqual(countdown, ceiling / 2)
                self.assertLessEqual(countdown, ceiling)
        self.assertLessEqual(_retry_countdown(50), 600)


class LegacyRowTests(IngestionHarness, TestCase):
    def test_existing_transcript_skips_transcription(self):
        project = self.make_project(transcript_data=[{"text": "hi", "start": 0.0, "end": 1.0}])
        project.metadata = {"full_text": "hi", "duration": 10.0, "resolution": [1920, 1080]}
        project.save()

        self.run_task(project)

        project.refresh_from_db()
        self.mock_transcribe.assert_not_called()
        self.mock_select.assert_called_once()
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)

    def test_existing_clips_are_reused_not_duplicated(self):
        project = self.make_project(transcript_data=[{"text": "hi", "start": 0.0, "end": 1.0}])
        VideoClip.objects.create(project=project, title="old", start_time=0, end_time=5)

        self.run_task(project)

        project.refresh_from_db()
        self.mock_transcribe.assert_not_called()
        self.mock_select.assert_not_called()
        self.assertEqual(project.clips.count(), 1)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    def test_existing_metadata_keys_survive_ingestion(self):
        project = self.make_project(
            metadata={"max_clips": 3, "subtitle_size": "large", "niche": "gaming"}
        )

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.metadata["max_clips"], 3)
        self.assertEqual(project.metadata["subtitle_size"], "large")
        self.assertEqual(project.metadata["niche"], "gaming")
        self.assertEqual(project.metadata["full_text"], "Hello world")


class MergeMetadataTests(IngestionHarness, TestCase):
    def test_existing_probe_and_transcript_keys_are_never_overwritten(self):
        project = self.make_project(
            metadata={"duration": 10.0, "resolution": [1, 1], "full_text": "kept", "niche": "x"}
        )

        _merge_metadata(
            project.id,
            {"duration": 99.0, "resolution": [9, 9], "full_text": "new", "extra": 1},
        )

        project.refresh_from_db()
        self.assertEqual(
            project.metadata,
            {"duration": 10.0, "resolution": [1, 1], "full_text": "kept", "niche": "x", "extra": 1},
        )

    def test_missing_keys_are_written(self):
        project = self.make_project(metadata={"niche": "x"})

        _merge_metadata(project.id, {"duration": 5.0})

        project.refresh_from_db()
        self.assertEqual(project.metadata, {"niche": "x", "duration": 5.0})

    def test_merge_does_not_act_when_the_caller_lost_ownership(self):
        project = self.make_project(metadata={"niche": "x"})
        advance(project.id, PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED)

        with self.assertRaises(PipelineConflictError):
            _merge_metadata(
                project.id, {"duration": 5.0}, expected_stage=PipelineStage.UPLOADED
            )

        project.refresh_from_db()
        self.assertEqual(project.metadata, {"niche": "x"})


class ConcurrencyTests(IngestionHarness, TestCase):
    def test_locked_project_is_not_processed_by_a_second_worker(self):
        project = self.make_project()
        lock = self.redis.lock(f"lock:pipeline:project:{project.id}")
        lock.acquire()

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertIn("Skipped", result.result)
        self.mock_transcribe.assert_not_called()
        self.mock_select.assert_not_called()
        self.assertEqual(project.pipeline_stage, PipelineStage.UPLOADED)
        self.assertNotEqual(project.status, VideoProject.Status.FAILED)

    def test_lost_compare_and_set_stops_the_loser_without_failing_the_project(self):
        project = self.make_project()

        def other_worker_wins(*args, **kwargs):
            # Simulates a second worker finishing source preparation + transcription
            # while this one is still inside the transcription call.
            advance(project.id, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED)
            return transcription_result()

        self.mock_transcribe.side_effect = other_worker_wins

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertIn("Skipped", result.result)
        self.mock_select.assert_not_called()
        self.assertEqual(project.pipeline_stage, PipelineStage.TRANSCRIBED)
        self.assertNotEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.clips.count(), 0)

    def test_failure_after_losing_ownership_does_not_fail_the_project(self):
        project = self.make_project()

        def other_worker_wins_then_this_one_fails(*args, **kwargs):
            advance(project.id, PipelineStage.AUDIO_EXTRACTED, PipelineStage.TRANSCRIBED)
            raise AIAuthenticationError("bad key")

        self.mock_transcribe.side_effect = other_worker_wins_then_this_one_fails

        result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.failed())
        self.assertEqual(project.pipeline_stage, PipelineStage.TRANSCRIBED)
        self.assertNotEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_stage_status["transcribed"], "completed")

    def test_redis_down_never_processes_without_the_lock(self):
        project = self.make_project()
        self.redis = DownRedis()
        self._patch(REDIS_CLIENT, return_value=self.redis)

        result = self.run_task(project)

        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, RetryableAIError)
        self.mock_transcribe.assert_not_called()
        self.mock_select.assert_not_called()
        self.mock_upload.assert_not_called()


class RenderDispatchTests(IngestionHarness, TestCase):
    def test_auto_render_bypass_dispatches_one_render_task_per_clip_on_commit(self):
        self.mock_select.return_value = selection_result(count=3)
        project = self.make_project(auto_render_bypass=True)

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.assertEqual(len(callbacks), 3)
        self.assertEqual(self.mock_render.call_count, 3)
        self.assertEqual(project.status, VideoProject.Status.RENDERING)
        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)

    def test_render_is_not_dispatched_before_the_transaction_commits(self):
        project = self.make_project(auto_render_bypass=True)

        with self.captureOnCommitCallbacks(execute=False):
            self.run_task(project)

        self.mock_render.assert_not_called()

    def test_redelivered_task_does_not_dispatch_renders_twice(self):
        project = self.make_project(auto_render_bypass=True)

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)
        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        self.assertEqual(self.mock_render.call_count, 1)
        self.mock_transcribe.assert_called_once()


class NoOpenTransactionTests(IngestionHarness, TransactionTestCase):
    """TransactionTestCase: TestCase would wrap everything in an atomic block."""

    def test_no_transaction_is_open_during_heavy_io(self):
        observed = {}

        def probe(name, value):
            def inner(*args, **kwargs):
                observed[name] = connection.in_atomic_block
                return value
            return inner

        self.mock_transcribe.side_effect = probe("transcribe", transcription_result())
        self.mock_select.side_effect = probe("select", selection_result())
        self.mock_upload.side_effect = probe("upload", "r2_key")
        self.mock_proxy.side_effect = probe("proxy", None)
        project = self.make_project()

        self.run_task(project)

        self.assertEqual(
            observed,
            {"transcribe": False, "select": False, "upload": False, "proxy": False},
        )
