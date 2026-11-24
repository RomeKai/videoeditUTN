"""
No paper-edit render stays RENDERING with its tokens reserved, and no
lock-busy re-queue is lost:

- recover_stuck_renders finds a render that nothing will bring back (a hard
  time limit kill acks its message; a lost redelivery) and re-queues it once,
  fails it with a refund past the caps, or completes and charges it when its
  export was saved. A project whose lock is held, or that started recently,
  is never touched, and the dry run writes nothing.
- A lock-busy re-queue that the broker does not take gives the message back
  to the broker (reject with requeue) instead of acking it away.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APITestCase

from apps.payments.models import Transaction
from apps.payments.services.wallet_service import reserve_funds
from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.tasks import process_initial_ingestion, render_video_segments
from apps.videos.tasks.render import RENDER_LOCK_RETRIES_HEADER, RENDER_SEGMENTS_MAX_REDELIVERIES
from apps.videos.tasks.render_recovery import recover_stuck_renders, recover_stuck_renders_now
from apps.videos.tasks.shared import LOCK_REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS
from apps.videos.tests.fakes import FakeRedis
from apps.videos.tests.test_approved_segments_source import (
    REDIS_CLIENT, _patched_render, _render_mocks,
)
from apps.videos.tests.test_reservation_settlement import RenderReservationHarness

User = get_user_model()

RESOLVE_SEGMENTS = "apps.videos.tasks.render._resolve_approved_segments"
PAUSE = "apps.videos.tasks.shared.time.sleep"


class HardKill(BaseException):
    """The worker process dies mid-render: no handler of the task runs."""


class StuckRenderHarness(RenderReservationHarness):
    def kill_render(self):
        """A delivery that starts rendering and loses its worker (hard time limit, OOM)."""
        with patch(RESOLVE_SEGMENTS, side_effect=HardKill), self.assertRaises(HardKill):
            render_video_segments(self.project.id)
        # A dead worker's lock expires with its TTL; the fake releases it on the way out.

    def metadata(self):
        self.project.refresh_from_db()
        return self.project.metadata

    def age(self, reservation_only=False):
        """Moves what the recovery reads back past RENDER_STUCK_AFTER_SECONDS."""
        past = timezone.now() - timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS + 60)
        metadata = dict(self.metadata())
        if not reservation_only:
            for key in ("render_started_at", "render_recovery_requeued_at"):
                if key in metadata:
                    metadata[key] = past.isoformat()
            VideoProject.objects.filter(pk=self.project.pk).update(metadata=metadata)
        Transaction.objects.filter(pk=metadata["render_reservation_id"]).update(created_at=past)

    def update_metadata(self, **values):
        VideoProject.objects.filter(pk=self.project.pk).update(metadata={**self.metadata(), **values})

    def recover(self, apply=True):
        with patch.object(render_video_segments, "apply_async") as requeue:
            rows = recover_stuck_renders_now(apply=apply)
        return rows, requeue

    def row(self, rows):
        return next(row for row in rows if row["project"] == self.project.pk)

    def hold_project_lock(self):
        lock = self.redis.lock(f"lock:pipeline:project:{self.project.id}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))

    def assert_untouched(self, txn, metadata):
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertEqual(self.project.metadata, metadata)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)


class RenderStartTimeTests(StuckRenderHarness, APITestCase):
    def test_a_start_records_when_it_happened_with_the_start_counter(self):
        self.approve()
        before = timezone.now()

        self.kill_render()

        metadata = self.metadata()
        self.assertEqual(metadata["render_unfinished_starts"], 1)
        started = datetime.fromisoformat(metadata["render_started_at"])
        self.assertGreaterEqual(started, before)


class StuckRenderRecoveryTests(StuckRenderHarness, APITestCase):
    def test_a_hard_killed_render_is_found_and_requeued_once(self):
        txn = self.approve()
        self.kill_render()
        self.age()

        rows, requeue = self.recover()

        requeue.assert_called_once_with(args=[str(self.project.id)])
        self.assertEqual(
            (self.row(rows)["action"], self.row(rows)["result"], self.row(rows)["starts"]),
            ("requeue", "requeued", 1),
        )
        metadata = self.metadata()
        self.assertEqual(metadata["render_recovery_requeues"], 1)
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

        # The next run, before the re-queued message could start, leaves it alone.
        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "recent"))
        self.assertEqual(self.metadata(), metadata)

    def test_the_requeued_render_is_delivered_and_charged_once(self):
        txn = self.approve()
        self.kill_render()
        self.age()
        self.recover()

        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload):
            render_video_segments(self.project.id)  # the re-queued message

        upload.assert_called_once()
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("100.00") - txn.amount, Decimal("0")))

    def test_stuck_again_after_the_requeue_cap_it_fails_and_is_refunded(self):
        txn = self.approve()
        self.kill_render()
        self.age()
        _, first = self.recover()
        first.assert_called_once()
        self.kill_render()  # the re-queued delivery starts and is killed too
        self.assertEqual(self.metadata()["render_unfinished_starts"], RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age()

        with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual(
            (self.row(rows)["action"], self.row(rows)["reason"]), ("fail", "lost_starts"),
        )
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertEqual(self.project.pipeline_error_code, "render_stuck")
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))
        output = "\n".join(logs.output)
        self.assertIn(f"render.recovery_failed project_id={self.project.id}", output)
        self.assertNotIn(self.project.title, output)

    def test_requeues_that_never_start_are_capped_too(self):
        txn = self.approve()  # its message never ran (e.g. the approval's delay failed)
        self.update_metadata(render_recovery_requeues=RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age(reservation_only=True)

        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("fail", "requeues"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)

    def test_a_render_that_never_started_is_requeued(self):
        self.approve()  # the approval's delay failed: nothing was ever queued
        self.age(reservation_only=True)

        rows, requeue = self.recover()

        requeue.assert_called_once_with(args=[str(self.project.id)])
        self.assertEqual(self.row(rows)["starts"], 0)

    def test_a_stuck_project_whose_export_was_saved_is_completed_and_charged_never_refunded(self):
        txn = self.approve()
        self.kill_render()
        VideoProject.objects.filter(pk=self.project.pk).update(final_export_r2_key="final/key.mp4")
        # Past both caps: without the export it would fail and be refunded.
        self.update_metadata(
            render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES,
            render_recovery_requeues=RENDER_SEGMENTS_MAX_REDELIVERIES,
        )
        self.age()

        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["result"]), ("deliver", "delivered"))
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("100.00") - txn.amount, Decimal("0")))

    def test_a_project_whose_lock_is_held_is_not_touched(self):
        txn = self.approve()
        self.kill_render()
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age()
        self.hold_project_lock()  # a live worker is rendering it
        metadata = self.metadata()

        for apply in (True, False):
            rows, requeue = self.recover(apply=apply)

            requeue.assert_not_called()
            self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "lock_held"))
            self.assert_untouched(txn, metadata)

    def test_a_project_that_started_recently_is_not_touched(self):
        txn = self.approve()
        self.kill_render()
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age(reservation_only=True)  # approved long ago, but it started just now
        metadata = self.metadata()

        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "recent"))
        self.assert_untouched(txn, metadata)
        self.assertNotIn(f"lock:pipeline:project:{self.project.id}", self.redis.held)

    def test_a_requeue_the_broker_refuses_changes_nothing(self):
        txn = self.approve()
        self.kill_render()
        self.age()
        metadata = self.metadata()

        with patch.object(
            render_video_segments, "apply_async", side_effect=OperationalError("broker down"),
        ), self.assertLogs("apps.videos.tasks", level="ERROR") as logs:
            rows = recover_stuck_renders_now(apply=True)

        self.assertEqual(self.row(rows)["result"], "error=OperationalError")
        self.assert_untouched(txn, metadata)  # the re-queue record rolled back
        self.assertIn("render.recovery_error", "\n".join(logs.output))
        self.assertNotIn("broker down", "\n".join(logs.output))

    def test_completed_and_whole_video_projects_are_never_candidates(self):
        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(status=VideoProject.Status.COMPLETED)
        self.age(reservation_only=True)
        whole_txn = reserve_funds(self.wallet.id, Decimal("5.00"), description="whole video")
        Transaction.objects.filter(pk=whole_txn.pk).update(created_at=timezone.now() - timedelta(days=2))
        whole = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title="whole",
            project_type=VideoProject.Type.SINGLE_EDIT, status=VideoProject.Status.RENDERING,
            metadata={"reservation_id": str(whole_txn.id), "priced_duration_s": 30},
        )

        rows, requeue = self.recover()

        self.assertEqual(rows, [])
        requeue.assert_not_called()
        self.project.refresh_from_db()
        whole.refresh_from_db()
        txn.refresh_from_db()
        whole_txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(whole.status, VideoProject.Status.RENDERING)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)  # settle_stale_reservations' job
        self.assertEqual(whole_txn.status, Transaction.Status.RESERVED)


class RecoverStuckRendersCommandTests(StuckRenderHarness, APITestCase):
    def run_command(self, *args):
        out = StringIO()
        with patch.object(render_video_segments, "apply_async") as requeue:
            call_command("recover_stuck_renders", *args, stdout=out)
        return out.getvalue(), requeue

    def line(self, output):
        line = next(item for item in output.splitlines() if item.startswith(f"project={self.project.id} "))
        return dict(part.split("=", 1) for part in line.split())

    def test_by_default_it_only_reports_and_writes_nothing(self):
        txn = self.approve()
        self.kill_render()
        self.age()
        metadata = self.metadata()

        output, requeue = self.run_command()

        requeue.assert_not_called()
        self.assert_untouched(txn, metadata)
        self.assertEqual(self.redis.held, {})  # not even the lock was taken
        line = self.line(output)
        self.assertEqual(
            (line["action"], line["reason"], line["result"], line["unfinished_starts"]),
            ("requeue", "no_render_left", "dry_run", "1"),
        )
        self.assertIn("summary mode=dry_run candidates=1 requeue=1", output)
        self.assertNotIn(self.project.title, output)

    def test_with_apply_it_recovers(self):
        self.approve()
        self.kill_render()
        self.age()

        output, requeue = self.run_command("--apply")

        requeue.assert_called_once_with(args=[str(self.project.id)])
        self.assertEqual(self.line(output)["result"], "requeued")
        self.assertIn("summary mode=apply", output)

    def test_an_error_is_reported_and_ends_with_an_error_code(self):
        self.approve()
        self.kill_render()
        self.age()

        with patch.object(render_video_segments, "apply_async", side_effect=OperationalError("x")), \
                self.assertRaises(CommandError):
            call_command("recover_stuck_renders", "--apply", stdout=StringIO())


class RecoverStuckRendersScheduleTests(StuckRenderHarness, APITestCase):
    def test_beat_runs_the_recovery_every_15_minutes(self):
        entry = settings.CELERY_BEAT_SCHEDULE["recover-stuck-renders"]

        self.assertEqual(entry["task"], recover_stuck_renders.name)
        self.assertEqual(entry["schedule"], 15 * 60)

    def test_the_task_applies_the_recovery(self):
        txn = self.approve()
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age(reservation_only=True)

        recover_stuck_renders()

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)

    def test_a_render_is_stuck_only_after_its_hard_time_limit(self):
        self.assertGreater(settings.RENDER_STUCK_AFTER_SECONDS, settings.RENDER_SEGMENTS_TIME_LIMIT)


class InlinePool:
    """The worker pool, in-process: runs the traced task and hands the outcome to the request."""

    def apply_async(self, target, args=(), callback=None, **kwargs):
        callback(target(*args))


def run_on_worker(task, message):
    """Runs one message as a worker does (acks_late) and returns its (ack, reject) callbacks."""
    from celery.worker.request import Request

    from backend.celery import app

    ack, reject = Mock(name="ack"), Mock(name="reject")
    Request(message, app=app, task=task, on_ack=ack, on_reject=reject).execute_using_pool(InlinePool())
    return ack, reject


class LockBusyRequeueNotPublishedTests(TestCase):
    """
    The lock-busy branch re-queues the message with apply_async. If the broker
    does not take it, the delivery used to end with that error: acks_late acks
    a failed task, so the only message of the project was lost. Now the same
    message goes back to the broker after a short pause.
    """

    def setUp(self):
        self.redis = FakeRedis()
        patcher = patch(REDIS_CLIENT, return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        user = User.objects.create_user(username="busy", email="busy@test.com", password="pw")
        workspace = Workspace.objects.create(name="Busy", owner=user)
        self.project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="Busy lock",
            status=VideoProject.Status.RENDERING,
        )
        lock = self.redis.lock(f"lock:pipeline:project:{self.project.id}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))  # a live worker holds the project

    def run_lock_busy(self, task, message):
        with patch.object(task, "apply_async", side_effect=OperationalError("Error 111 secret-host")), \
                patch(PAUSE) as pause, \
                self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            ack, reject = run_on_worker(task, message)
        return ack, reject, pause, "\n".join(logs.output)

    def assert_redelivered(self, ack, reject, pause):
        ack.assert_not_called()
        reject.assert_called_once()
        self.assertIs(reject.call_args.args[2], True)  # requeue
        pause.assert_called_once_with(LOCK_REQUEUE_NOT_PUBLISHED_PAUSE_SECONDS)

    def test_a_paper_edit_requeue_that_is_not_published_is_redelivered(self):
        from celery.contrib.testing.mocks import TaskMessage

        message = TaskMessage(
            render_video_segments.name, args=[str(self.project.id)], **{RENDER_LOCK_RETRIES_HEADER: 2},
        )

        ack, reject, pause, logs = self.run_lock_busy(render_video_segments, message)

        self.assert_redelivered(ack, reject, pause)
        self.assertIn(
            f"render.requeue_not_published project_id={self.project.id} error=OperationalError", logs,
        )
        self.assertNotIn("secret-host", logs)

    def test_an_ingestion_requeue_that_is_not_published_is_redelivered(self):
        from celery.contrib.testing.mocks import TaskMessage

        message = TaskMessage(
            process_initial_ingestion.name, args=[str(self.project.id)], kwargs={"lock_retries": 2},
        )

        ack, reject, pause, logs = self.run_lock_busy(process_initial_ingestion, message)

        self.assert_redelivered(ack, reject, pause)
        self.assertIn(
            f"pipeline.requeue_not_published project_id={self.project.id} error=OperationalError", logs,
        )
        self.assertNotIn("secret-host", logs)

    def test_a_published_requeue_still_acks_the_message(self):
        from celery.contrib.testing.mocks import TaskMessage

        message = TaskMessage(render_video_segments.name, args=[str(self.project.id)])
        with patch.object(render_video_segments, "apply_async") as requeue, patch(PAUSE) as pause:
            ack, reject = run_on_worker(render_video_segments, message)

        requeue.assert_called_once()
        ack.assert_called_once()
        reject.assert_not_called()
        pause.assert_not_called()
