"""
Reserved tokens are settled (they used to stay reserved forever):

- The creation reservation is charged once the AI work is delivered (the
  clips are selected) and still refunded when the project fails before that.
- The render reservation taken by approve-paper-edit is charged when the
  render is delivered and returned when it fails for good.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.payments.models import Transaction, Wallet
from apps.payments.services.wallet_service import reserve_funds
from apps.users.models import Workspace
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.errors import NonRetryableAIError
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.tasks import _commit_ingestion_reservation, render_video_segments
from apps.videos.tasks.render import RENDER_SEGMENTS_MAX_REDELIVERIES
from apps.videos.tests.fakes import FakeRedis
from apps.videos.tests.test_approved_segments_source import (
    DELAY, REDIS_CLIENT, SEGMENTS, _patched_render, _render_mocks,
)
from apps.videos.tests.test_ingestion_moderation import ModerationStageHarness

from django.contrib.auth import get_user_model

User = get_user_model()


class CreationReservationTests(ModerationStageHarness, TestCase):
    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def test_the_reservation_is_charged_once_the_clips_are_selected(self):
        project, txn = self.make_reserved_project()

        self.run_task(project)

        project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_a_project_that_fails_before_its_clips_is_still_refunded(self):
        from apps.core.security import UnsafeContentError

        self.mock_moderate.side_effect = UnsafeContentError("blocked")
        project, txn = self.make_reserved_project()

        self.run_task(project)

        self.assert_refunded(txn)

    def test_charging_twice_changes_nothing(self):
        project, txn = self.make_reserved_project()
        self.run_task(project)

        _commit_ingestion_reservation(project.id)  # a redelivered task

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_a_project_without_a_reservation_is_skipped_quietly(self):
        project = self.make_project()

        _commit_ingestion_reservation(project.id)

        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_payment_error_never_stops_the_pipeline(self):
        from apps.payments.services.wallet_service import PaymentError

        project, txn = self.make_reserved_project()
        with patch(
            "apps.payments.services.wallet_service.commit_reservation", side_effect=PaymentError("x"),
        ), self.assertLogs("apps.videos.tasks.ingestion", level="ERROR") as logs:
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertTrue(any("pipeline.charge_failed" in line for line in logs.output))


class RenderReservationHarness:
    def setUp(self):
        # The render holds the project lock (acks_late): never a real Redis here.
        self.redis = FakeRedis()
        redis_patcher = patch(REDIS_CLIENT, return_value=self.redis)
        redis_patcher.start()
        self.addCleanup(redis_patcher.stop)
        self.user = User.objects.create_user(username="rres", email="rres@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Render Res", owner=self.user)
        self.workspace.members.add(self.user)
        self.wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=self.wallet.pk).update(
            available_balance=Decimal("100.00"), reserved_balance=Decimal("0")
        )
        self.client.force_authenticate(user=self.user)
        self.project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="Render reservation",
            original_r2_key="videos/1/original/source.mp4",
            status=VideoProject.Status.AWAITING_APPROVAL, add_subtitles=False,
        )

    def approve(self):
        with patch(DELAY):
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.project.refresh_from_db()
        return Transaction.objects.get(pk=self.project.metadata["render_reservation_id"])

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance


class RenderReservationTests(RenderReservationHarness, APITestCase):
    def test_approval_links_its_reservation_to_the_project(self):
        txn = self.approve()

        self.assertEqual(txn.status, Transaction.Status.RESERVED)
        self.assertEqual(self.balances(), (Decimal("100.00") - txn.amount, txn.amount))

    def test_a_delivered_render_is_charged(self):
        txn = self.approve()
        source, processed = _render_mocks()

        with _patched_render(source, processed):
            render_video_segments(self.project.id)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("100.00") - txn.amount, Decimal("0")))

    def test_a_render_that_cannot_work_is_refunded(self):
        txn = self.approve()

        with patch(
            "apps.videos.tasks.render._resolve_approved_segments",
            side_effect=NonRetryableAIError("bad segments", provider="render", metadata={"code": "x"}),
        ), self.assertRaises(NonRetryableAIError):
            render_video_segments(self.project.id)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_failing_render_keeps_the_reservation_until_its_last_retry(self):
        txn = self.approve()

        render_video_segments.push_request(retries=render_video_segments.max_retries)
        try:
            with patch("moviepy.VideoFileClip", side_effect=RuntimeError("corrupt")), \
                    patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"):
                with self.assertRaises(RuntimeError):
                    render_video_segments.run(self.project.id)
        finally:
            render_video_segments.pop_request()

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_retry_in_progress_keeps_the_reservation(self):
        from celery.exceptions import Retry

        txn = self.approve()
        with patch("moviepy.VideoFileClip", side_effect=RuntimeError("corrupt")), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"), \
                patch.object(render_video_segments, "retry", side_effect=Retry()):
            with self.assertRaises(Retry):
                render_video_segments(self.project.id)

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_the_approval_charges_the_render_only_not_the_add_ons_again(self):
        # SEO was charged with the project at creation (ADR-013).
        VideoProject.objects.filter(pk=self.project.pk).update(metadata={"use_seo": True})

        txn = self.approve()

        seconds = sum(seg["end"] - seg["start"] for seg in SEGMENTS)
        from math import ceil

        self.assertEqual(txn.amount, (Decimal(ceil(seconds)) * Decimal("0.5")).quantize(Decimal("0.01")))

    def test_a_concurrent_approval_cannot_reserve_or_render_twice(self):
        from apps.videos.services.segments import validate_approved_segments as real_validate

        def another_request_wins(segments):
            # The other approval claimed the project between this one's checks.
            VideoProject.objects.filter(pk=self.project.pk).update(status=VideoProject.Status.RENDERING)
            return real_validate(segments)

        with patch("apps.videos.views.validate_approved_segments", side_effect=another_request_wins), \
                patch(DELAY) as delay:
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )

        self.assertEqual(response.status_code, 409, response.content[:300])
        delay.assert_not_called()
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet).exists())

    def test_an_approval_without_funds_gives_the_project_back(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("0"))
        with patch(DELAY) as delay:
            response = self.client.post(
                reverse("project-approve-paper-edit", args=[self.project.id]),
                {"approved_segments": SEGMENTS}, format="json",
            )

        self.assertEqual(response.status_code, 402, response.content[:300])
        delay.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.AWAITING_APPROVAL)


class PaperEditRedeliveryTests(RenderReservationHarness, APITestCase):
    """
    render_video_segments acks late, so a lost worker gets its message back.
    A redelivery never renders or charges a delivered edit again, two
    deliveries never render the same project at once, and a render that
    keeps losing its worker fails and is refunded.
    """

    def run_delivery(self, redelivered=False, **kwargs):
        """One delivery of the message, as the worker runs it (with its delivery_info)."""
        render_video_segments.push_request(delivery_info={"redelivered": redelivered}, retries=0)
        try:
            return render_video_segments.run(self.project.id, **kwargs)
        finally:
            render_video_segments.pop_request()

    def hold_project_lock(self):
        """Another delivery is rendering the project (its lock is alive)."""
        lock = self.redis.lock(f"lock:pipeline:project:{self.project.id}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))

    def final_clips(self):
        return VideoClip.objects.filter(project=self.project, title="VIDEO FINAL EDITADO").count()

    def test_the_paper_edit_render_gets_its_message_back_after_a_lost_worker(self):
        self.assertTrue(render_video_segments.acks_late)
        self.assertTrue(render_video_segments.reject_on_worker_lost)

    def test_a_redelivery_after_the_delivery_neither_renders_nor_charges_again(self):
        txn = self.approve()
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, upload):
            render_video_segments(self.project.id)
            charged = self.balances()
            self.run_delivery(redelivered=True)

        self.assertEqual(client.download_file.call_count, 1)
        self.assertEqual(upload.call_count, 1)
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), charged)
        self.assertEqual(self.final_clips(), 1)

    def test_a_worker_lost_between_saving_and_charging_is_charged_once(self):
        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.COMPLETED, final_export_r2_key="final/key.mp4",
        )
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, _):
            self.run_delivery(redelivered=True)
            self.run_delivery(redelivered=True)

        client.download_file.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("100.00") - txn.amount, Decimal("0")))

    def test_a_redelivery_renders_a_project_whose_worker_died_mid_render(self):
        txn = self.approve()
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload):
            self.run_delivery(redelivered=True)

        upload.assert_called_once()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(self.project.metadata["render_redeliveries"], 1)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.final_clips(), 1)

    def test_a_render_that_keeps_losing_its_worker_fails_and_is_refunded(self):
        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(metadata={
            **self.project.metadata, "render_redeliveries": RENDER_SEGMENTS_MAX_REDELIVERIES,
        })
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, _):
            with self.assertRaises(NonRetryableAIError):
                self.run_delivery(redelivered=True)

        client.download_file.assert_not_called()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertEqual(self.project.pipeline_error_code, "render_worker_lost")
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_delivery_never_renders_while_another_one_holds_the_project(self):
        from apps.videos.tasks import PIPELINE_LOCK_RETRY_SECONDS

        txn = self.approve()
        self.hold_project_lock()  # e.g. a render that outlasted the visibility timeout
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, _), \
                patch.object(render_video_segments, "apply_async") as requeue:
            self.run_delivery(redelivered=True)

        client.download_file.assert_not_called()
        requeue.assert_called_once()
        # Re-queued with the redelivery flag: a dead worker's lock must still count.
        self.assertEqual(requeue.call_args.kwargs["kwargs"], {"lock_retries": 1, "redelivered": True})
        self.assertEqual(requeue.call_args.kwargs["countdown"], PIPELINE_LOCK_RETRY_SECONDS)
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertNotIn("render_redeliveries", self.project.metadata)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_past_its_lock_retries_a_delivery_gives_up_without_touching_the_project(self):
        from apps.videos.tasks import PIPELINE_LOCK_MAX_RETRIES

        txn = self.approve()
        self.hold_project_lock()
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, _), \
                patch.object(render_video_segments, "apply_async") as requeue:
            self.run_delivery(lock_retries=PIPELINE_LOCK_MAX_RETRIES)

        client.download_file.assert_not_called()
        requeue.assert_not_called()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_a_retry_starts_as_a_new_delivery(self):
        from celery.exceptions import Retry

        self.approve()
        with patch("moviepy.VideoFileClip", side_effect=RuntimeError("corrupt")), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"), \
                patch.object(render_video_segments, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                self.run_delivery(redelivered=True, lock_retries=3)

        # Neither the lock budget nor the redelivery flag travel with a retry.
        self.assertEqual(retry.call_args.kwargs["kwargs"], {})

    def test_the_export_and_its_clip_are_saved_together(self):
        from celery.exceptions import Retry
        from django.db import DatabaseError

        txn = self.approve()
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis), \
                patch.object(VideoClip.objects, "create", side_effect=DatabaseError("x")), \
                patch.object(render_video_segments, "retry", side_effect=Retry()):
            with self.assertRaises(Retry):
                render_video_segments(self.project.id)

        self.project.refresh_from_db()
        txn.refresh_from_db()
        # Not half delivered: a redelivery would otherwise skip it with no clip row.
        self.assertFalse(self.project.final_export_r2_key)
        self.assertNotEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_an_error_after_the_export_was_saved_is_charged_not_retried(self):
        txn = self.approve()
        source, processed = _render_mocks()
        source.__exit__.side_effect = OSError("closing the source failed")
        with _patched_render(source, processed, redis=self.redis) as (_, upload), \
                patch.object(render_video_segments, "retry") as retry:
            render_video_segments(self.project.id)

        retry.assert_not_called()
        upload.assert_called_once()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
