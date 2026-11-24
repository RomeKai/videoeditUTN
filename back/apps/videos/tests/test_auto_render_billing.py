"""
"Renderizar sin revisar" (auto_render_bypass) pays for its render: the render
of the selected clips is reserved before they are queued, priced like an
approval, and settled by what was delivered. And a PATCH never writes over an
approval it raced with.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.payments.services.wallet_service import (
    PaymentError, commit_partial_reservation, reserve_funds, rollback_reservation,
)
from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _record_pipeline_failure, finalize_project_render, render_clip_task
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, fund_wallet, selection_result

User = get_user_model()

KEY = "auto_render_reservation_id"


class AutoRenderBillingTests(IngestionHarness, TestCase):
    def setUp(self):
        super().setUp()
        self.wallet = fund_wallet(self.workspace, "100.00")
        self.mock_select.return_value = selection_result(count=3)  # 3 clips of 5 s

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def run_to_render(self, project):
        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)
        project.refresh_from_db()
        return project

    def reservation(self, project):
        return Transaction.objects.get(pk=project.metadata[KEY])

    # Reservation

    def test_the_render_of_the_clips_is_reserved_before_they_are_queued(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))

        txn = self.reservation(project)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)
        self.assertEqual(txn.amount, Decimal("7.50"))  # 15 s, priced like an approval
        self.assertEqual(project.metadata["auto_render_seconds"], 15.0)
        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(self.mock_render.call_count, 3)

    def test_without_enough_tokens_it_waits_for_the_review(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("1.00"))

        project = self.run_to_render(self.make_project(auto_render_bypass=True))

        self.mock_render.assert_not_called()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.metadata["auto_render_skipped"], "insufficient_funds")
        self.assertNotIn(KEY, project.metadata)

    def test_over_the_plan_limit_it_waits_for_the_review(self):
        plan = SubscriptionPlan.objects.create(name="Corto", max_resolution="720p", max_video_duration_seconds=10)
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=plan)

        project = self.run_to_render(self.make_project(auto_render_bypass=True))

        self.mock_render.assert_not_called()
        self.assertEqual(project.metadata["auto_render_skipped"], "plan_limit")

    def test_a_redelivery_reuses_the_reservation(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        first = project.metadata[KEY]
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.CLIPS_SELECTED)

        project = self.run_to_render(project)

        self.assertEqual(project.metadata[KEY], first)
        self.assertEqual(Transaction.objects.filter(status=Transaction.Status.RESERVED).count(), 1)

    def test_a_project_dispatched_again_after_a_refund_reserves_again_for_what_is_left(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        rollback_reservation(project.metadata[KEY])
        clips = list(VideoClip.objects.filter(project=project).order_by("start_time"))
        VideoClip.objects.filter(pk=clips[0].pk).update(status=VideoClip.Status.COMPLETED)
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.CLIPS_SELECTED)

        project = self.run_to_render(project)

        txn = self.reservation(project)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)
        self.assertEqual(txn.amount, Decimal("5.00"))  # the 2 clips still to render

    # Settlement

    def finish(self, *statuses):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        clips = list(VideoClip.objects.filter(project=project).order_by("start_time"))
        for clip, status in zip(clips, statuses):
            VideoClip.objects.filter(pk=clip.pk).update(status=status)
        stage = finalize_project_render(project.id)
        project.refresh_from_db()
        return project, self.reservation(project), stage

    def test_every_clip_delivered_is_charged_whole(self):
        done = VideoClip.Status.COMPLETED
        project, txn, stage = self.finish(done, done, done)

        self.assertEqual(stage, PipelineStage.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(txn.amount, Decimal("7.50"))
        self.assertEqual(self.balances(), (Decimal("92.50"), Decimal("0")))

    def test_only_the_clips_delivered_are_charged(self):
        done, failed = VideoClip.Status.COMPLETED, VideoClip.Status.FAILED
        project, txn, stage = self.finish(done, failed, failed)

        self.assertEqual(stage, PipelineStage.PARTIAL)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(txn.amount, Decimal("2.50"))  # 5 of 15 s
        self.assertEqual(self.balances(), (Decimal("97.50"), Decimal("0")))

    def test_nothing_delivered_is_refunded(self):
        failed = VideoClip.Status.FAILED
        project, txn, stage = self.finish(failed, failed, failed)

        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_it_waits_while_a_clip_renders(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)
        clips = list(VideoClip.objects.filter(project=project))
        VideoClip.objects.filter(pk=clips[0].pk).update(status=VideoClip.Status.RENDERING)

        finalize_project_render(project.id)

        self.assertEqual(self.reservation(project).status, Transaction.Status.RESERVED)

    def test_a_project_failed_before_its_clips_rendered_is_refunded(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))

        _record_pipeline_failure(project.id, RuntimeError("x"), 1, final=True, stage=PipelineStage.RENDER_DISPATCHED)

        self.assertEqual(self.reservation(project).status, Transaction.Status.FAILED)

    def test_a_queued_render_of_a_refunded_project_is_not_rendered(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        rollback_reservation(project.metadata[KEY])
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)
        clip = VideoClip.objects.filter(project=project).first()

        with patch("apps.videos.services.render_engine.RenderEngine.render_clip") as render:
            render_clip_task.apply(args=[str(clip.id)])

        render.assert_not_called()

    def test_a_paid_auto_render_is_rendered(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        clip = VideoClip.objects.filter(project=project).first()

        with patch("apps.videos.services.render_engine.RenderEngine.render_clip") as render:
            render_clip_task.apply(args=[str(clip.id)])

        render.assert_called_once()

    def test_a_clips_project_without_auto_render_reserves_nothing(self):
        project = self.run_to_render(self.make_project())

        self.assertNotIn(KEY, project.metadata)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)


class PartialCommitTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="part", email="part@test.com", password="pw")
        workspace = Workspace.objects.create(name="Part", owner=user)
        self.wallet = fund_wallet(workspace, "100.00")

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def test_it_charges_the_amount_and_returns_the_rest(self):
        txn = reserve_funds(self.wallet.id, Decimal("10.00"))

        commit_partial_reservation(txn.id, Decimal("4.00"))

        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("4.00")))
        self.assertEqual(self.balances(), (Decimal("96.00"), Decimal("0")))

    def test_the_whole_amount_or_more_commits_it_whole(self):
        txn = reserve_funds(self.wallet.id, Decimal("10.00"))

        commit_partial_reservation(txn.id, Decimal("12.00"))

        txn.refresh_from_db()
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("10.00")))
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_nothing_rolls_it_back(self):
        txn = reserve_funds(self.wallet.id, Decimal("10.00"))

        commit_partial_reservation(txn.id, Decimal("0"))

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_settled_reservation_cannot_be_charged_again(self):
        txn = reserve_funds(self.wallet.id, Decimal("10.00"))
        commit_partial_reservation(txn.id, Decimal("4.00"))

        with self.assertRaises(PaymentError):
            commit_partial_reservation(txn.id, Decimal("4.00"))


class PatchDuringApprovalTests(TestCase):
    """A PATCH that read the project before an approval never writes over it."""

    def setUp(self):
        self.user = User.objects.create_user(username="race", email="race@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="Race", owner=self.user).members.add(self.user)
        self.workspace = self.user.workspaces.first()
        self.wallet = fund_wallet(self.workspace, "100.00")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Carrera",
            original_r2_key="videos/1/original/source.mp4",
            status=VideoProject.Status.AWAITING_APPROVAL, add_subtitles=False,
        )

    def stale_patch(self, data):
        """The serializer update of a PATCH that read the row before the approval."""
        from apps.videos.serializers import VideoProjectSerializer

        stale = VideoProject.objects.get(pk=self.project.pk)
        self.approve_in_the_meantime()
        serializer = VideoProjectSerializer(stale, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    def approve_in_the_meantime(self):
        txn = reserve_funds(self.wallet.id, Decimal("5.00"))
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.RENDERING,
            metadata={"render_reservation_id": str(txn.id)},
            approved_segments=[{"start": 0, "end": 5}],
        )
        self.txn = txn

    def test_a_settings_change_racing_the_approval_is_rejected(self):
        from apps.videos.serializers import ProjectRenderingError

        with self.assertRaises(ProjectRenderingError):
            self.stale_patch({"render_layout": "blurred"})

        project = VideoProject.objects.get(pk=self.project.pk)
        self.assertEqual(project.status, VideoProject.Status.RENDERING)
        self.assertEqual(project.metadata["render_reservation_id"], str(self.txn.id))
        self.assertNotEqual(project.render_layout, "blurred")

    def test_a_title_change_racing_the_approval_keeps_what_the_approval_wrote(self):
        self.stale_patch({"title": "Nuevo título"})

        project = VideoProject.objects.get(pk=self.project.pk)
        self.assertEqual(project.title, "Nuevo título")
        self.assertEqual(project.status, VideoProject.Status.RENDERING)
        self.assertEqual(project.metadata["render_reservation_id"], str(self.txn.id))
        self.assertEqual(project.approved_segments, [{"start": 0, "end": 5}])

    def test_a_patch_while_rendering_gets_a_409(self):
        VideoProject.objects.filter(pk=self.project.pk).update(status=VideoProject.Status.RENDERING)

        response = self.client.patch(
            reverse("project-detail", args=[self.project.pk]), {"render_layout": "blurred"}, format="json",
        )

        self.assertEqual(response.status_code, 409, response.content[:300])

    def test_a_patch_only_writes_its_own_fields(self):
        stale = VideoProject.objects.get(pk=self.project.pk)
        VideoProject.objects.filter(pk=self.project.pk).update(metadata={"written": "by the pipeline"})
        from apps.videos.serializers import VideoProjectSerializer

        serializer = VideoProjectSerializer(stale, data={"render_layout": "blurred"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        project = VideoProject.objects.get(pk=self.project.pk)
        self.assertEqual(project.render_layout, "blurred")
        self.assertEqual(project.metadata, {"written": "by the pipeline"})

    def test_the_approval_keeps_metadata_written_while_it_ran(self):
        from apps.videos.services.segments import validate_approved_segments as real_validate

        def the_pipeline_writes(segments):
            VideoProject.objects.filter(pk=self.project.pk).update(metadata={"written": "meanwhile"})
            return real_validate(segments)

        with patch("apps.videos.views.validate_approved_segments", side_effect=the_pipeline_writes), \
                patch("apps.videos.tasks.render_video_segments.delay"):
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.pk]),
                {"approved_segments": [{"start": 0, "end": 5, "text": "hola"}]}, format="json",
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        metadata = VideoProject.objects.get(pk=self.project.pk).metadata
        self.assertEqual(metadata["written"], "meanwhile")
        self.assertIn("render_reservation_id", metadata)
