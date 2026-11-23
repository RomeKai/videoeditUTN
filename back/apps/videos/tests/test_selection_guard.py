"""
Pipeline guards around clip selection: a selection without clips never
advances the project, projects stuck by the old behavior resume at selection,
and the user's preferences reach the selector.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.videos.models import AIUsageRecord, VideoClip, VideoProject
from apps.videos.services.ai.contracts import AIExecutionResult, ClipSelectionResult, ProviderUsage
from apps.videos.services.ai.errors import AIEmptySelectionError
from apps.videos.services.ai.pipeline_state import (
    PipelineStage,
    StageStatus,
    last_completed_stage,
)
from apps.videos.tests.test_ingestion_pipeline import (
    LEGACY_SELECT,
    IngestionHarness,
    selection_result,
)


def empty_selection():
    return AIExecutionResult(
        data=ClipSelectionResult(clips=[]),
        usage=ProviderUsage(provider="gemini", model="gemini/gemini-3.8-flash", prompt_tokens=10),
    )


class EmptySelectionGuardTests(IngestionHarness, TestCase):
    def test_empty_v2_selection_fails_the_project_without_advancing(self):
        self.mock_select.return_value = empty_selection()
        project = self.make_project()

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "AIEmptySelectionError")
        self.assertEqual(project.pipeline_stage_status["clips_selected"], StageStatus.FAILED)
        self.assertFalse(project.clips.exists())
        # Non-retryable: one selection call, and the billed call is still recorded.
        self.mock_select.assert_called_once()
        record = AIUsageRecord.objects.get(project=project, stage=AIUsageRecord.Stage.SELECTION)
        self.assertEqual(record.prompt_tokens, 10)

    @override_settings(AI_CORE_V2_ENABLED=False)
    def test_empty_legacy_selection_is_rejected_by_the_stage_too(self):
        project = self.make_project()

        with patch(LEGACY_SELECT, return_value=[]):
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "AIEmptySelectionError")
        self.assertFalse(project.clips.exists())

    def test_redispatch_after_an_empty_selection_runs_selection_again(self):
        self.mock_select.return_value = empty_selection()
        project = self.make_project()
        self.run_task(project)

        self.mock_select.return_value = selection_result(count=2)
        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.clips.count(), 2)
        # Transcription was persisted and is never paid for twice.
        self.mock_transcribe.assert_called_once()


class StuckProjectRecoveryTests(IngestionHarness, TestCase):
    """Projects that reached CLIPS_SELECTED with zero clips before the guard existed."""

    def _stuck_project(self, **kwargs):
        project = self.make_project(auto_render_bypass=True, **kwargs)
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.FAILED,
            status=VideoProject.Status.FAILED,
            transcript_data=[{"start": 0.0, "end": 1.0, "text": "Hello"}],
            metadata={"duration": 10.0, "full_text": "Hello"},
            pipeline_stage_status={
                "audio_extracted": StageStatus.COMPLETED,
                "transcribed": StageStatus.COMPLETED,
                "clips_selected": StageStatus.COMPLETED,
                "render_dispatched": StageStatus.FAILED,
            },
        )
        project.refresh_from_db()
        return project

    def test_resume_point_falls_back_to_selection_when_no_clip_exists(self):
        project = self._stuck_project()
        self.assertEqual(last_completed_stage(project), PipelineStage.TRANSCRIBED)

    def test_resume_point_is_unchanged_when_clips_exist(self):
        project = self._stuck_project()
        VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5)
        self.assertEqual(last_completed_stage(project), PipelineStage.CLIPS_SELECTED)

    def test_redispatch_reselects_and_renders(self):
        project = self._stuck_project()

        self.run_task(project)

        project.refresh_from_db()
        self.mock_select.assert_called_once()
        self.mock_transcribe.assert_not_called()
        self.assertEqual(project.clips.count(), 1)
        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)


class SelectionPreferencesTests(IngestionHarness, TestCase):
    def test_editing_style_and_max_clips_reach_the_selector(self):
        project = self.make_project(editing_style=VideoProject.EditingStyle.HORMOZI)
        VideoProject.objects.filter(pk=project.pk).update(metadata={"max_clips": 2})

        self.run_task(project)

        kwargs = self.mock_select.call_args.kwargs
        self.assertEqual(kwargs["editing_style"], project.editing_style)
        self.assertEqual(kwargs["max_clips"], 2)

    def test_missing_max_clips_is_passed_as_none(self):
        project = self.make_project()

        self.run_task(project)

        self.assertIsNone(self.mock_select.call_args.kwargs["max_clips"])
