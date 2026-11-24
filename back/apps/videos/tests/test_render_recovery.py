"""
No paper-edit render stays RENDERING with its tokens reserved:
recover_stuck_renders finds a render that nothing will bring back (a hard
time limit kill acks its message; a lost redelivery) and re-queues it once,
fails it with a refund past the caps, or completes and charges it when its
export was saved. A project whose lock is held, or that started recently, is
never touched, and the dry run writes nothing.

A re-queue the broker does not take: test_requeue_not_published.py.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APITestCase

from apps.payments.models import Transaction
from apps.payments.services.wallet_service import reserve_funds
from apps.videos.models import VideoProject
from apps.videos.tasks import render_video_segments
from apps.videos.tasks.render import RENDER_SEGMENTS_MAX_REDELIVERIES
from apps.videos.tasks.render_recovery import (
    RENDER_RECOVERY_MAX_REQUEUES,
    backoff_window,
    recover_stuck_renders,
    recover_stuck_renders_now,
)
from apps.videos.tests.test_approved_segments_source import _patched_render, _render_mocks
from apps.videos.tests.test_reservation_settlement import RenderReservationHarness


RESOLVE_SEGMENTS = "apps.videos.tasks.render._resolve_approved_segments"


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

    def age(self, reservation_only=False, seconds=None):
        """Moves what the recovery reads back past RENDER_STUCK_AFTER_SECONDS (or ``seconds``)."""
        if seconds is None:
            seconds = settings.RENDER_STUCK_AFTER_SECONDS + 60
        past = timezone.now() - timedelta(seconds=seconds)
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

    def test_a_render_that_keeps_losing_its_worker_fails_past_the_lost_starts_cap_and_is_refunded(self):
        txn = self.approve()
        actions = []
        for _ in range(RENDER_SEGMENTS_MAX_REDELIVERIES + 1):
            self.kill_render()  # the approval's delivery, then each re-queued one
            self.age()
            with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
                rows, requeue = self.recover()
            actions.append((self.row(rows)["action"], self.row(rows)["starts"]))

        # Re-queued while render_video_segments would still start it (lost
        # starts up to the cap), failed only past it.
        self.assertEqual(
            actions,
            [("requeue", n) for n in range(1, RENDER_SEGMENTS_MAX_REDELIVERIES + 1)]
            + [("fail", RENDER_SEGMENTS_MAX_REDELIVERIES + 1)],
        )
        requeue.assert_not_called()
        self.assertEqual(self.row(rows)["reason"], "lost_starts")
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.FAILED)
        self.assertEqual(self.project.pipeline_error_code, "render_stuck")
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))
        output = "\n".join(logs.output)
        self.assertIn(f"render.recovery_failed project_id={self.project.id}", output)
        self.assertNotIn(self.project.title, output)

    def test_at_the_lost_starts_cap_it_requeues_a_render_the_task_would_still_run(self):
        txn = self.approve()
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age(reservation_only=True)

        rows, requeue = self.recover()

        requeue.assert_called_once()
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload):
            render_video_segments(self.project.id)  # the re-queued message

        upload.assert_called_once()
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

    def test_a_render_that_never_starts_keeps_being_requeued_with_backoff_never_failed(self):
        # Its message waits for a render worker that is down, or it was lost
        # with the broker: re-queued again and again, further apart each time.
        txn = self.approve()
        stuck_after = timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS)
        steps = []
        for in_a_row in range(5):
            window = backoff_window(stuck_after, in_a_row)
            self.age(seconds=window - 60)  # not yet: only reported (or recent)
            rows, early = self.recover()
            self.age(seconds=window + 60)
            with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
                rows, requeue = self.recover()
            steps.append((early.call_count, requeue.call_count, self.row(rows)["requeues"]))
            if in_a_row:
                self.assertIn(
                    f"render.recovery_requeued project_id={self.project.id} reason=never_started",
                    "\n".join(logs.output),
                )

        self.assertEqual(steps, [(0, 1, n) for n in range(5)])
        self.assertEqual(self.metadata()["render_recovery_requeues_total"], 5)
        # Waiting out the window is reported on every run.
        self.age(seconds=settings.RENDER_STUCK_AFTER_SECONDS + 60)
        with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            rows, requeue = self.recover()
        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("report", "never_started"))
        self.assertIn(f"render.recovery_not_started project_id={self.project.id}", "\n".join(logs.output))
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

        # A worker comes back: one copy renders it, once.
        source, processed = _render_mocks()
        with _patched_render(source, processed, redis=self.redis) as (_, upload):
            render_video_segments(self.project.id)
            render_video_segments(self.project.id)  # a duplicate copy

        upload.assert_called_once()
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)

    def test_the_backoff_window_doubles_up_to_a_day(self):
        hour = timedelta(hours=1)

        self.assertEqual(
            [backoff_window(hour, n) / 3600 for n in range(7)], [1, 2, 4, 8, 16, 24, 24],
        )
        self.assertEqual(backoff_window(timedelta(hours=30), 3), 30 * 3600)  # never below the threshold

    def test_requeues_whose_starts_end_and_get_lost_stop_at_the_total_cap(self):
        """
        Each re-queued start ends in a handled error (its start is closed) and
        its retry message is lost: the lost-starts cap never sees it.
        """
        from celery.exceptions import Retry

        txn = self.approve()
        actions = []
        for _ in range(RENDER_RECOVERY_MAX_REQUEUES + 2):
            with patch(RESOLVE_SEGMENTS, side_effect=ConnectionError("r2")), \
                    patch.object(render_video_segments, "retry", side_effect=Retry()), \
                    self.assertRaises(Retry):
                render_video_segments(self.project.id)  # its retry is then lost
            self.age()
            with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
                rows, _ = self.recover()
            actions.append((self.row(rows)["action"], self.row(rows)["reason"]))

        self.assertEqual(
            actions,
            [("requeue", "no_render_left")] * RENDER_RECOVERY_MAX_REQUEUES
            + [("report", "requeue_total_cap")] * 2,
        )
        self.assertIn(
            f"render.recovery_reported project_id={self.project.id} reason=requeue_total_cap",
            "\n".join(logs.output),
        )
        self.project.refresh_from_db()
        txn.refresh_from_db()
        # Never failed nor refunded by the routine: an operator decides.
        self.assertEqual(self.project.status, VideoProject.Status.RENDERING)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_a_start_after_the_last_requeue_resets_the_requeue_count(self):
        self.approve()
        requeued_at = timezone.now() - timedelta(seconds=settings.RENDER_STUCK_AFTER_SECONDS + 120)
        self.update_metadata(
            render_unfinished_starts=1,
            render_recovery_requeues=RENDER_SEGMENTS_MAX_REDELIVERIES,
            render_recovery_requeued_at=requeued_at.isoformat(),
            render_started_at=(requeued_at + timedelta(seconds=60)).isoformat(),
        )
        self.age(reservation_only=True)

        rows, requeue = self.recover()

        requeue.assert_called_once()
        self.assertEqual(self.row(rows)["requeues"], 0)
        self.assertEqual(self.metadata()["render_recovery_requeues"], 1)

    def test_a_render_that_never_started_is_requeued(self):
        self.approve()  # the approval's delay failed: nothing was ever queued
        self.age(reservation_only=True)

        rows, requeue = self.recover()

        requeue.assert_called_once_with(args=[str(self.project.id)])
        self.assertEqual(self.row(rows)["starts"], 0)

    def test_a_delivered_render_left_unpaid_is_charged_never_refunded(self):
        txn = self.approve()
        self.kill_render()
        # Saved (with COMPLETED), then its worker was killed before charging.
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.COMPLETED, final_export_r2_key="final/key.mp4",
        )
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
        self.age()

        with self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual(
            (self.row(rows)["action"], self.row(rows)["reason"], self.row(rows)["result"]),
            ("charge", "delivered_unpaid", "charged"),
        )
        self.assertIn(f"render.recovery_charged project_id={self.project.id}", "\n".join(logs.output))
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(self.project.pipeline_error_code, "")
        self.assertEqual(txn.status, Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("100.00") - txn.amount, Decimal("0")))

    def test_a_charge_that_fails_is_reported_as_an_error_not_as_charged(self):
        from django.db import DatabaseError

        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.COMPLETED, final_export_r2_key="final/key.mp4",
        )
        self.age(reservation_only=True)

        with patch(
            "apps.payments.services.wallet_service.commit_reservation", side_effect=DatabaseError("x"),
        ), self.assertLogs("apps.videos.tasks", level="WARNING") as logs:
            rows, _ = self.recover()

        self.assertEqual(
            (self.row(rows)["action"], self.row(rows)["result"]), ("error", "error=settle_failed"),
        )
        output = "\n".join(logs.output)
        self.assertNotIn("render.recovery_charged", output)
        self.assertIn(f"render.recovery_error project_id={self.project.id} error=settle_failed", output)
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_a_charge_settled_meanwhile_is_reported_as_already_settled(self):
        from apps.payments.services.wallet_service import commit_reservation
        from apps.videos.tasks import render_recovery

        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.COMPLETED, final_export_r2_key="final/key.mp4",
        )
        self.age(reservation_only=True)

        def settled_elsewhere(project_id, charge):
            commit_reservation(txn.id)  # e.g. settle_stale_reservations got there first
            return False

        with patch.object(render_recovery, "_settle_render_reservation", side_effect=settled_elsewhere):
            rows, _ = self.recover()

        self.assertEqual(
            (self.row(rows)["action"], self.row(rows)["result"]), ("skip", "already_settled"),
        )

    def test_a_delivered_render_being_charged_right_now_is_left_alone(self):
        txn = self.approve()
        self.kill_render()  # started just now
        VideoProject.objects.filter(pk=self.project.pk).update(
            status=VideoProject.Status.COMPLETED, final_export_r2_key="final/key.mp4",
        )
        self.age(reservation_only=True)

        rows, _ = self.recover()

        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "recent"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)

    def test_a_rendering_project_with_an_export_is_never_failed_nor_refunded(self):
        txn = self.approve()
        VideoProject.objects.filter(pk=self.project.pk).update(final_export_r2_key="final/key.mp4")
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
        self.age(reservation_only=True)
        metadata = self.metadata()

        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("report", "export_saved"))
        self.assert_untouched(txn, metadata)

    def test_a_project_whose_lock_is_held_is_not_touched(self):
        txn = self.approve()
        self.kill_render()
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
        self.age()
        self.hold_project_lock()  # a live worker is rendering it
        metadata = self.metadata()

        for apply in (True, False):
            rows, requeue = self.recover(apply=apply)

            requeue.assert_not_called()
            self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "lock_held"))
            self.assert_untouched(txn, metadata)

    def test_a_project_that_started_recently_is_not_touched_nor_locked(self):
        txn = self.approve()
        self.kill_render()
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
        self.age(reservation_only=True)  # approved long ago, but it started just now
        metadata = self.metadata()
        calls = len(self.redis.lock_calls)

        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "recent"))
        self.assert_untouched(txn, metadata)
        # Only the read-only check: the project lock was never taken, so a
        # render message arriving now does not lose a lock retry to it.
        self.assertEqual([timeout for _, timeout in self.redis.lock_calls[calls:]], [None])

    def test_a_reported_project_is_not_locked_either(self):
        self.approve()
        self.update_metadata(render_recovery_requeues=RENDER_SEGMENTS_MAX_REDELIVERIES)
        self.age()
        calls = len(self.redis.lock_calls)

        rows, _ = self.recover()

        self.assertEqual(self.row(rows)["action"], "report")
        self.assertEqual([timeout for _, timeout in self.redis.lock_calls[calls:]], [None])

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

    def test_a_completed_project_is_never_failed_or_refunded(self):
        txn = self.approve()
        # COMPLETED without an export: not a delivery this routine can charge.
        VideoProject.objects.filter(pk=self.project.pk).update(status=VideoProject.Status.COMPLETED)
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
        self.age(reservation_only=True)

        rows, requeue = self.recover()

        requeue.assert_not_called()
        self.assertEqual((self.row(rows)["action"], self.row(rows)["reason"]), ("skip", "not_delivered"))
        self.project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.project.status, VideoProject.Status.COMPLETED)
        self.assertEqual(txn.status, Transaction.Status.RESERVED)  # settle_stale_reservations' job

    def test_whole_video_projects_are_never_candidates(self):
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
        whole.refresh_from_db()
        whole_txn.refresh_from_db()
        self.assertEqual(whole.status, VideoProject.Status.RENDERING)
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
        self.update_metadata(render_unfinished_starts=RENDER_SEGMENTS_MAX_REDELIVERIES + 1)
        self.age(reservation_only=True)

        recover_stuck_renders()

        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)

    def test_a_render_is_stuck_only_after_its_hard_time_limit(self):
        self.assertGreater(settings.RENDER_STUCK_AFTER_SECONDS, settings.RENDER_SEGMENTS_TIME_LIMIT)

    def test_the_environment_can_raise_the_threshold_but_never_below_the_hard_limit(self):
        import runpy

        import backend.settings.base as base

        def stuck_after(**env):
            with patch.dict("os.environ", {"RENDER_SEGMENTS_TIME_LIMIT": "1000", **env}):
                return runpy.run_path(base.__file__)["RENDER_STUCK_AFTER_SECONDS"]

        self.assertEqual(stuck_after(), 1900)
        self.assertEqual(stuck_after(RENDER_STUCK_AFTER_SECONDS="60"), 1900)
        self.assertEqual(stuck_after(RENDER_STUCK_AFTER_SECONDS="5000"), 5000)
