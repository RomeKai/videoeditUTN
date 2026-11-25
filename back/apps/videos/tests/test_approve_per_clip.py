"""
Approving the review renders each approved segment as its own clip (D2
Revisión -> D3 Clips), the way the review screen maps them (the
approve-paper-edit mock): a segment with the ``clip_id`` of a DRAFT clip
updates it, any other one creates a clip, and the drafts left out are
deleted. The render is reserved before anything is queued, one
render_clip_task per clip goes out after the commit, and the reservation is
settled by the seconds delivered, like "Renderizar sin revisar".

An approval made before this (one export of every segment) still renders
through render_video_segments: test_reservation_settlement.py and
test_render_recovery.py (``approve_legacy``).
"""

import os
import shutil
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.payments.services.wallet_service import reserve_funds, rollback_reservation
from apps.users.models import Workspace
from apps.videos.models import ClipPreview, VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.services.render_pricing import render_cost
from apps.videos.services.segments import validate_approved_segments
from apps.videos.tasks import finalize_project_render, render_clip_task, render_video_segments
from apps.videos.tasks.ingestion import RENDER_REDISPATCH_SECONDS, _run_ingestion, _whole_video_render_allowed
from apps.videos.tasks.render import RENDER_TAKEOVER_HEADER
from apps.videos.tasks.render_recovery import RENDER_RECOVERY_MAX_REQUEUES, recover_stuck_renders_now
from apps.videos.tests.fakes import CapturedTaskFailures, FakeRedis
from apps.videos.tests.test_approved_segments_source import (
    CLIP_DELAY, DELAY, REDIS_CLIENT, SEGMENTS, _patched_render, _render_mocks,
)
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness, fund_wallet, selection_result

User = get_user_model()

RENDER_CLIP = "apps.videos.services.render_engine.RenderEngine.render_clip"
RESUME = "apps.videos.tasks.ingestion.process_initial_ingestion.apply_async"
AUTO_KEY = "auto_render_reservation_id"
DONE, FAILED, RENDERING = VideoClip.Status.COMPLETED, VideoClip.Status.FAILED, VideoClip.Status.RENDERING


def fake_render(clip_id, claim_token=None):
    """What RenderEngine.render_clip saves: COMPLETED, only while this delivery owns the clip."""
    return VideoClip.objects.filter(pk=clip_id, render_claim_token=claim_token).update(
        status=DONE, s3_object_key=f"videos/{clip_id}.mp4", render_claim_token=None, render_unfinished_starts=0,
    ) == 1


class ApprovalHarness:
    def setUp(self):
        self.redis = FakeRedis()
        redis_patcher = patch(REDIS_CLIENT, return_value=self.redis)
        redis_patcher.start()
        self.addCleanup(redis_patcher.stop)
        self.user = User.objects.create_user(username="clips", email="clips@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Clips", owner=self.user)
        self.workspace.members.add(self.user)
        self.wallet = fund_wallet(self.workspace, "100.00")
        self.client.force_authenticate(user=self.user)
        # Where the ingestion leaves a project for the review: its clips selected.
        self.project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Revisión",
            original_r2_key="videos/1/original/source.mp4",
            status=VideoProject.Status.AWAITING_APPROVAL, pipeline_stage=PipelineStage.CLIPS_SELECTED,
            add_subtitles=False, ai_rationale_log={"suggestions_count": 3},
        )
        self.suggested = [
            VideoClip.objects.create(
                project=self.project, title=f"Sugerido {i}", start_time=i * 10, end_time=i * 10 + 5,
                virality_score=80 - i, ai_reasoning="Por qué lo eligió la IA",
            )
            for i in range(3)
        ]

    def approve(self, segments, execute=True):
        """Posts the approval; returns (response, render_clip_task.delay, render_video_segments.delay)."""
        with patch(CLIP_DELAY) as delay, patch(DELAY) as whole_edit, \
                self.captureOnCommitCallbacks(execute=execute):
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": segments}, format="json",
            )
        self.project.refresh_from_db()
        return response, delay, whole_edit

    def approve_ok(self, segments):
        response, delay, _whole_edit = self.approve(segments)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return delay

    def reservation(self):
        return Transaction.objects.get(pk=self.project.metadata["render_reservation_id"])

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def clips(self):
        return list(VideoClip.objects.filter(project=self.project).order_by("start_time"))

    def assert_review_untouched(self):
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertNotIn("render_clip_ids", self.project.metadata)
        self.assertEqual(
            [(clip.pk, clip.start_time, clip.end_time, clip.status) for clip in self.clips()],
            [(clip.pk, clip.start_time, clip.end_time, VideoClip.Status.DRAFT) for clip in self.suggested],
        )


class ApprovedSegmentsBecomeClipsTests(ApprovalHarness, APITestCase):
    def test_each_approved_segment_becomes_its_own_clip(self):
        first, second, third = self.suggested
        segments = [
            {"start": 1, "end": 6, "text": "hola", "title": "Mejor momento", "clip_id": str(first.id)},
            {"start": 10, "end": 14, "clip_id": str(second.id)},
            {"start": 30, "end": 38, "title": "Desde la transcripción"},
            {"start": 40, "end": 45, "clip_id": "not-an-id"},
        ]

        delay = self.approve_ok(segments)

        clips = self.clips()
        self.assertEqual(
            [(clip.title, clip.start_time, clip.end_time, clip.status) for clip in clips],
            [
                ("Mejor momento", 1.0, 6.0, VideoClip.Status.DRAFT),
                ("Sugerido 1", 10.0, 14.0, VideoClip.Status.DRAFT),
                ("Desde la transcripción", 30.0, 38.0, VideoClip.Status.DRAFT),
                ("Clip 4", 40.0, 45.0, VideoClip.Status.DRAFT),
            ],
        )
        # Updated in place: the AI's suggestion keeps its id, score and reasoning.
        self.assertEqual((clips[0].pk, clips[0].ai_reasoning, clips[0].virality_score), (first.pk, "Por qué lo eligió la IA", 80))
        self.assertEqual(clips[1].pk, second.pk)
        # Left out of the approval: deleted. The AI reasoning stays in the log.
        self.assertFalse(VideoClip.objects.filter(pk=third.pk).exists())
        self.assertEqual(self.project.ai_rationale_log, {"suggestions_count": 3})

        paid = [str(clip.pk) for clip in clips]
        self.assertEqual(self.project.metadata["render_clip_ids"], paid)
        self.assertEqual(self.project.metadata["render_seconds"], 22.0)
        self.assertEqual(self.reservation().amount, Decimal("11.00"))  # 22 s, priced like before
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(sorted(call.args[0] for call in delay.call_args_list), sorted(paid))

    def test_one_render_per_clip_is_queued_and_never_one_export_of_all(self):
        response, delay, whole_edit = self.approve(SEGMENTS)

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(delay.call_count, len(SEGMENTS))
        whole_edit.assert_not_called()

    def test_the_renders_are_queued_only_after_the_commit(self):
        with patch(CLIP_DELAY) as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(
                    reverse("project-approve-paper-edit", args=[self.project.id]),
                    {"approved_segments": SEGMENTS}, format="json",
                )
            self.assertEqual(response.status_code, 200, response.content[:300])
            delay.assert_not_called()

            for callback in callbacks:
                callback()

        self.assertEqual(delay.call_count, len(SEGMENTS))

    def test_a_render_that_cannot_be_queued_is_retried_by_a_resume(self):
        # The logs are read after the commit callbacks ran (the outer block).
        with self.assertLogs("apps.videos.tasks.ingestion", level="ERROR") as logs, \
                patch(CLIP_DELAY, side_effect=[OperationalError("broker down"), None]) as delay, \
                patch(RESUME) as resume, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(delay.call_count, 2)  # the other clip is queued all the same
        self.assertTrue(any("render.dispatch_failed" in line and "OperationalError" in line for line in logs.output))
        # Retried by itself: a resume of the pipeline re-queues the clip left DRAFT.
        resume.assert_called_once_with(args=[str(self.project.id)], countdown=RENDER_REDISPATCH_SECONDS)

    def test_the_resume_queues_the_clip_that_was_left_out(self):
        with patch(CLIP_DELAY, side_effect=[OperationalError("broker down"), None]), \
                patch(RESUME), self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )
        self.project.refresh_from_db()
        left_out = self.project.metadata["render_clip_ids"][0]
        VideoClip.objects.filter(pk=self.project.metadata["render_clip_ids"][1]).update(status=RENDERING)

        with patch(CLIP_DELAY) as delay:
            _run_ingestion(self.project.id, attempt=1)  # what the resume runs

        delay.assert_called_once_with(left_out)


    def test_clips_that_are_not_drafts_are_never_touched(self):
        delivered = VideoClip.objects.create(
            project=self.project, title="Ya renderizado", start_time=50, end_time=55,
            status=VideoClip.Status.COMPLETED, s3_object_key="videos/x.mp4",
        )
        other_project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=self.user, title="Otro")
        foreign = VideoClip.objects.create(project=other_project, title="Ajeno", start_time=0, end_time=5)

        self.approve_ok([
            {"start": 60, "end": 65, "clip_id": str(delivered.id)},
            {"start": 70, "end": 75, "clip_id": str(foreign.id)},
        ])

        delivered.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual((delivered.start_time, delivered.status), (50.0, VideoClip.Status.COMPLETED))
        self.assertEqual((foreign.start_time, foreign.project_id), (0.0, other_project.pk))
        new = VideoClip.objects.filter(project=self.project, status=VideoClip.Status.DRAFT).order_by("start_time")
        self.assertEqual([(clip.title, clip.start_time) for clip in new], [("Clip 1", 60.0), ("Clip 2", 70.0)])
        self.assertNotIn(str(delivered.id), self.project.metadata["render_clip_ids"])

    def test_a_clip_id_sent_twice_approves_its_draft_once(self):
        first = self.suggested[0]

        self.approve_ok([
            {"start": 0, "end": 5, "clip_id": str(first.id)},
            {"start": 20, "end": 25, "clip_id": str(first.id)},
        ])

        clip_ids = self.project.metadata["render_clip_ids"]
        self.assertEqual(len(set(clip_ids)), 2)
        self.assertEqual(clip_ids[0], str(first.id))
        self.assertEqual(VideoClip.objects.filter(project=self.project).count(), 2)

    def test_previews_of_the_drafts_left_out_go_with_their_files(self):
        left_out = self.suggested[2]
        preview_file = self.tmp_file()
        preview = ClipPreview.objects.create(
            clip=left_out, status=ClipPreview.Status.READY, storage_key=preview_file,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        self.approve_ok([{"start": 0, "end": 5, "clip_id": str(self.suggested[0].id)}])

        self.assertFalse(ClipPreview.objects.filter(pk=preview.pk).exists())
        self.assertFalse(os.path.exists(preview_file))

    def tmp_file(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = os.path.join(directory, "preview.mp4")
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path


class ApprovalRefusedTests(ApprovalHarness, APITestCase):
    """402, 409 and the plan limit work as before, and leave the review as it was."""

    def test_without_funds_nothing_changes(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("1.00"))

        response, delay, _whole_edit = self.approve(SEGMENTS)

        self.assertEqual(response.status_code, 402, response.content[:300])
        delay.assert_not_called()
        self.assert_review_untouched()

    def test_a_concurrent_approval_changes_nothing(self):
        from apps.videos.services.segments import validate_approved_segments as real_validate

        def another_request_wins(segments):
            VideoProject.objects.filter(pk=self.project.pk).update(status=VideoProject.Status.RENDERING)
            return real_validate(segments)

        with patch("apps.videos.views.validate_approved_segments", side_effect=another_request_wins):
            response, delay, _whole_edit = self.approve(SEGMENTS)

        self.assertEqual(response.status_code, 409, response.content[:300])
        delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())
        self.assertEqual(len(self.clips()), 3)

    def test_over_the_plan_limit_nothing_changes(self):
        plan = SubscriptionPlan.objects.create(name="Corto", max_resolution="720p", max_video_duration_seconds=3)
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=plan)

        response, delay, _whole_edit = self.approve(SEGMENTS)  # 4.5 s

        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertIn("Error en el motor de precios", response.data["error"])
        delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())
        self.assert_review_untouched()

    def test_a_whole_video_project_never_reaches_an_approval(self):
        VideoProject.objects.filter(pk=self.project.pk).update(
            project_type=VideoProject.Type.SINGLE_EDIT, metadata={"priced_duration_s": 30},
        )

        response, delay, _whole_edit = self.approve(SEGMENTS)

        self.assertEqual(response.status_code, 400, response.content[:300])
        delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())

    def test_an_approval_that_cannot_be_written_is_refunded(self):
        # Moved out of clips_selected while the request ran (e.g. a redelivered
        # ingestion message failed it): nothing is written and nothing is paid.
        def the_stage_moves(segments):
            VideoProject.objects.filter(pk=self.project.pk).update(pipeline_stage=PipelineStage.FAILED)
            from apps.videos.services.segments import validate_approved_segments as real_validate

            return real_validate(segments)

        with patch("apps.videos.views.validate_approved_segments", side_effect=the_stage_moves):
            response, delay, _whole_edit = self.approve(SEGMENTS)

        self.assertEqual(response.status_code, 409, response.content[:300])
        delay.assert_not_called()
        self.assertEqual(Transaction.objects.get(wallet=self.wallet).status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(len(self.clips()), 3)


class ApprovedRenderSettlementTests(CapturedTaskFailures, ApprovalHarness, APITestCase):
    """Settled like the auto-render: the share of the seconds delivered."""

    def approve_three(self):
        first, second, third = self.suggested
        self.approve_ok([
            {"start": 0, "end": 5, "clip_id": str(first.id)},
            {"start": 10, "end": 15, "clip_id": str(second.id)},
            {"start": 20, "end": 30, "clip_id": str(third.id)},  # 10 s
        ])
        self.assertEqual(self.reservation().amount, Decimal("10.00"))
        return self.clips()

    def finish(self, *statuses):
        for clip, status in zip(self.approve_three(), statuses):
            VideoClip.objects.filter(pk=clip.pk).update(status=status)
        stage = finalize_project_render(self.project.id)
        self.project.refresh_from_db()
        return stage, self.reservation()

    def test_every_clip_delivered_is_charged_whole(self):
        stage, txn = self.finish(DONE, DONE, DONE)

        self.assertEqual(stage, PipelineStage.COMPLETED)
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("10.00")))
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_only_the_seconds_delivered_are_charged(self):
        stage, txn = self.finish(DONE, FAILED, FAILED)

        self.assertEqual(stage, PipelineStage.PARTIAL)
        self.assertEqual(self.project.status, VideoProject.Status.PARTIAL)
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("2.50")))  # 5 of 20 s
        self.assertEqual(self.balances(), (Decimal("97.50"), Decimal("0")))

    def test_nothing_delivered_is_refunded(self):
        _stage, txn = self.finish(FAILED, FAILED, FAILED)

        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_it_waits_while_a_clip_renders(self):
        stage, txn = self.finish(DONE, RENDERING, FAILED)

        self.assertIsNone(stage)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)
        # Also once the project ended another way: a clip still rendering waits.
        VideoProject.objects.filter(pk=self.project.pk).update(pipeline_stage=PipelineStage.FAILED)
        finalize_project_render(self.project.id)
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)

        # When it finishes, what was delivered is charged: 10 of 20 s.
        VideoClip.objects.filter(pk=self.clips()[1].pk).update(status=DONE)
        finalize_project_render(self.project.id)
        txn = self.reservation()
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("5.00")))

    def test_the_stale_sweep_settles_it_with_the_same_rule(self):
        for clip, status in zip(self.approve_three(), (DONE, FAILED, FAILED)):
            VideoClip.objects.filter(pk=clip.pk).update(status=status)
        # The project closed but its settle failed (a database error, say).
        VideoProject.objects.filter(pk=self.project.pk).update(
            pipeline_stage=PipelineStage.PARTIAL, status=VideoProject.Status.PARTIAL,
        )
        Transaction.objects.filter(pk=self.reservation().pk).update(created_at=timezone.now() - timedelta(days=2))

        out = StringIO()
        call_command("settle_stale_reservations", "--apply", stdout=out)

        line = next(item for item in out.getvalue().splitlines() if f"transaction={self.reservation().pk} " in item)
        self.assertIn("kind=render", line)
        self.assertIn("action=commit reason=clips_delivered result=committed", line)
        txn = self.reservation()
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("2.50")))

    def test_the_stale_sweep_never_settles_a_render_in_progress(self):
        # A clip that is really rendering (it started just now): the money
        # waits for it, and the stuck-render recovery leaves it alone too.
        clips = self.approve_three()
        VideoClip.objects.filter(pk=clips[0].pk).update(status=RENDERING, render_claim_token=uuid.uuid4())
        VideoProject.objects.filter(pk=self.project.pk).update(
            metadata={**self.project.metadata, "render_clip_started_at": timezone.now().isoformat()},
        )
        Transaction.objects.filter(pk=self.reservation().pk).update(created_at=timezone.now() - timedelta(days=2))

        out = StringIO()
        call_command("settle_stale_reservations", "--apply", stdout=out)
        with patch(CLIP_DELAY) as delay:
            rows = recover_stuck_renders_now(apply=True)

        self.assertIn("action=report reason=render_in_progress", out.getvalue())
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)
        delay.assert_not_called()
        self.assertEqual(
            [(row["kind"], row["action"], row["reason"]) for row in rows if row["project"] == self.project.pk],
            [("clips", "skip", "recent")],
        )

    def test_a_clip_that_really_renders_through_the_task_is_charged(self):
        clips = self.approve_three()

        with patch(RENDER_CLIP, side_effect=fake_render) as render:
            for clip in clips[:2]:
                render_clip_task.apply(args=[str(clip.id)])
            with patch(RENDER_CLIP, side_effect=RuntimeError("bad encode")):
                result = render_clip_task.apply(args=[str(clips[2].id)])  # fails for good

        self.assertTrue(result.failed())
        self.assertEqual(render.call_count, 2)
        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)
        txn = self.reservation()
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("5.00")))  # 10 of 20 s
        self.assertEqual(self.balances(), (Decimal("95.00"), Decimal("0")))
        self.assertIn("render_clip_started_at", self.project.metadata)

    def test_clips_failed_by_an_earlier_auto_render_do_not_make_the_approval_partial(self):
        failed_before = VideoClip.objects.create(
            project=self.project, title="Falló antes", start_time=50, end_time=55, status=FAILED,
        )
        clips = self.approve_three()

        with patch(RENDER_CLIP, side_effect=fake_render):
            for clip in clips:
                render_clip_task.apply(args=[str(clip.id)])

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(VideoClip.objects.get(pk=failed_before.pk).status, FAILED)  # never touched
        self.assertEqual(self.reservation().status, Transaction.Status.COMPLETED)


class ApprovedRenderGateTests(ApprovalHarness, APITestCase):
    """render_clip_task renders only what the approval paid for, while it is paid."""

    def setUp(self):
        super().setUp()
        self.approve_ok([{"start": 0, "end": 5, "clip_id": str(self.suggested[0].id)}])
        self.approved = VideoClip.objects.get(pk=self.suggested[0].pk)

    def render(self, clip):
        with patch(RENDER_CLIP) as render:
            render_clip_task.apply(args=[str(clip.id)])
        return render

    def test_an_approved_clip_is_rendered(self):
        self.render(self.approved).assert_called_once()

    def test_a_clip_outside_the_approved_set_is_not_rendered(self):
        stray = VideoClip.objects.create(project=self.project, title="Fuera", start_time=40, end_time=45)

        self.render(stray).assert_not_called()

        self.assertEqual(VideoClip.objects.get(pk=stray.pk).status, VideoClip.Status.FAILED)
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)

    def test_a_clip_is_not_rendered_once_its_approval_was_refunded(self):
        rollback_reservation(self.project.metadata["render_reservation_id"])  # still render_dispatched

        self.render(self.approved).assert_not_called()

        self.assertEqual(VideoClip.objects.get(pk=self.approved.pk).status, VideoClip.Status.FAILED)

    def test_a_clip_is_not_rendered_once_the_project_left_render_dispatched(self):
        VideoProject.objects.filter(pk=self.project.pk).update(pipeline_stage=PipelineStage.FAILED)

        self.render(self.approved).assert_not_called()


class LegacyApprovalTests(ApprovalHarness, APITestCase):
    """
    render_video_segments and recover_stuck_renders only handle the approvals
    from before (still in flight at deploy), and keep handling them.
    """

    def approve_legacy(self):
        """What the view wrote before: the segments and the reservation, no clip ids."""
        segments = validate_approved_segments(SEGMENTS)
        seconds = sum(seg["end"] - seg["start"] for seg in segments)
        txn = reserve_funds(self.wallet.id, render_cost(self.project, seconds), user=self.user)
        VideoProject.objects.filter(pk=self.project.pk).update(
            approved_segments=segments, status=VideoProject.Status.RENDERING,
            metadata={"final_duration": seconds, "render_reservation_id": str(txn.id)},
        )
        self.project.refresh_from_db()
        return txn

    def test_an_approval_in_flight_at_deploy_still_renders_as_one_export(self):
        txn = self.approve_legacy()
        source, processed = _render_mocks()

        with _patched_render(source, processed, redis=self.redis) as (_client, upload):
            render_video_segments(self.project.id)

        upload.assert_called_once()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(
            [c.args for c in source.subclipped.call_args_list], [(0.0, 2.0), (5.0, 7.5)]
        )

    def test_a_stuck_approval_from_before_is_still_recovered(self):
        txn = self.approve_legacy()
        Transaction.objects.filter(pk=txn.pk).update(created_at=timezone.now() - timedelta(days=2))

        with patch.object(render_video_segments, "apply_async") as requeue:
            rows = recover_stuck_renders_now(apply=True)

        requeue.assert_called_once_with(args=[str(self.project.id)])
        self.assertIn(self.project.pk, [row["project"] for row in rows])

    def test_an_approval_rendered_clip_by_clip_is_recovered_by_its_clips_never_as_one_export(self):
        self.approve_ok(SEGMENTS)
        old = timezone.now() - timedelta(days=2)
        Transaction.objects.filter(pk=self.reservation().pk).update(created_at=old)

        with patch.object(render_video_segments, "apply_async") as requeue, patch(CLIP_DELAY) as delay:
            rows = recover_stuck_renders_now(apply=True)

        requeue.assert_not_called()
        self.assertEqual(
            [(row["kind"], row["action"]) for row in rows if row["project"] == self.project.pk],
            [("clips", "requeue")],
        )
        self.assertEqual(delay.call_count, len(SEGMENTS))
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)

    def test_render_video_segments_neither_renders_fails_nor_settles_it(self):
        self.approve_ok(SEGMENTS)
        from apps.videos.tasks.render import _fail_render, _settle_render_reservation

        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, upload):
            render_video_segments(self.project.id)
        _fail_render(self.project.id, "render_stuck")
        _settle_render_reservation(self.project.id, charge=True)

        client.download_file.assert_not_called()
        upload.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)


class ApprovalCommitTests(ApprovalHarness, APITransactionTestCase):
    """With real commits: the approval's callbacks run when its transaction commits."""

    def test_an_error_after_the_approval_was_saved_does_not_undo_it(self):
        with patch("apps.videos.tasks.ingestion._dispatch_approved_clips", side_effect=RuntimeError("late")):
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )

        # Saved before the error: still approved and paid, not given back to the review.
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)
        self.assertEqual(self.reservation().status, Transaction.Status.RESERVED)

    def test_an_error_before_the_commit_gives_everything_back(self):
        with patch("apps.videos.tasks.ingestion._mark_render_dispatched", side_effect=RuntimeError("early")), \
                self.assertRaises(RuntimeError):
            self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )

        self.assert_review_untouched()
        self.assertEqual(Transaction.objects.get(wallet=self.wallet).status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))


class StuckApprovedClipsRecoveryTests(CapturedTaskFailures, ApprovalHarness, APITestCase):
    """
    A per-clip approval whose render messages are lost, or whose clip was left
    RENDERING by a dead worker, is brought back by recover_stuck_renders once
    it has been idle for RENDER_STUCK_AFTER_SECONDS: never stuck with its
    tokens reserved.
    """

    def approve_and_lose_the_messages(self):
        # The broker refuses every render and the resume of the pipeline too.
        with patch(CLIP_DELAY, side_effect=OperationalError("broker down")), \
                patch(RESUME, side_effect=OperationalError("broker down")), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.project.refresh_from_db()
        return self.project.metadata["render_clip_ids"]

    def age(self):
        """Nothing happened since long before RENDER_STUCK_AFTER_SECONDS."""
        past = (timezone.now() - timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS + 60)).isoformat()
        self.project.refresh_from_db()
        metadata = dict(self.project.metadata)
        for key in ("render_clip_started_at", "render_recovery_requeued_at"):
            if key in metadata:
                metadata[key] = past
        VideoProject.objects.filter(pk=self.project.pk).update(metadata=metadata)
        Transaction.objects.filter(pk=metadata["render_reservation_id"]).update(created_at=past)

    def recover(self):
        with patch(CLIP_DELAY) as delay, patch.object(render_clip_task, "apply_async") as takeover:
            rows = recover_stuck_renders_now(apply=True)
        row = next(row for row in rows if row["project"] == self.project.pk)
        return row, delay, takeover

    def test_clips_whose_messages_were_lost_are_queued_again_and_delivered(self):
        clip_ids = self.approve_and_lose_the_messages()
        self.age()

        row, delay, _takeover = self.recover()

        self.assertEqual((row["kind"], row["action"], row["reason"], row["result"]), ("clips", "requeue", "clips_left", "requeued"))
        self.assertEqual(sorted(call.args[0] for call in delay.call_args_list), sorted(clip_ids))
        self.project.refresh_from_db()
        self.assertEqual(self.project.metadata["render_recovery_requeues"], 1)
        # The next run, before those messages could start, leaves it alone.
        row, delay, _takeover = self.recover()
        self.assertEqual((row["action"], row["reason"]), ("skip", "recent"))
        delay.assert_not_called()

        with patch(RENDER_CLIP, side_effect=fake_render):
            for clip_id in clip_ids:
                render_clip_task.apply(args=[clip_id])

        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.reservation().status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("100.00") - self.reservation().amount, Decimal("0")))

    def test_a_clip_left_rendering_by_a_dead_owner_is_taken_over_and_delivered(self):
        self.approve_ok(SEGMENTS)
        done, stuck = self.project.metadata["render_clip_ids"]
        dead_owner = uuid.uuid4()
        VideoClip.objects.filter(pk=done).update(status=DONE, s3_object_key="videos/done.mp4")
        VideoClip.objects.filter(pk=stuck).update(status=RENDERING, render_claim_token=dead_owner, render_unfinished_starts=1)
        self.age()
        # A manual re-dispatch queues only DRAFT clips: it cannot free this one.
        with patch(CLIP_DELAY) as delay:
            _run_ingestion(self.project.id, attempt=1)
        delay.assert_not_called()

        row, _delay, takeover = self.recover()

        self.assertEqual((row["action"], row["rendering"]), ("requeue", 1))
        takeover.assert_called_once_with(args=[stuck], headers={RENDER_TAKEOVER_HEADER: str(dead_owner)})
        with patch(RENDER_CLIP, side_effect=fake_render) as render:
            render_clip_task.apply(args=[stuck], headers={RENDER_TAKEOVER_HEADER: str(dead_owner)})
        render.assert_called_once()
        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.reservation().status, Transaction.Status.COMPLETED)

    def test_a_clip_that_keeps_killing_its_worker_fails_at_the_start_cap(self):
        self.approve_ok(SEGMENTS)
        done, stuck = self.project.metadata["render_clip_ids"]
        dead_owner = uuid.uuid4()
        VideoClip.objects.filter(pk=done).update(status=DONE, s3_object_key="videos/done.mp4")
        VideoClip.objects.filter(pk=stuck).update(
            status=RENDERING, render_claim_token=dead_owner, render_unfinished_starts=settings.RENDER_CLIP_MAX_STARTS,
        )
        self.age()
        self.recover()

        with patch(RENDER_CLIP, side_effect=fake_render) as render:
            result = render_clip_task.apply(args=[stuck], headers={RENDER_TAKEOVER_HEADER: str(dead_owner)})

        self.assertTrue(result.failed())
        render.assert_not_called()
        self.assertEqual(VideoClip.objects.get(pk=stuck).status, FAILED)
        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.PARTIAL)
        txn = self.reservation()
        self.assertEqual((txn.status, txn.amount), (Transaction.Status.COMPLETED, Decimal("1.11")))  # 2.50 for 4.5 s, 2 delivered

    def test_past_the_total_of_requeues_it_is_only_reported(self):
        self.approve_and_lose_the_messages()
        self.age()
        VideoProject.objects.filter(pk=self.project.pk).update(
            metadata={**self.project.metadata, "render_recovery_requeues_total": RENDER_RECOVERY_MAX_REQUEUES},
        )

        row, delay, _takeover = self.recover()

        self.assertEqual((row["action"], row["reason"]), ("report", "requeue_total_cap"))
        delay.assert_not_called()

    def test_a_fan_out_left_open_is_closed_and_settled(self):
        self.approve_ok(SEGMENTS)
        VideoClip.objects.filter(pk__in=self.project.metadata["render_clip_ids"]).update(status=DONE)  # its close failed
        self.age()

        row, _delay, _takeover = self.recover()

        self.assertEqual((row["action"], row["reason"], row["result"]), ("close", "fan_out_open", "closed"))
        self.project.refresh_from_db()
        self.assertEqual(self.project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.reservation().status, Transaction.Status.COMPLETED)

    def test_the_dry_run_writes_nothing(self):
        self.approve_and_lose_the_messages()
        self.age()
        self.project.refresh_from_db()
        metadata = self.project.metadata

        with patch(CLIP_DELAY) as delay:
            out = StringIO()
            call_command("recover_stuck_renders", stdout=out)

        delay.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.project.metadata, metadata)
        self.assertIn(f"project={self.project.pk} kind=clips", out.getvalue())
        self.assertIn("action=requeue reason=clips_left result=dry_run", out.getvalue())


class LeftoverAutoRenderTests(IngestionHarness, TestCase):
    """
    An auto-render refunded and then not paid again goes to the review with its
    keys still in the metadata. The approval pays alone: its clips render, it
    is charged once, and no resume reserves the auto-render again.
    """

    def setUp(self):
        super().setUp()
        self.wallet = fund_wallet(self.workspace, "100.00")
        self.mock_select.return_value = selection_result(count=3)  # 3 clips of 5 s
        self.workspace.members.add(self.user)
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def run_to_render(self, project):
        with self.captureOnCommitCallbacks(execute=True):
            self.run_task(project)
        project.refresh_from_db()
        return project

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def in_review_with_a_settled_auto_render(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))
        rollback_reservation(project.metadata[AUTO_KEY])  # refunded when it failed
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.CLIPS_SELECTED, status=VideoProject.Status.INGESTING,
        )
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("1.00"))
        project = self.run_to_render(project)  # cannot pay it again: to the review
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("100.00"))
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(Transaction.objects.get(pk=project.metadata[AUTO_KEY]).status, Transaction.Status.FAILED)
        self.mock_render.reset_mock()
        return project

    def approve(self, project, delay_side_effect=None):
        clips = list(VideoClip.objects.filter(project=project).order_by("start_time"))[:2]
        segments = [{"start": clip.start_time, "end": clip.end_time, "clip_id": str(clip.pk)} for clip in clips]
        with patch(CLIP_DELAY, side_effect=delay_side_effect), patch(RESUME), \
                self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(
                reverse("project-approve-paper-edit", args=[project.id]), {"approved_segments": segments}, format="json",
            )
        project.refresh_from_db()
        return response

    def render_all(self, project):
        with patch(RENDER_CLIP, side_effect=fake_render) as render:
            for clip_id in project.metadata["render_clip_ids"]:
                render_clip_task.apply(args=[clip_id])
        project.refresh_from_db()
        return render

    def test_the_approved_clips_render_and_only_the_approval_is_charged(self):
        project = self.in_review_with_a_settled_auto_render()

        response = self.approve(project)

        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertNotIn(AUTO_KEY, project.metadata)
        self.assertNotIn("auto_render_clip_ids", project.metadata)
        self.assertEqual(self.render_all(project).call_count, 2)
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        approval = Transaction.objects.get(pk=project.metadata["render_reservation_id"])
        self.assertEqual((approval.status, approval.amount), (Transaction.Status.COMPLETED, Decimal("5.00")))
        self.assertEqual(self.balances(), (Decimal("95.00"), Decimal("0")))

    def test_a_lost_dispatch_and_a_redispatch_never_reserve_the_auto_render_again(self):
        project = self.in_review_with_a_settled_auto_render()
        self.approve(project, delay_side_effect=OperationalError("broker down"))

        project = self.run_to_render(project)  # the resume (or a manual re-dispatch)

        self.assertEqual(self.mock_render.call_count, 2)  # the approved clips, queued again
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=Transaction.Status.RESERVED).count(), 1)
        self.render_all(project)
        self.assertEqual(project.pipeline_stage, PipelineStage.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("95.00"), Decimal("0")))  # charged once, 10 s

    def test_a_resume_never_reserves_an_auto_render_for_an_approved_review(self):
        # Even with the auto-render keys still there (an approval made before
        # they were dropped): the approval pays.
        project = self.in_review_with_a_settled_auto_render()
        stale = {key: project.metadata[key] for key in (AUTO_KEY, "auto_render_clip_ids", "auto_render_seconds")}
        self.approve(project)
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, **stale})

        project = self.run_to_render(project)

        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, status=Transaction.Status.RESERVED).count(), 1)
        self.assertEqual(project.pipeline_stage, PipelineStage.RENDER_DISPATCHED)

    def test_the_gate_asks_the_reservation_that_pays_for_the_clip(self):
        project = self.in_review_with_a_settled_auto_render()
        self.approve(project)
        approved = project.metadata["render_clip_ids"][0]
        stale = Transaction.objects.get(pk=self.stale_auto_id(project))
        open_approval = project.metadata["render_reservation_id"]
        both = {
            **project.metadata, AUTO_KEY: str(stale.pk), "auto_render_clip_ids": [approved, "other"],
        }
        VideoProject.objects.filter(pk=project.pk).update(metadata=both)

        # A settled auto-render that also lists the clip has no say: the approval pays for it.
        self.assertTrue(_whole_video_render_allowed(project.pk, clip_id=approved))
        # A clip only the settled auto-render paid for is not rendered.
        self.assertFalse(_whole_video_render_allowed(project.pk, clip_id="other"))
        # Once the approval is settled too, nothing pays for the approved clip.
        rollback_reservation(open_approval)
        self.assertFalse(_whole_video_render_allowed(project.pk, clip_id=approved))

    def stale_auto_id(self, project):
        return Transaction.objects.filter(wallet=self.wallet, status=Transaction.Status.FAILED).earliest("created_at").pk

    def test_an_auto_render_still_reserved_refuses_the_approval(self):
        project = self.in_review_with_a_settled_auto_render()
        open_auto = reserve_funds(self.wallet.id, Decimal("7.50"))
        VideoProject.objects.filter(pk=project.pk).update(metadata={**project.metadata, AUTO_KEY: str(open_auto.pk)})

        response = self.approve(project)

        self.assertEqual(response.status_code, 409, response.content[:300])
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(VideoClip.objects.filter(project=project, status=VideoClip.Status.DRAFT).count(), 3)
        open_auto.refresh_from_db()
        self.assertEqual(open_auto.status, Transaction.Status.RESERVED)
        # Only the auto-render stays reserved: the approval's reservation went back.
        self.assertEqual(self.balances(), (Decimal("92.50"), Decimal("7.50")))

    def test_an_auto_render_whose_messages_were_lost_is_recovered_by_its_clips(self):
        project = self.run_to_render(self.make_project(auto_render_bypass=True))  # messages never ran
        past = timezone.now() - timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS + 60)
        Transaction.objects.filter(pk=project.metadata[AUTO_KEY]).update(created_at=past)

        with patch(CLIP_DELAY) as delay:
            rows = recover_stuck_renders_now(apply=True)

        row = next(row for row in rows if row["project"] == project.pk)
        self.assertEqual((row["kind"], row["action"]), ("clips", "requeue"))
        self.assertEqual(sorted(call.args[0] for call in delay.call_args_list), sorted(project.metadata["auto_render_clip_ids"]))
