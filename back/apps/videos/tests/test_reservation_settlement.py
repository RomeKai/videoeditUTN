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
from apps.videos.tasks.render import RENDER_LOCK_RETRIES_HEADER, RENDER_SEGMENTS_MAX_REDELIVERIES
from apps.videos.tests.fakes import FakeRedis
from apps.videos.tests.test_approved_segments_source import (
    DELAY, REDIS_CLIENT, SEGMENTS, _patched_render, _render_mocks,
)
from apps.videos.tests.test_ingestion_moderation import ModerationStageHarness

from django.contrib.auth import get_user_model

User = get_user_model()

DELETE_OBJECT = "apps.videos.services.storage_service.CloudflareR2Manager.delete_object"


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

    def run_delivery(self, redelivered=False, lock_retries=None, retries=0):
        """One delivery of the message, as the worker runs it (delivery_info and headers)."""
        headers = {RENDER_LOCK_RETRIES_HEADER: lock_retries} if lock_retries is not None else None
        render_video_segments.push_request(
            delivery_info={"redelivered": redelivered}, headers=headers, retries=retries,
        )
        try:
            return render_video_segments.run(self.project.id)
        finally:
            render_video_segments.pop_request()

    def hold_project_lock(self):
        """Another delivery is rendering the project (its lock is alive)."""
        lock = self.redis.lock(f"lock:pipeline:project:{self.project.id}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))

    def set_unfinished_starts(self, count):
        self.project.refresh_from_db()
        VideoProject.objects.filter(pk=self.project.pk).update(
            metadata={**self.project.metadata, "render_unfinished_starts": count},
        )

    def unfinished_starts(self):
        self.project.refresh_from_db()
        return self.project.metadata.get("render_unfinished_starts")

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
        self.set_unfinished_starts(1)  # the first start never finished: its worker died
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload):
            self.run_delivery(redelivered=True)

        upload.assert_called_once()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.final_clips(), 1)
        self.assertEqual(self.unfinished_starts(), 1)  # only the lost one stays counted

    def test_a_render_that_keeps_losing_its_worker_fails_and_is_refunded(self):
        txn = self.approve()
        self.set_unfinished_starts(RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
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

    def test_only_starts_that_never_finished_count_towards_the_cap(self):
        """
        Redeliveries that come from a broker blip or a warm shutdown, with
        every start ending in a handled error, never fail a healthy render.
        """
        from celery.exceptions import Retry

        txn = self.approve()
        for _ in range(RENDER_SEGMENTS_MAX_REDELIVERIES + 1):
            with patch("moviepy.VideoFileClip", side_effect=RuntimeError("transient")), \
                    patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"), \
                    patch.object(render_video_segments, "retry", side_effect=Retry()):
                with self.assertRaises(Retry):
                    self.run_delivery(redelivered=True)
            self.assertEqual(self.unfinished_starts(), 0)

        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload):
            self.run_delivery(redelivered=True)

        upload.assert_called_once()
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

    def test_a_delivery_never_renders_while_another_one_holds_the_project(self):
        from apps.videos.tasks import PIPELINE_LOCK_RETRY_SECONDS

        txn = self.approve()
        self.hold_project_lock()  # e.g. a copy redelivered while its worker is alive
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, _), \
                patch.object(render_video_segments, "apply_async") as requeue:
            self.run_delivery(redelivered=True)

        client.download_file.assert_not_called()
        requeue.assert_called_once()
        # The lock budget travels in a header, never as a kwarg an older worker rejects.
        self.assertEqual(requeue.call_args.kwargs["headers"], {RENDER_LOCK_RETRIES_HEADER: 1})
        self.assertNotIn("kwargs", requeue.call_args.kwargs)
        self.assertEqual(requeue.call_args.kwargs["args"], [str(self.project.id)])
        self.assertEqual(requeue.call_args.kwargs["countdown"], PIPELINE_LOCK_RETRY_SECONDS)
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertIsNone(self.unfinished_starts())  # nothing started
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

    def test_a_retry_starts_as_a_new_delivery_and_the_project_keeps_rendering(self):
        from celery.exceptions import Retry

        txn = self.approve()
        with patch("moviepy.VideoFileClip", side_effect=RuntimeError("corrupt")), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"), \
                patch.object(render_video_segments, "retry", side_effect=Retry()) as retry:
            with self.assertRaises(Retry):
                self.run_delivery(redelivered=True, lock_retries=3)

        # The lock budget (a header) does not travel with a retry.
        self.assertEqual(retry.call_args.kwargs["headers"], {})
        self.project.refresh_from_db()
        txn.refresh_from_db()
        # Not FAILED while a retry is pending: a copy still queued may finish it.
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

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
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_an_error_after_the_export_was_saved_is_charged_by_the_handler(self):
        from apps.videos.tasks import render as render_module

        txn = self.approve()
        source, processed = _render_mocks()
        events = []

        def close_fails(*args):
            events.append("close_failed")
            raise OSError("closing the source failed")

        real_settle = render_module._settle_render_reservation

        def settle(project_id, charge):
            events.append(f"settle charge={charge}")
            return real_settle(project_id, charge)

        source.__exit__.side_effect = close_fails
        with _patched_render(source, processed, redis=self.redis) as (_, upload), \
                patch.object(render_module, "_settle_render_reservation", side_effect=settle), \
                patch.object(render_video_segments, "retry") as retry:
            render_video_segments(self.project.id)

        # The export was saved before MoviePy failed to close; only the error
        # handler charges it (the happy path never got to).
        self.assertEqual(events, ["close_failed", "settle charge=True"])
        retry.assert_not_called()
        upload.assert_called_once()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)


class PaperEditDuplicateCopyTests(RenderReservationHarness, APITestCase):
    """
    Two copies of the same paper-edit message (a redelivery, a lock-busy
    re-queue) never deliver for free, never charge a failed render and never
    mark a delivered one failed.
    """

    def run_last_attempt(self):
        render_video_segments.push_request(delivery_info={}, retries=render_video_segments.max_retries)
        try:
            return render_video_segments.run(self.project.id)
        finally:
            render_video_segments.pop_request()

    def test_a_copy_queued_behind_a_final_failure_does_not_render_for_free(self):
        txn = self.approve()
        # Copy A: its last attempt fails -> FAILED and refunded.
        with patch("moviepy.VideoFileClip", side_effect=RuntimeError("A fails")), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"):
            with self.assertRaises(RuntimeError):
                self.run_last_attempt()
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)

        # Copy B (redelivered, or re-queued while A held the lock) runs now.
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, upload):
            render_video_segments(self.project.id)

        client.download_file.assert_not_called()
        upload.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_copy_does_not_start_once_its_reservation_was_settled(self):
        from apps.payments.services.wallet_service import rollback_reservation

        txn = self.approve()
        rollback_reservation(txn.id)  # e.g. refunded by settle_stale_reservations
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (client, _):
            render_video_segments(self.project.id)

        client.download_file.assert_not_called()
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)

    def test_the_final_failure_is_recorded_while_holding_the_lock(self):
        from apps.videos.tasks import render as render_module

        self.approve()
        lock_key = f"lock:pipeline:project:{self.project.id}"
        held = []
        real_fail = render_module._fail_render

        def fail_render(project_id, code):
            held.append(lock_key in self.redis.held)
            return real_fail(project_id, code)

        with patch("moviepy.VideoFileClip", side_effect=RuntimeError("fails")), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"), \
                patch.object(render_module, "_fail_render", side_effect=fail_render):
            with self.assertRaises(RuntimeError):
                self.run_last_attempt()

        self.assertEqual(held, [True])
        self.assertNotIn(lock_key, self.redis.held)  # released afterwards

    def test_a_failure_never_marks_a_delivered_render_failed_nor_refunds_it(self):
        from apps.payments.services.wallet_service import commit_reservation
        from apps.videos.tasks.render import _fail_render

        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.COMPLETED, final_export_r2_key="final/b.mp4",
        )
        commit_reservation(txn.id)  # another copy delivered and charged it

        _fail_render(self.project.id, "RuntimeError")

        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

    def test_an_export_is_not_saved_over_a_project_that_stopped_rendering(self):
        txn = self.approve()

        def another_copy_fails_it(*args, **kwargs):
            # A copy that could not take the lock failed the project meanwhile.
            VideoProject.objects.filter(pk=self.project.pk).update(status=VideoProject.Status.FAILED)
            return "final/late.mp4"

        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload), \
                patch(DELETE_OBJECT) as delete:
            upload.side_effect = another_copy_fails_it
            render_video_segments(self.project.id)

        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertFalse(self.project.final_export_r2_key)
        self.assertEqual(
            VideoClip.objects.filter(project=self.project, title="VIDEO FINAL EDITADO").count(), 0,
        )
        delete.assert_called_once_with("final/late.mp4")  # not left orphaned in R2
        self.assertEqual(txn.status, Transaction.Status.RESERVED)  # never charged

    def test_a_copy_that_lost_its_lock_does_not_save_its_export(self):
        import threading
        from contextlib import contextmanager
        from types import SimpleNamespace

        from celery.exceptions import Retry

        txn = self.approve()
        handle = SimpleNamespace(lost=threading.Event())

        @contextmanager
        def lock_that_gets_lost(project_id):
            yield handle

        def upload_then_lose_the_lock(*args, **kwargs):
            handle.lost.set()  # the heartbeat found the lock taken over
            return "final/lost.mp4"

        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload), \
                patch("apps.videos.tasks.render.project_lock", side_effect=lock_that_gets_lost), \
                patch(DELETE_OBJECT) as delete, \
                patch.object(render_video_segments, "retry", side_effect=Retry()):
            upload.side_effect = upload_then_lose_the_lock
            with self.assertRaises(Retry):
                render_video_segments(self.project.id)

        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertFalse(self.project.final_export_r2_key)
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        delete.assert_called_once_with("final/lost.mp4")
        self.assertEqual(txn.status, Transaction.Status.RESERVED)


class PaperEditTimeLimitTests(RenderReservationHarness, APITestCase):
    def test_the_render_has_soft_and_hard_limits_below_the_visibility_timeout(self):
        from django.conf import settings

        soft = render_video_segments.soft_time_limit
        hard = render_video_segments.time_limit
        self.assertEqual(soft, settings.RENDER_SEGMENTS_SOFT_TIME_LIMIT)
        self.assertEqual(hard, settings.RENDER_SEGMENTS_TIME_LIMIT)
        self.assertLess(soft, hard)
        self.assertLess(hard, settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"])

    def test_the_soft_limit_fails_the_render_for_good_and_refunds_it(self):
        from celery.exceptions import SoftTimeLimitExceeded

        txn = self.approve()
        source, processed = _render_mocks()
        processed.write_videofile.side_effect = SoftTimeLimitExceeded()
        with _patched_render(source, processed, redis=self.redis), \
                patch.object(render_video_segments, "retry") as retry:
            with self.assertRaises(SoftTimeLimitExceeded):
                render_video_segments(self.project.id)

        retry.assert_not_called()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertEqual(self.project.pipeline_error_code, "SoftTimeLimitExceeded")
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))


class PaperEditRetryNotQueuedTests(RenderReservationHarness, APITestCase):
    def test_a_retry_that_cannot_be_queued_fails_and_refunds_the_render(self):
        from celery.exceptions import Reject

        txn = self.approve()

        def broker_down(*args, **kwargs):
            raise Reject(RuntimeError("broker down"), requeue=False)

        with patch("moviepy.VideoFileClip", side_effect=RuntimeError("transient")), \
                patch("apps.videos.services.storage_service.CloudflareR2Manager.get_client"), \
                patch.object(render_video_segments, "retry", side_effect=broker_down):
            with self.assertRaises(RuntimeError):
                render_video_segments(self.project.id)

        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertEqual(txn.status, Transaction.Status.FAILED)


class PaperEditLockHeaderOnTheWorkerTests(RenderReservationHarness, APITestCase):
    """
    The lock budget is a custom message header: a worker exposes it on
    self.request.headers, and a worker on the previous code ignores it.
    """

    def test_a_worker_reads_the_header_and_sends_it_on_with_the_requeue(self):
        from celery.contrib.testing.mocks import TaskMessage
        from celery.worker.request import Request

        from backend.celery import app

        self.approve()
        lock = self.redis.lock(f"lock:pipeline:project:{self.project.id}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))
        message = TaskMessage(
            render_video_segments.name, args=[str(self.project.id)],
            **{RENDER_LOCK_RETRIES_HEADER: 3},
        )
        with patch.object(render_video_segments, "apply_async") as requeue:
            Request(message, app=app, task=render_video_segments).execute()

        requeue.assert_called_once()
        self.assertEqual(requeue.call_args.kwargs["headers"], {RENDER_LOCK_RETRIES_HEADER: 4})
        self.assertNotIn("kwargs", requeue.call_args.kwargs)
