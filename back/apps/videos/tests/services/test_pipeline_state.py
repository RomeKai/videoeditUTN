from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import (
    InvalidTransitionError,
    PipelineConflictError,
    PipelineStage,
    StageStatus,
    advance,
    advance_to,
    can_transition,
    infer_completed_stage,
    last_completed_stage,
    mark_failed,
    record_error,
    reopen,
    set_stage_status,
)

User = get_user_model()


class PipelineStateTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="ps", email="ps@test.com", password="pw")
        workspace = Workspace.objects.create(name="PS Workspace", owner=user)
        self.project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="PS")

    def test_new_project_has_safe_defaults(self):
        self.assertEqual(self.project.pipeline_stage, PipelineStage.UPLOADED)
        self.assertEqual(self.project.pipeline_stage_status, {})
        self.assertEqual(self.project.pipeline_attempts, 0)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertIsNone(self.project.pipeline_error_at)

    def test_transition_table(self):
        self.assertTrue(can_transition(PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED))
        self.assertTrue(can_transition(PipelineStage.TRANSCRIBED, PipelineStage.FAILED))
        self.assertFalse(can_transition(PipelineStage.UPLOADED, PipelineStage.CLIPS_SELECTED))
        self.assertFalse(can_transition(PipelineStage.COMPLETED, PipelineStage.UPLOADED))
        self.assertTrue(can_transition(PipelineStage.FAILED, PipelineStage.TRANSCRIBED))

    def test_advance_moves_stage_and_marks_it_completed(self):
        advance(self.project.id, PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED)

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.AUDIO_EXTRACTED)
        self.assertEqual(
            self.project.pipeline_stage_status[PipelineStage.AUDIO_EXTRACTED], StageStatus.COMPLETED
        )

    def test_advance_with_stale_expected_stage_raises_conflict(self):
        advance(self.project.id, PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED)

        # A second worker still believes the project is at UPLOADED: rowcount 0.
        with self.assertRaises(PipelineConflictError):
            advance(self.project.id, PipelineStage.UPLOADED, PipelineStage.AUDIO_EXTRACTED)

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.AUDIO_EXTRACTED)

    def test_advance_rejects_invalid_transition(self):
        with self.assertRaises(InvalidTransitionError):
            advance(self.project.id, PipelineStage.UPLOADED, PipelineStage.CLIPS_SELECTED)

    def test_advance_to_walks_each_intermediate_stage(self):
        advance_to(self.project.id, PipelineStage.UPLOADED, PipelineStage.TRANSCRIBED)

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.TRANSCRIBED)
        self.assertEqual(
            self.project.pipeline_stage_status[PipelineStage.AUDIO_EXTRACTED], StageStatus.COMPLETED
        )

    def test_set_stage_status_merges_without_dropping_other_stages(self):
        set_stage_status(self.project.id, PipelineStage.TRANSCRIBED, StageStatus.RUNNING)
        set_stage_status(self.project.id, PipelineStage.AUDIO_EXTRACTED, StageStatus.COMPLETED)
        set_stage_status(self.project.id, PipelineStage.TRANSCRIBED, StageStatus.FAILED)

        self.project.refresh_from_db()
        self.assertEqual(
            self.project.pipeline_stage_status,
            {"transcribed": "failed", "audio_extracted": "completed"},
        )

    def test_completed_stage_is_never_downgraded(self):
        set_stage_status(self.project.id, PipelineStage.TRANSCRIBED, StageStatus.COMPLETED)
        set_stage_status(self.project.id, PipelineStage.TRANSCRIBED, StageStatus.RUNNING)

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage_status["transcribed"], "completed")

    def test_mark_failed_sets_stage_and_project_status(self):
        mark_failed(self.project.id)

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)

    def test_mark_failed_leaves_finished_projects_alone(self):
        VideoProject.objects.filter(pk=self.project.id).update(
            pipeline_stage=PipelineStage.COMPLETED, status=VideoProject.Status.COMPLETED
        )

        mark_failed(self.project.id)

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)

    def test_reopen_resumes_failed_project_at_last_completed_stage(self):
        advance_to(self.project.id, PipelineStage.UPLOADED, PipelineStage.TRANSCRIBED)
        record_error(self.project.id, "Boom")
        mark_failed(self.project.id)
        self.project.refresh_from_db()

        resume_at = last_completed_stage(self.project)
        reopen(self.project.id, resume_at)

        self.project.refresh_from_db()
        self.assertEqual(resume_at, PipelineStage.TRANSCRIBED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.TRANSCRIBED)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertIsNone(self.project.pipeline_error_at)

    def test_record_error_stores_code_and_timestamp(self):
        record_error(self.project.id, "AIAuthenticationError")

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_error_code, "AIAuthenticationError")
        self.assertIsNotNone(self.project.pipeline_error_at)

    def test_infer_completed_stage_for_rows_without_pipeline_state(self):
        self.assertEqual(infer_completed_stage(self.project), PipelineStage.UPLOADED)

        self.project.transcript_data = [{"text": "hi", "start": 0.0, "end": 1.0}]
        self.assertEqual(infer_completed_stage(self.project), PipelineStage.TRANSCRIBED)

        self.project.save()
        VideoClip.objects.create(project=self.project, title="c", start_time=0, end_time=5)
        self.assertEqual(infer_completed_stage(self.project), PipelineStage.CLIPS_SELECTED)
