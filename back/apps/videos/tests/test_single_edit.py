"""
"Editar el video completo" (project_type=single_edit, screen D5): the whole video
is the one clip, there is no selection LLM and no review, and it renders at once.
It is paid by the creation reservation, charged only when the render is delivered.
"""

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Transaction, Wallet
from apps.payments.services.wallet_service import reserve_funds, rollback_reservation
from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoClip, VideoProject
from apps.videos.services.ai.errors import AINoSpeechDetectedError
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.services.media_validation import MediaInfo
from apps.videos.tasks import (
    _refund_ingestion_reservation, _stage_whole_video, finalize_project_render, render_clip_task,
)
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness

User = get_user_model()

SINGLE_EDIT = VideoProject.Type.SINGLE_EDIT


class SingleEditPipelineTests(IngestionHarness, TestCase):
    def make_whole_video(self, metadata=None, paid=True, **kwargs):
        # The create view writes priced_duration_s for an uploaded file and
        # reservation_id for every project.
        project = self.make_project(
            project_type=SINGLE_EDIT, metadata={"priced_duration_s": 10, **(metadata or {})}, **kwargs,
        )
        if paid:
            self.txn = self.reserve(project)
        return project

    def reserve(self, project, amount="10.00"):
        self.wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("100.00"), reserved_balance=0)
        txn = reserve_funds(self.wallet.id, Decimal(amount))
        project.refresh_from_db()
        VideoProject.objects.filter(pk=project.pk).update(
            metadata={**project.metadata, "reservation_id": str(txn.id)},
        )
        return txn

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def run_to_render(self, project):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.run_task(project)
        project.refresh_from_db()
        return result

    # The flow

    def test_the_whole_video_becomes_one_clip_without_the_selection_llm(self):
        project = self.make_whole_video()

        result = self.run_to_render(project)

        self.assertTrue(result.successful())
        self.mock_select.assert_not_called()
        clip = VideoClip.objects.get(project=project)
        self.assertEqual((clip.start_time, clip.end_time), (0.0, 10.0))  # the probed duration
        self.assertEqual(clip.title, "Pipeline")
        self.assertFalse(AIUsageRecord.objects.filter(project=project, stage="selection").exists())

    def test_it_renders_at_once_without_waiting_for_a_review(self):
        project = self.make_whole_video(auto_render_bypass=False)

        self.run_to_render(project)

        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(project.status, VideoProject.Status.RENDERING)
        self.mock_render.assert_called_once()

    def test_it_is_still_transcribed_and_moderated(self):
        # The subtitles come from the transcript, and the content is still checked.
        project = self.make_whole_video()

        self.run_to_render(project)

        self.mock_transcribe.assert_called_once()
        self.mock_moderate.assert_called_once()
        self.assertTrue(project.transcript_data)

    def test_a_clips_project_still_selects_and_waits_for_review(self):
        project = self.make_project()

        self.run_to_render(project)

        self.mock_select.assert_called_once()
        self.mock_render.assert_not_called()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    def test_a_single_edit_never_priced_for_a_whole_render_keeps_the_clips_flow(self):
        # A link, a row created before this flow or an admin edit: no priced duration.
        project = self.make_project(project_type=SINGLE_EDIT)

        self.run_to_render(project)

        self.mock_select.assert_called_once()
        self.mock_render.assert_not_called()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    # Payment: the render is what it pays for

    def test_the_reservation_is_not_charged_before_the_render_is_delivered(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)

        self.run_to_render(project)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_a_delivered_render_is_charged(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        self.run_to_render(project)
        VideoClip.objects.filter(project=project).update(status=VideoClip.Status.COMPLETED)

        self.assertEqual(finalize_project_render(project.id), PipelineStage.COMPLETED)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_a_failed_render_is_refunded(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        self.run_to_render(project)
        VideoClip.objects.filter(project=project).update(status=VideoClip.Status.FAILED)

        self.assertEqual(finalize_project_render(project.id), PipelineStage.PARTIAL)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_refunded_project_that_is_dispatched_again_is_not_rendered_for_free(self):
        project = self.make_whole_video(metadata={"duration": 10.0}, paid=False)
        txn = self.reserve(project)
        rollback_reservation(txn.id)  # refunded when it failed earlier
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.MODERATED)

        self.run_to_render(project)

        self.mock_render.assert_not_called()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "WholeVideoNotPaidError")

    def test_a_refunded_project_resumed_at_the_dispatch_is_not_rendered_for_free(self):
        project = self.make_whole_video(metadata={"duration": 10.0}, paid=False)
        txn = self.reserve(project)
        rollback_reservation(txn.id)
        VideoClip.objects.create(project=project, title="Pipeline", start_time=0, end_time=10)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.RENDER_DISPATCHED)

        self.run_to_render(project)

        self.mock_render.assert_not_called()
        self.assertEqual(project.pipeline_error_code, "WholeVideoNotPaidError")

    def test_a_failed_project_whose_video_was_never_rendered_is_refunded(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        VideoClip.objects.create(project=project, title="Pipeline", start_time=0, end_time=10)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)

        _refund_ingestion_reservation(project.id)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)

    def finish_render_after_the_project_failed(self, clip_status):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        clip = VideoClip.objects.create(
            project=project, title="Pipeline", start_time=0, end_time=10, status=VideoClip.Status.RENDERING,
        )
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)
        VideoClip.objects.filter(pk=clip.pk).update(status=clip_status)
        finalize_project_render(project.id)  # what the render task calls when it ends
        txn.refresh_from_db()
        return txn

    def test_a_video_delivered_after_the_project_failed_is_charged(self):
        self.assertEqual(self.finish_render_after_the_project_failed(VideoClip.Status.COMPLETED).status,
                         Transaction.Status.COMPLETED)

    def test_a_render_that_fails_after_the_project_failed_is_refunded(self):
        self.assertEqual(self.finish_render_after_the_project_failed(VideoClip.Status.FAILED).status,
                         Transaction.Status.FAILED)

    def test_a_crash_between_closing_and_charging_is_charged_on_the_next_delivery(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        clip = VideoClip.objects.create(
            project=project, title="Pipeline", start_time=0, end_time=10,
            status=VideoClip.Status.COMPLETED, s3_object_key="clips/done.mp4",
        )
        # The worker died after the project was closed, before the charge.
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.COMPLETED)

        render_clip_task.apply(args=[str(clip.id)])  # the redelivered message

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

    def test_a_queued_render_of_a_refunded_project_is_not_rendered(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        rollback_reservation(txn.id)  # the project failed and was refunded
        clip = VideoClip.objects.create(project=project, title="Pipeline", start_time=0, end_time=10)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)

        with patch("apps.videos.services.render_engine.RenderEngine.render_clip") as render:
            render_clip_task.apply(args=[str(clip.id)])

        render.assert_not_called()
        clip.refresh_from_db()
        self.assertEqual(clip.status, VideoClip.Status.FAILED)

    def test_a_paid_whole_video_is_rendered(self):
        project = self.make_whole_video()
        clip = VideoClip.objects.create(project=project, title="Pipeline", start_time=0, end_time=10)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.RENDER_DISPATCHED)

        with patch("apps.videos.services.render_engine.RenderEngine.render_clip") as render:
            render_clip_task.apply(args=[str(clip.id)])

        render.assert_called_once()

    def test_a_refunded_project_dispatched_again_runs_nothing(self):
        # A moderation override (or any re-dispatch) after the refund.
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        rollback_reservation(txn.id)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)

        self.run_to_render(project)

        self.mock_transcribe.assert_not_called()
        self.assertFalse(VideoClip.objects.filter(project=project).exists())
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "WholeVideoNotPaidError")

    def test_a_failed_project_whose_video_is_rendering_keeps_its_reservation(self):
        project = self.make_whole_video(paid=False)
        txn = self.reserve(project)
        VideoClip.objects.create(
            project=project, title="Pipeline", start_time=0, end_time=10, status=VideoClip.Status.RENDERING,
        )
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)

        _refund_ingestion_reservation(project.id)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    # The source

    def test_a_source_longer_than_what_was_priced_is_never_rendered(self):
        # The file was replaced with a PATCH after it was priced at 2 s.
        project = self.make_whole_video(metadata={"priced_duration_s": 2}, paid=False)
        txn = self.reserve(project, amount="1.00")

        self.run_to_render(project)

        self.mock_transcribe.assert_not_called()  # checked before paying for anything else
        self.mock_render.assert_not_called()
        self.assertFalse(VideoClip.objects.filter(project=project).exists())
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "WholeVideoSourceChangedError")
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)  # refunded

    @override_settings(AI_SINGLE_EDIT_MAX_DURATION_SECONDS=5)
    def test_a_long_clips_project_turned_into_a_whole_video_in_the_admin_is_not_rendered(self):
        project = self.make_whole_video(metadata={"priced_duration_s": 1800})

        self.run_to_render(project)

        self.mock_transcribe.assert_not_called()
        self.mock_render.assert_not_called()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)

    def test_without_a_stored_duration_the_source_is_measured(self):
        project = self.make_whole_video()
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.MODERATED)

        with patch("apps.videos.tasks.ingestion._probe_video", return_value=(9.5, "1920x1080")) as probe:
            _stage_whole_video(project.id)

        probe.assert_called_once()
        project.refresh_from_db()
        self.assertEqual(VideoClip.objects.get(project=project).end_time, 9.5)
        self.assertEqual(project.metadata["duration"], 9.5)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)

    def test_a_redelivered_stage_never_creates_a_second_clip(self):
        project = self.make_whole_video(metadata={"duration": 10.0})
        VideoClip.objects.create(project=project, title="Pipeline", start_time=0, end_time=10)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.MODERATED)

        _stage_whole_video(project.id)

        project.refresh_from_db()
        self.assertEqual(VideoClip.objects.filter(project=project).count(), 1)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)

    def test_a_video_without_speech_renders_without_subtitles(self):
        self.mock_transcribe.side_effect = AINoSpeechDetectedError(provider="groq", model="whisper")
        project = self.make_whole_video()

        self.run_to_render(project)

        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(project.transcript_data, [])
        self.assertTrue(project.metadata["no_speech"])
        self.mock_moderate.assert_called_once_with("Pipeline")  # only the title is left to check
        self.mock_render.assert_called_once()

    def test_a_clips_project_without_speech_still_fails(self):
        # Clips are chosen from what is said: there is nothing to choose from.
        self.mock_transcribe.side_effect = AINoSpeechDetectedError(provider="groq", model="whisper")
        project = self.make_project()

        self.run_to_render(project)

        self.mock_select.assert_not_called()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "AINoSpeechDetectedError")


@override_settings(AI_SINGLE_EDIT_MAX_DURATION_SECONDS=600)
class SingleEditCreationTests(TestCase):
    """What the API accepts: the whole video is priced at creation, so it must be a short file."""

    def setUp(self):
        self.user = User.objects.create_user(username="whole", email="whole@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="Whole", owner=self.user).members.add(self.user)
        wallet, _ = Wallet.objects.get_or_create(workspace=self.user.workspaces.first())
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("1000.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def send(self, method, url, body, format="json", duration=100.0):
        probed = MediaInfo(duration_s=duration, width=1920, height=1080, mime="video/mp4")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("apps.videos.services.media_validation.validate_upload", return_value=probed), \
                patch("apps.videos.views.process_initial_ingestion.delay") as delay:
            return getattr(self.client, method)(url, body, format=format), delay

    def upload(self, duration=100.0, **extra):
        return self.send("post", reverse("project-list"), {
            "title": "Video completo", **extra,
            "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
        }, format="multipart", duration=duration)

    def link(self, **extra):
        return self.send("post", reverse("project-list"), {
            "title": "Link", "video_url": "https://www.youtube.com/watch?v=x", **extra,
        })

    def test_an_uploaded_whole_video_keeps_the_duration_it_was_priced_for(self):
        response, delay = self.upload(project_type=SINGLE_EDIT)

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.project_type, SINGLE_EDIT)
        self.assertEqual(project.metadata["priced_duration_s"], 100)
        self.assertEqual(Transaction.objects.get(pk=project.metadata["reservation_id"]).amount, Decimal("50.00"))
        delay.assert_called_once()

    def test_the_file_of_a_whole_video_cannot_be_replaced(self):
        response, _ = self.upload(project_type=SINGLE_EDIT)

        patched, _ = self.send("patch", reverse("project-detail", args=[response.data["id"]]), {
            "source_file": SimpleUploadedFile("long.mp4", b"0" * 10, content_type="video/mp4"),
        }, format="multipart", duration=5000.0)

        self.assertEqual(patched.status_code, 400, patched.content[:300])
        self.assertIn("source_file", patched.data)
        metadata = VideoProject.objects.get(pk=response.data["id"]).metadata
        self.assertEqual(metadata["source_media"]["duration_s"], 100.0)
        self.assertEqual(metadata["priced_duration_s"], 100)

    def test_replacing_the_file_of_a_clips_project_keeps_the_priced_duration(self):
        response, _ = self.upload()

        self.send("patch", reverse("project-detail", args=[response.data["id"]]), {
            "source_file": SimpleUploadedFile("long.mp4", b"0" * 10, content_type="video/mp4"),
        }, format="multipart", duration=5000.0)

        metadata = VideoProject.objects.get(pk=response.data["id"]).metadata
        self.assertEqual(metadata["source_media"]["duration_s"], 5000.0)
        self.assertEqual(metadata["priced_duration_s"], 100)

    def test_the_pipeline_fields_cannot_be_written(self):
        response, _ = self.upload(project_type=SINGLE_EDIT)

        self.send("patch", reverse("project-detail", args=[response.data["id"]]), {
            "pipeline_stage": "failed", "pipeline_error_code": "x", "pipeline_attempts": 9,
        })

        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.pipeline_stage, PipelineStage.UPLOADED)
        self.assertEqual((project.pipeline_error_code, project.pipeline_attempts), ("", 0))

    def test_a_whole_video_has_no_preview(self):
        response, _ = self.upload(project_type=SINGLE_EDIT)
        clip = VideoClip.objects.create(project_id=response.data["id"], title="v", start_time=0, end_time=10)

        with patch("apps.videos.tasks.render_clip_preview_task.delay") as delay:
            preview = self.client.post(f"/api/v1/clips/{clip.id}/preview/")

        self.assertEqual(preview.status_code, 409, preview.content[:300])
        delay.assert_not_called()

    def test_a_whole_video_longer_than_the_limit_is_rejected_before_anything_is_reserved(self):
        response, delay = self.upload(duration=601.0, project_type=SINGLE_EDIT)

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("10 min", str(response.data["project_type"]))
        delay.assert_not_called()
        self.assertFalse(Transaction.objects.exists())

        at_the_limit, _ = self.upload(duration=600.0, project_type=SINGLE_EDIT)
        self.assertEqual(at_the_limit.status_code, 201, at_the_limit.content[:300])

    def test_the_limit_is_only_for_the_whole_video(self):
        response, _ = self.upload(duration=601.0)

        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_a_whole_video_from_a_link_is_rejected_before_anything_is_reserved(self):
        response, delay = self.link(project_type=SINGLE_EDIT)

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("project_type", response.data)
        delay.assert_not_called()
        self.assertFalse(VideoProject.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_a_link_can_still_be_used_to_get_clips(self):
        response, _ = self.link()

        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_project_type_cannot_change_after_creation(self):
        response, _ = self.link()

        patched, _ = self.send(
            "patch", reverse("project-detail", args=[response.data["id"]]), {"project_type": SINGLE_EDIT},
        )

        self.assertEqual(patched.status_code, 200, patched.content[:300])
        self.assertEqual(VideoProject.objects.get(pk=response.data["id"]).project_type, VideoProject.Type.REPURPOSE)
