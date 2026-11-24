"""
"Editar el video completo" (project_type=single_edit, screen D5): the whole video
is the one clip, there is no selection LLM and no review, and it renders at once.
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
from apps.payments.services.wallet_service import reserve_funds
from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.services.media_validation import MediaInfo
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness

User = get_user_model()

SINGLE_EDIT = VideoProject.Type.SINGLE_EDIT


class SingleEditPipelineTests(IngestionHarness, TestCase):
    def test_the_whole_video_becomes_one_clip_without_the_selection_llm(self):
        project = self.make_project(project_type=SINGLE_EDIT)

        with self.captureOnCommitCallbacks(execute=True):
            result = self.run_task(project)

        project.refresh_from_db()
        self.assertTrue(result.successful())
        self.mock_select.assert_not_called()
        clip = VideoClip.objects.get(project=project)
        self.assertEqual((clip.start_time, clip.end_time), (0.0, 10.0))  # the probed duration
        self.assertEqual(clip.title, "Pipeline")
        self.assertFalse(AIUsageRecord.objects.filter(project=project, stage="selection").exists())

    def test_it_renders_at_once_without_waiting_for_a_review(self):
        project = self.make_project(project_type=SINGLE_EDIT, auto_render_bypass=False)

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(project.status, VideoProject.Status.RENDERING)
        self.mock_render.assert_called_once()

    def test_it_is_still_transcribed_and_moderated(self):
        # The subtitles come from the transcript, and the content is still checked.
        project = self.make_project(project_type=SINGLE_EDIT)

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        self.mock_transcribe.assert_called_once()
        self.mock_moderate.assert_called_once()

    def test_a_redelivery_never_creates_a_second_clip(self):
        project = self.make_project(project_type=SINGLE_EDIT)

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)
            self.run_task(project)

        self.assertEqual(VideoClip.objects.filter(project=project).count(), 1)

    def test_the_creation_reservation_is_charged_once_the_clip_exists(self):
        wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("100.00"), reserved_balance=0)
        project = self.make_project(project_type=SINGLE_EDIT)
        txn = reserve_funds(wallet.id, Decimal("10.00"))
        VideoProject.objects.filter(pk=project.pk).update(metadata={"reservation_id": str(txn.id)})

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

    def test_without_a_stored_duration_the_source_is_measured(self):
        from apps.videos.tasks import _stage_whole_video

        project = self.make_project(project_type=SINGLE_EDIT)
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.MODERATED, metadata={},
        )

        with patch("apps.videos.tasks.ingestion._probe_video", return_value=(42.5, "1920x1080")) as probe:
            _stage_whole_video(project.id)

        probe.assert_called_once()
        project.refresh_from_db()
        self.assertEqual(VideoClip.objects.get(project=project).end_time, 42.5)
        self.assertEqual(project.metadata["duration"], 42.5)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)

    def test_a_clips_project_still_selects_and_waits_for_review(self):
        project = self.make_project()

        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)

        project.refresh_from_db()
        self.mock_select.assert_called_once()
        self.mock_render.assert_not_called()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)


class SingleEditCreationTests(TestCase):
    """What the API accepts: the whole video is priced at creation, so it must be a file."""

    def setUp(self):
        self.user = User.objects.create_user(username="whole", email="whole@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="Whole", owner=self.user).members.add(self.user)
        wallet, _ = Wallet.objects.get_or_create(workspace=self.user.workspaces.first())
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("1000.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def create(self, body, format="json"):
        probed = MediaInfo(duration_s=100.0, width=1920, height=1080, mime="video/mp4")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("apps.videos.services.media_validation.validate_upload", return_value=probed), \
                patch("apps.videos.views.process_initial_ingestion.delay") as delay:
            return self.client.post(reverse("project-list"), body, format=format), delay

    def upload(self, **extra):
        return self.create({
            "title": "Video completo", **extra,
            "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
        }, format="multipart")

    def test_an_uploaded_whole_video_is_created_and_priced_for_its_full_duration(self):
        response, delay = self.upload(project_type=SINGLE_EDIT)

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.project_type, SINGLE_EDIT)
        self.assertEqual(Transaction.objects.get(pk=project.metadata["reservation_id"]).amount, Decimal("50.00"))
        delay.assert_called_once()

    def test_a_whole_video_from_a_link_is_rejected_before_anything_is_reserved(self):
        response, delay = self.create({
            "title": "Link", "video_url": "https://www.youtube.com/watch?v=x", "project_type": SINGLE_EDIT,
        })

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("project_type", response.data)
        delay.assert_not_called()
        self.assertFalse(VideoProject.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_a_link_can_still_be_used_to_get_clips(self):
        response, _ = self.create({"title": "Link", "video_url": "https://www.youtube.com/watch?v=x"})

        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_the_project_type_cannot_change_after_creation(self):
        response, _ = self.create({"title": "Link", "video_url": "https://www.youtube.com/watch?v=x"})

        self.client.patch(
            reverse("project-detail", args=[response.data["id"]]), {"project_type": SINGLE_EDIT}, format="json",
        )

        self.assertEqual(VideoProject.objects.get(pk=response.data["id"]).project_type, VideoProject.Type.REPURPOSE)
