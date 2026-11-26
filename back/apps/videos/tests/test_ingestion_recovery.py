"""
No ingestion stays "processing" with its creation tokens reserved because its
message is gone: recover_stuck_renders (kind=ingestion) finds a project that
makes no progress (no worker holds its lock, nothing started, failed or was
re-queued for ingestion_stuck_after()), re-queues it at most
INGESTION_RECOVERY_MAX_REQUEUES times, and past that and ingestion_fail_after()
fails it with IngestionStuck and returns the reservation, once, in the same
transaction. Work already delivered (clips selected) is never refunded, a
project whose lock is held or that moved recently is never touched, and a copy
of the message that arrives after the refund never runs it for free.

The restore a settled render asked for later left undone (a database blip
after the settle) is retried by the same routine (kind=restore), and the stale
sweep reports it apart from the settle.
"""

import logging
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from apps.payments.models import Transaction
from apps.payments.services.wallet_service import reserve_funds
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage, mark_failed
from apps.videos.tasks import ingestion, process_initial_ingestion, render_recovery
from apps.videos.tasks.render_recovery import recover_stuck_renders, recover_stuck_renders_now
from apps.videos.tests.fakes import FakeRedis
from apps.videos.tests.test_approved_segments_source import CLIP_DELAY
from apps.videos.tests.test_clip_actions import DONE, DRAFT, ClipActionsHarness, clip_url
from apps.videos.tests.test_create_cleanup import DELAY, ROLLBACK, CreationHarness
from apps.videos.tests.test_ingestion_pipeline import REDIS_CLIENT, IngestionHarness, fund_wallet

RESERVED, CHARGED, REFUNDED = Transaction.Status.RESERVED, Transaction.Status.COMPLETED, Transaction.Status.FAILED
STARTED_AT, REQUEUES, REQUEUED_AT = (
    "ingestion_started_at", "ingestion_recovery_requeues", "ingestion_recovery_requeued_at",
)
DELETE_OBJECT = "apps.videos.services.storage_service.CloudflareR2Manager.delete_object"


class HardKill(BaseException):
    """The worker process dies mid-stage (the hard time limit): no handler of the task runs."""


def stuck_after():
    return render_recovery.ingestion_stuck_after()


def fail_after():
    return render_recovery.ingestion_fail_after()


class StuckIngestionHarness(IngestionHarness):
    """A funded workspace and the pipeline's mocks (IngestionHarness); Redis is a fake."""

    def setUp(self):
        super().setUp()
        self.wallet = fund_wallet(self.workspace, "100.00")

    def lost(self, stage=PipelineStage.UPLOADED, status=VideoProject.Status.UPLOADED, idle=None, **fields):
        """A project created ``idle`` seconds ago with its creation reserved, and no message left."""
        txn = reserve_funds(self.wallet.id, Decimal("10.00"))
        metadata = {"reservation_id": str(txn.id), **fields.pop("metadata", {})}
        project = self.make_project(status=status, pipeline_stage=stage, metadata=metadata, **fields)
        self.age(project, idle)
        return project, txn

    def age(self, project, seconds=None):
        """Moves every moment the sweep reads back ``seconds`` (by default, just past ingestion_stuck_after)."""
        past = timezone.now() - timedelta(seconds=stuck_after() + 60 if seconds is None else seconds)
        project.refresh_from_db()
        metadata = dict(project.metadata)
        for key in (STARTED_AT, REQUEUED_AT):
            if key in metadata:
                metadata[key] = past.isoformat()
        updates = {"created_at": past, "metadata": metadata}
        if project.pipeline_error_at:
            updates["pipeline_error_at"] = past
        VideoProject.objects.filter(pk=project.pk).update(**updates)
        Transaction.objects.filter(pk=metadata["reservation_id"]).update(created_at=past)
        project.refresh_from_db()

    def spend_requeues(self, project):
        project.refresh_from_db()
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            **project.metadata, REQUEUES: render_recovery.INGESTION_RECOVERY_MAX_REQUEUES,
            REQUEUED_AT: timezone.now().isoformat(),
        })

    def sweep(self, project, apply=True, requeue_error=None):
        """Runs the routine with the re-queue captured; returns (this project's row or None, apply_async)."""
        with patch.object(process_initial_ingestion, "apply_async", side_effect=requeue_error) as requeue:
            rows = recover_stuck_renders_now(apply=apply)
        row = next((row for row in rows if row["project"] == project.pk and row["kind"] == "ingestion"), None)
        return row, requeue

    @staticmethod
    def chain(project):
        """The chain stored in the project (tasks/ingestion.py, INGESTION_CHAIN_KEY), or None."""
        project.refresh_from_db()
        return project.metadata.get("ingestion_chain")

    def run_chain(self, project, chain):
        """A delivery of ``chain``'s message (``run_task`` is one without a token: the creation's)."""
        return process_initial_ingestion.apply(args=[str(project.pk)], kwargs={"chain": chain} if chain else {})

    def run_requeued(self, project):
        """The message the sweep re-queued, with the chain it stored."""
        return self.run_chain(project, self.chain(project))

    def assert_requeued_once(self, requeue, project):
        requeue.assert_called_once_with(args=[str(project.pk)], kwargs={"chain": self.chain(project)})
        self.assertIsNotNone(self.chain(project))

    def kill_at_transcription(self, project):
        """A delivery that starts, extracts the audio and dies transcribing (its message acked)."""
        self.mock_transcribe.side_effect = HardKill
        with self.assertRaises(HardKill):
            process_initial_ingestion(str(project.pk))
        self.mock_transcribe.side_effect = None
        # A dead worker's lock expires with its TTL; the fake releases it on the way out.

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def locks_taken(self):
        """The project locks taken (a read of whether one is held asks for no TTL)."""
        return [call for call in self.redis.lock_calls if call[1] is not None]

    def hold_lock(self, project):
        lock = self.redis.lock(f"lock:pipeline:project:{project.pk}", timeout=300)
        self.assertTrue(lock.acquire(blocking=False))


class StartMarkerTests(StuckIngestionHarness, TestCase):
    def test_every_delivery_that_runs_the_ingestion_records_when_it_started(self):
        project, _txn = self.lost()
        before = timezone.now()

        self.kill_at_transcription(project)

        project.refresh_from_db()
        self.assertGreaterEqual(datetime.fromisoformat(project.metadata[STARTED_AT]), before)
        self.assertEqual(project.pipeline_stage_status.get(PipelineStage.TRANSCRIBED), "running")


class StuckIngestionRecoveryTests(StuckIngestionHarness, TestCase):
    def test_a_message_that_never_ran_is_requeued_once_and_recorded(self):
        project, txn = self.lost()

        row, requeue = self.sweep(project)

        self.assert_requeued_once(requeue, project)
        self.assertEqual((row["action"], row["reason"], row["result"]), ("requeue", "never_started", "requeued"))
        project.refresh_from_db()
        self.assertEqual(project.metadata[REQUEUES], 1)
        self.assertIn(REQUEUED_AT, project.metadata)
        self.assertEqual(project.status, VideoProject.Status.UPLOADED)
        txn.refresh_from_db()
        self.assertEqual(txn.status, RESERVED)

        # The next run, before that message could start, leaves it alone.
        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("skip", "recent"))

    def test_the_requeued_delivery_runs_and_charges_once_and_a_late_copy_changes_nothing(self):
        project, txn = self.lost()
        self.sweep(project)

        self.run_requeued(project)  # the re-queued message
        self.run_task(project)  # the original, delivered late after all: another chain

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        txn.refresh_from_db()
        self.assertEqual(txn.status, CHARGED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))
        self.mock_transcribe.assert_called_once()

    def test_a_worker_that_died_mid_stage_is_requeued_and_resumes_where_it_stopped(self):
        project, txn = self.lost()
        self.kill_at_transcription(project)
        self.age(project)

        row, _requeue = self.sweep(project)

        self.assertEqual((row["action"], row["reason"]), ("requeue", "worker_lost"))
        self.mock_upload.reset_mock()
        self.run_requeued(project)
        self.mock_upload.assert_not_called()  # the source is not prepared again
        txn.refresh_from_db()
        self.assertEqual(txn.status, CHARGED)

    def test_a_project_whose_lock_is_held_is_never_touched(self):
        project, txn = self.lost()
        self.hold_lock(project)
        metadata = dict(project.metadata)

        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("skip", "lock_held"))
        project.refresh_from_db()
        self.assertEqual(project.metadata, metadata)

    def test_a_project_that_started_or_failed_recently_is_not_touched_nor_locked(self):
        for moment in ("start", "error"):
            with self.subTest(moment=moment):
                project, _txn = self.lost(status=VideoProject.Status.INGESTING)
                if moment == "start":
                    VideoProject.objects.filter(pk=project.pk).update(
                        metadata={**project.metadata, STARTED_AT: timezone.now().isoformat()},
                    )
                else:
                    VideoProject.objects.filter(pk=project.pk).update(pipeline_error_at=timezone.now())

                row, requeue = self.sweep(project)

                requeue.assert_not_called()
                self.assertEqual((row["action"], row["reason"]), ("skip", "recent"))
                self.assertEqual(self.locks_taken(), [])

    def test_a_new_project_is_not_even_a_candidate(self):
        project, _txn = self.lost(idle=60)

        row, requeue = self.sweep(project)

        self.assertIsNone(row)
        requeue.assert_not_called()

    def test_the_dry_run_writes_nothing_and_takes_no_lock(self):
        project, txn = self.lost()
        metadata = dict(project.metadata)

        row, requeue = self.sweep(project, apply=False)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["result"]), ("requeue", "dry_run"))
        self.assertEqual(self.locks_taken(), [])
        project.refresh_from_db()
        self.assertEqual(project.metadata, metadata)

    def test_a_requeue_the_broker_refuses_records_nothing(self):
        project, _txn = self.lost()

        row, _requeue = self.sweep(project, requeue_error=OperationalError("broker down"))

        self.assertEqual((row["action"], row["result"]), ("error", "error=OperationalError"))
        project.refresh_from_db()
        self.assertNotIn(REQUEUES, project.metadata)
        self.assertEqual(self.redis.held, {})  # its lock was released

    def test_past_its_requeues_it_waits_then_fails_and_refunds_once(self):
        project, txn = self.lost()
        self.spend_requeues(project)
        self.age(project)  # idle past the re-queue window, not yet past the fail window

        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("report", "requeue_cap"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, RESERVED)

        self.age(project, fail_after() + 60)
        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"], row["result"]), ("fail", "no_progress", "failed"))
        project.refresh_from_db()
        self.assertEqual(
            (project.status, project.pipeline_stage, project.pipeline_error_code),
            (VideoProject.Status.FAILED, PipelineStage.FAILED, "IngestionStuck"),
        )
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

        # Settled: never a candidate again, never refunded twice.
        row, _requeue = self.sweep(project)
        self.assertIsNone(row)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_late_copy_of_its_message_never_runs_the_refunded_project(self):
        project, txn = self.lost()
        self.spend_requeues(project)
        self.age(project, fail_after() + 60)
        self.sweep(project)

        result = self.run_task(project)

        self.assertIn("Not resumed", result.result)
        self.mock_transcribe.assert_not_called()
        project.refresh_from_db()
        self.assertEqual((project.status, project.pipeline_error_code), (VideoProject.Status.FAILED, "IngestionStuck"))
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_selected_clips_are_requeued_and_never_refunded(self):
        project, txn = self.lost(stage=PipelineStage.CLIPS_SELECTED, status=VideoProject.Status.INGESTING)
        VideoClip.objects.create(project=project, title="Clip", start_time=0, end_time=5)

        row, _requeue = self.sweep(project)
        self.assertEqual(row["action"], "requeue")

        self.spend_requeues(project)
        self.age(project, fail_after() + 60)
        row, _requeue = self.sweep(project)

        self.assertEqual((row["action"], row["reason"]), ("report", "clips_selected"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, RESERVED)

        # The re-queued delivery charges it and hands it to the review.
        self.run_requeued(project)
        txn.refresh_from_db()
        project.refresh_from_db()
        self.assertEqual((txn.status, project.status), (CHARGED, VideoProject.Status.AWAITING_APPROVAL))

    def test_a_reservation_older_than_a_day_is_refunded_and_never_requeued(self):
        """No paid stage runs again for a project the user paid for days ago (the first run after a deploy)."""
        project, txn = self.lost(idle=2 * 24 * 3600)
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            **project.metadata, STARTED_AT: (timezone.now() - timedelta(seconds=stuck_after() + 60)).isoformat(),
        })

        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("report", "too_old"))  # a delivery may still be alive

        self.age(project, 2 * 24 * 3600)
        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"], row["result"]), ("fail", "too_old", "failed"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_old_selected_clips_are_still_requeued(self):
        """At clips_selected only the charge and the review are left: no paid stage runs again."""
        project, _txn = self.lost(
            stage=PipelineStage.CLIPS_SELECTED, status=VideoProject.Status.INGESTING, idle=2 * 24 * 3600,
        )
        VideoClip.objects.create(project=project, title="Clip", start_time=0, end_time=5)

        row, requeue = self.sweep(project)

        self.assert_requeued_once(requeue, project)
        self.assertEqual(row["action"], "requeue")

    def test_the_beat_task_applies_it(self):
        project, txn = self.lost()
        self.spend_requeues(project)
        self.age(project, fail_after() + 60)

        recover_stuck_renders()

        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)

    def test_the_command_lists_it_with_its_kind(self):
        project, _txn = self.lost()
        out = StringIO()

        call_command("recover_stuck_renders", stdout=out)

        line = next(item for item in out.getvalue().splitlines() if item.startswith(f"project={project.pk} "))
        fields = dict(part.split("=", 1) for part in line.split())
        self.assertEqual(
            (fields["kind"], fields["action"], fields["reason"], fields["result"], fields["requeues"]),
            ("ingestion", "requeue", "never_started", "dry_run", "0"),
        )


class StuckIngestionRedispatchTests(StuckIngestionHarness, APITestCase):
    def test_a_redispatch_pays_again_runs_and_starts_its_own_count(self):
        project, old = self.lost()
        self.spend_requeues(project)
        self.age(project, fail_after() + 60)
        self.sweep(project)
        client = APIClient()
        client.force_authenticate(user=self.user)
        client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))

        with patch("apps.videos.tasks.process_initial_ingestion.delay") as delay:
            response = client.post(reverse("project-redispatch", args=[project.pk]))

        self.assertEqual(response.status_code, 202, response.content[:300])
        delay.assert_called_once_with(str(project.pk), chain=self.chain(project))
        self.run_task(project)  # a copy of an older chain: runs nothing
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.run_chain(project, delay.call_args.kwargs["chain"])
        project.refresh_from_db()
        self.assertEqual((project.status, project.pipeline_error_code), (VideoProject.Status.AWAITING_APPROVAL, ""))
        new = Transaction.objects.get(pk=project.metadata["reservation_id"])
        old.refresh_from_db()
        self.assertEqual((old.status, new.status), (REFUNDED, CHARGED))
        self.assertNotIn(REQUEUES, project.metadata)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))


class UnconfirmedCreationTests(CreationHarness, TransactionTestCase):
    """
    The create view could not queue the ingestion nor record that
    (NOT_QUEUED_UNCONFIRMED): real commits, so its on_commit queueing runs.
    """

    def test_its_ingestion_is_requeued_by_the_recovery(self):
        with patch(DELAY, side_effect=OperationalError("broker down")), \
                patch(ROLLBACK, side_effect=DatabaseError("db down")), \
                self.assertLogs("apps.videos.views", level=logging.ERROR):
            response = self.upload()
        self.assertEqual((response.status_code, response.data["refunded"]), (503, False))
        project = VideoProject.objects.get(pk=response.data["project_id"])
        past = timezone.now() - timedelta(seconds=stuck_after() + 60)
        VideoProject.objects.filter(pk=project.pk).update(created_at=past)
        Transaction.objects.filter(pk=project.metadata["reservation_id"]).update(created_at=past)

        with patch(REDIS_CLIENT, return_value=FakeRedis()), \
                patch.object(process_initial_ingestion, "apply_async") as requeue:
            rows = recover_stuck_renders_now(apply=True)

        project.refresh_from_db()
        requeue.assert_called_once_with(args=[str(project.pk)], kwargs={"chain": project.metadata["ingestion_chain"]})
        row = next(row for row in rows if row["project"] == project.pk)
        self.assertEqual((row["kind"], row["action"], row["reason"]), ("ingestion", "requeue", "never_started"))


class ChainTests(StuckIngestionHarness, APITestCase):
    """
    One chain of messages runs the ingestion (INGESTION_CHAIN_KEY): a re-queue
    of the sweep, a re-dispatch or the override starts a new one, and a copy of
    an older chain that was only delayed (a backed-up queue, a countdown held
    by a busy worker, the visibility timeout of a dead host) runs nothing.
    """

    def test_a_late_original_copy_never_reruns_a_project_its_requeue_failed_and_refunded(self):
        """The review's scenario: the re-queued run fails for good, and then the original arrives."""
        from apps.videos.services.ai.errors import AIAuthenticationError

        project, txn = self.lost()
        row, _requeue = self.sweep(project)
        self.assertEqual(row["action"], "requeue")
        self.mock_transcribe.side_effect = AIAuthenticationError("bad key")
        self.run_requeued(project)  # fails for good: refunded
        self.mock_transcribe.side_effect = None
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)
        self.mock_transcribe.reset_mock()

        result = self.run_task(project)  # the original message, only delayed

        self.assertIn("Not resumed", result.result)
        self.mock_transcribe.assert_not_called()
        self.mock_select.assert_not_called()
        project.refresh_from_db()
        self.assertEqual((project.status, project.pipeline_stage), (VideoProject.Status.FAILED, PipelineStage.FAILED))
        self.assertFalse(VideoClip.objects.filter(project=project).exists())
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_with_two_chains_alive_only_the_current_one_runs(self):
        project, txn = self.lost()
        self.sweep(project)
        current = self.chain(project)

        stale = self.run_task(project)  # the original, delivered first
        self.assertIn("Skipped", stale.result)
        self.mock_upload.assert_not_called()
        self.mock_transcribe.assert_not_called()
        project.refresh_from_db()
        self.assertNotIn(STARTED_AT, project.metadata)  # nothing started for it
        self.assertEqual(project.status, VideoProject.Status.UPLOADED)

        self.run_chain(project, current)
        self.run_chain(project, "an-older-chain")  # any other token runs nothing either

        self.mock_transcribe.assert_called_once()
        self.mock_select.assert_called_once()
        project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual((project.status, txn.status), (VideoProject.Status.AWAITING_APPROVAL, CHARGED))
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_two_chains_never_plan_the_capacity_wait_or_spend_retries_twice(self):
        from apps.videos.services.ai.capacity import CapacityUnavailable

        project, _txn = self.lost(stage=PipelineStage.AUDIO_EXTRACTED, status=VideoProject.Status.INGESTING)
        self.sweep(project)
        current = self.chain(project)
        self.mock_transcribe.side_effect = CapacityUnavailable(60, stage=PipelineStage.TRANSCRIBED)

        with patch.object(process_initial_ingestion, "apply_async") as requeue:
            self.run_chain(project, current)
            self.run_task(project)  # the stale chain, waiting for capacity too
            self.run_task(project)

        # One wait planned, one re-queue, carrying the current chain on.
        requeue.assert_called_once()
        self.assertEqual(requeue.call_args.kwargs["kwargs"], {"parse_retries": 0, "chain": current})
        project.refresh_from_db()
        planned = project.metadata["capacity_wait"]["planned_s"]
        self.assertEqual(planned, round(requeue.call_args.kwargs["countdown"], 1))
        self.assertEqual(self.mock_transcribe.call_count, 1)
        self.assertEqual(project.pipeline_attempts, 0)

    def test_every_republish_of_a_delivery_carries_its_chain(self):
        from apps.videos.services.ai.prompt_validator import PromptParseRetry

        project, _txn = self.lost()
        self.sweep(project)
        current = self.chain(project)

        self.hold_lock(project)  # another worker: the delivery waits for the lock
        with patch.object(process_initial_ingestion, "apply_async") as requeue:
            self.run_chain(project, current)
        self.assertEqual(requeue.call_args.kwargs["kwargs"], {"lock_retries": 1, "parse_retries": 0, "chain": current})
        self.redis.held.clear()

        parse = "apps.videos.services.ai.prompt_validator.apply_prompt_to_project"
        with patch(parse, side_effect=PromptParseRetry()), \
                patch.object(process_initial_ingestion, "apply_async") as requeue:
            self.run_chain(project, current)
        self.assertEqual(requeue.call_args.kwargs["kwargs"], {"lock_retries": 0, "parse_retries": 1, "chain": current})

    def test_of_a_double_redispatch_only_the_second_message_runs(self):
        project, _old = self.lost()
        self.spend_requeues(project)
        self.age(project, fail_after() + 60)
        self.sweep(project)  # failed and refunded
        client = APIClient()
        client.force_authenticate(user=self.user)
        client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))

        with patch("apps.videos.tasks.process_initial_ingestion.delay") as delay:
            first = client.post(reverse("project-redispatch", args=[project.pk]))
            second = client.post(reverse("project-redispatch", args=[project.pk]))

        self.assertEqual((first.status_code, second.status_code), (202, 202))
        chains = [call.kwargs["chain"] for call in delay.call_args_list]
        self.assertEqual(len(set(chains)), 2)
        self.assertIn("Not resumed", self.run_chain(project, chains[0]).result)
        self.run_chain(project, chains[1])
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.mock_transcribe.assert_called_once()
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))  # the new reservation, charged once

    def test_the_moderation_override_requeues_a_stuck_project_with_its_own_chain(self):
        from django.contrib.auth import get_user_model

        project, txn = self.lost()
        self.spend_requeues(project)
        self.age(project, fail_after() + 60)
        self.sweep(project)
        admin = get_user_model().objects.create_superuser(username="adm", email="adm@test.com", password="pw")
        self.client.force_login(admin)

        with patch("apps.videos.tasks.process_initial_ingestion.delay") as delay, \
                self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:videos_moderationoverride_add"),
                {"project": str(project.pk), "reason": "Revisión humana"}, follow=True,
            )

        self.assertContains(response, "se vuelve a encolar")
        self.assertContains(response, "sin volver a cobrar")
        delay.assert_called_once_with(str(project.pk), chain=self.chain(project))
        self.run_chain(project, delay.call_args.kwargs["chain"])
        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)  # the admin's re-run is not charged again


class RequeueCountTests(StuckIngestionHarness, TestCase):
    def test_a_start_after_the_last_requeue_resets_the_requeues_in_a_row(self):
        project, _txn = self.lost(status=VideoProject.Status.INGESTING)
        moment = (timezone.now() - timedelta(seconds=stuck_after() + 60)).isoformat()
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            **project.metadata, REQUEUES: 2, "ingestion_recovery_requeues_total": 2,
            REQUEUED_AT: moment, STARTED_AT: moment,  # the last re-queue started, then its worker died
        })

        row, requeue = self.sweep(project)

        self.assert_requeued_once(requeue, project)
        self.assertEqual((row["action"], row["reason"]), ("requeue", "worker_lost"))
        project.refresh_from_db()
        self.assertEqual((project.metadata[REQUEUES], project.metadata["ingestion_recovery_requeues_total"]), (1, 3))

    def test_the_total_bounds_a_delivery_that_starts_and_dies_every_time(self):
        project, txn = self.lost(status=VideoProject.Status.INGESTING)
        moment = (timezone.now() - timedelta(seconds=fail_after() + 60)).isoformat()
        VideoProject.objects.filter(pk=project.pk).update(metadata={
            **project.metadata, REQUEUES: 1,
            "ingestion_recovery_requeues_total": render_recovery.INGESTION_RECOVERY_MAX_REQUEUES_TOTAL,
            REQUEUED_AT: moment, STARTED_AT: moment,
        })
        self.age(project, fail_after() + 60)

        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("fail", "no_progress"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)


class ChargedAtClipsSelectedTests(StuckIngestionHarness, TestCase):
    """Charged at clips_selected and its worker died before the review: its creation is no longer RESERVED."""

    def charged(self, idle=None, **fields):
        project, txn = self.lost(
            stage=PipelineStage.CLIPS_SELECTED, status=VideoProject.Status.INGESTING, idle=idle, **fields,
        )
        VideoClip.objects.create(project=project, title="Clip", start_time=0, end_time=5)
        from apps.payments.services.wallet_service import commit_reservation

        commit_reservation(txn.id)
        return project, txn

    def test_it_is_requeued_twice_charged_once_and_never_refunded(self):
        project, txn = self.charged()

        row, requeue = self.sweep(project)
        self.assert_requeued_once(requeue, project)
        self.assertEqual(row["action"], "requeue")
        self.age(project)
        row, _requeue = self.sweep(project)
        self.assertEqual(row["action"], "requeue")
        self.age(project, fail_after() + 60)
        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("report", "clips_selected"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, CHARGED)

        self.run_requeued(project)
        project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual((project.status, txn.status), (VideoProject.Status.AWAITING_APPROVAL, CHARGED))
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))

    def test_an_old_auto_render_is_only_reported_never_rendered(self):
        project, _txn = self.charged(idle=2 * 24 * 3600, auto_render_bypass=True)

        row, requeue = self.sweep(project)

        requeue.assert_not_called()
        self.assertEqual((row["action"], row["reason"]), ("report", "clips_selected"))


class StuckIngestionThresholdTests(TestCase):
    def test_it_waits_longer_than_any_wait_the_pipeline_schedules(self):
        from apps.videos.tasks.shared import PIPELINE_RETRY_CAP_SECONDS

        self.assertEqual(stuck_after(), 30 * 60)
        self.assertGreaterEqual(stuck_after(), 2 * PIPELINE_RETRY_CAP_SECONDS)
        with override_settings(AI_CAPACITY_MAX_COUNTDOWN_SECONDS=1500):
            self.assertEqual(stuck_after(), 2 * 1.2 * 1500)

    def test_it_fails_only_once_no_delivery_can_still_be_alive(self):
        self.assertEqual(fail_after(), settings.PIPELINE_TIME_LIMIT + 15 * 60)
        self.assertGreater(fail_after(), settings.PIPELINE_TIME_LIMIT)


class StuckIngestionDeleteTests(StuckIngestionHarness, APITestCase):
    def test_a_delivery_that_started_recently_keeps_the_project_from_being_deleted(self):
        project, txn = self.lost(status=VideoProject.Status.INGESTING, idle=settings.RENDER_STUCK_AFTER_SECONDS + 60)
        VideoProject.objects.filter(pk=project.pk).update(
            metadata={**project.metadata, STARTED_AT: timezone.now().isoformat()},
        )
        client = APIClient()
        client.force_authenticate(user=self.user)
        client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))

        with patch(DELETE_OBJECT):
            response = client.delete(reverse("project-detail", args=[project.pk]))

        self.assertEqual((response.status_code, response.data["code"]), (409, "project_processing"))
        txn.refresh_from_db()
        self.assertEqual(txn.status, RESERVED)


# --- Two at the same time -------------------------------------------------------

class ConcurrentStuckIngestionTests(StuckIngestionHarness, APITransactionTestCase):
    """Real commits and threads: the locks let one of them act, and money moves once."""

    def together(self, *targets):
        barrier = threading.Barrier(len(targets))
        errors = []

        def run(target):
            try:
                barrier.wait(10)
                target()
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(target,)) for target in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])

    def test_two_sweeps_requeue_it_once(self):
        project, _txn = self.lost()

        with patch.object(process_initial_ingestion, "apply_async") as requeue:
            self.together(lambda: recover_stuck_renders_now(apply=True), lambda: recover_stuck_renders_now(apply=True))

        self.assert_requeued_once(requeue, project)
        project.refresh_from_db()
        self.assertEqual(project.metadata[REQUEUES], 1)

    def test_without_the_project_lock_the_row_lock_still_requeues_it_once(self):
        """Two sweeps whose Redis lock calls did not see each other: the row lock decides."""
        from contextlib import nullcontext

        project, _txn = self.lost()

        with patch.object(process_initial_ingestion, "apply_async") as requeue, \
                patch.object(render_recovery, "project_lock", side_effect=lambda *args, **kwargs: nullcontext()), \
                patch.object(render_recovery, "project_lock_held", return_value=False):
            self.together(lambda: recover_stuck_renders_now(apply=True), lambda: recover_stuck_renders_now(apply=True))

        self.assert_requeued_once(requeue, project)
        project.refresh_from_db()
        self.assertEqual(project.metadata[REQUEUES], 1)

    def test_the_fail_and_a_delete_refund_it_once(self):
        project, txn = self.lost()
        self.spend_requeues(project)
        self.age(project, settings.RENDER_STUCK_AFTER_SECONDS + fail_after())
        client = APIClient()
        client.force_authenticate(user=self.user)
        client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
        responses = []

        with patch(DELETE_OBJECT):
            self.together(
                lambda: recover_stuck_renders_now(apply=True),
                lambda: responses.append(client.delete(reverse("project-detail", args=[project.pk]))),
            )

        self.assertIn(responses[0].status_code, (204, 409))
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_the_fail_and_a_late_delivery_never_both_happen(self):
        project, txn = self.lost()
        self.spend_requeues(project)
        self.age(project, fail_after() + 60)

        self.together(lambda: recover_stuck_renders_now(apply=True), lambda: self.run_task(project))

        project.refresh_from_db()
        txn.refresh_from_db()
        if project.pipeline_error_code == "IngestionStuck":
            # The sweep won: refunded, and the delivery ran nothing.
            self.assertEqual((txn.status, project.status), (REFUNDED, VideoProject.Status.FAILED))
            self.mock_transcribe.assert_not_called()
            self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))
        else:
            # The delivery won: delivered and charged once, never refunded.
            self.assertEqual((txn.status, project.status), (CHARGED, VideoProject.Status.AWAITING_APPROVAL))
            self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))


# --- Restores a settled render left undone ---------------------------------------

class RestoreRetryTests(ClipActionsHarness, APITestCase):
    """A delivered clip rendered again whose render never ran, settled when its project failed."""

    def settled_with_a_failed_restore(self):
        project, (clip, _failed) = self.finished()
        self.post(clip_url(clip, "render"), execute=False)  # its message never ran
        mark_failed(project.pk)
        # The settle commits; the restore after it hits a database blip.
        with patch.object(ingestion, "_restore_rerendered_clips", side_effect=DatabaseError("blip")), \
                self.assertLogs("apps.videos.tasks.ingestion", level=logging.ERROR):
            ingestion._settle_clip_render_reservations(project.pk)
        self.assertEqual(self.clip_render(project).status, REFUNDED)
        clip.refresh_from_db()
        self.assertEqual(clip.status, DRAFT)
        return project, clip

    def restore_rows(self, project, apply=True):
        rows = recover_stuck_renders_now(apply=apply)
        return [row for row in rows if row["project"] == project.pk and row["kind"] == "restore"]

    def test_the_recovery_gives_the_clip_back(self):
        project, clip = self.settled_with_a_failed_restore()

        rows = self.restore_rows(project)

        self.assertEqual([(row["action"], row["result"]) for row in rows], [("restore", "restored")])
        clip.refresh_from_db()
        self.assertEqual((clip.status, clip.s3_object_key), (DONE, "videos/old-0.mp4"))
        # Nothing left to give back: not a candidate any more.
        self.assertEqual(self.restore_rows(project), [])

    def test_a_published_clip_completed_with_its_new_file_is_published_again(self):
        """A render of a published clip that went well, whose restore failed after the settle."""
        project, (clip, _failed) = self.finished()
        VideoClip.objects.filter(pk=clip.pk).update(status=VideoClip.Status.PUBLISHED)
        self.post(clip_url(clip, "render"))
        VideoClip.objects.filter(pk=clip.pk).update(status=DONE, s3_object_key="videos/new-0.mp4")
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.COMPLETED, status=VideoProject.Status.COMPLETED,
        )
        with patch.object(ingestion, "_restore_rerendered_clips", side_effect=DatabaseError("blip")), \
                self.assertLogs("apps.videos.tasks.ingestion", level=logging.ERROR):
            ingestion._settle_clip_render_reservations(project.pk)
        self.assertEqual(self.clip_render(project).status, CHARGED)

        rows = self.restore_rows(project)

        self.assertEqual(
            [(row["action"], row["result"], row["restored"]) for row in rows], [("restore", "restored", 1)],
        )
        clip.refresh_from_db()
        self.assertEqual((clip.status, clip.s3_object_key), (VideoClip.Status.PUBLISHED, "videos/new-0.mp4"))

    def test_the_dry_run_gives_nothing_back(self):
        project, clip = self.settled_with_a_failed_restore()

        rows = self.restore_rows(project, apply=False)

        self.assertEqual([(row["action"], row["result"]) for row in rows], [("restore", "dry_run")])
        clip.refresh_from_db()
        self.assertEqual(clip.status, DRAFT)

    def test_a_new_paid_render_of_the_clip_keeps_its_draft(self):
        project, clip = self.settled_with_a_failed_restore()
        # The user renders the clip again before the routine runs: paid, reserved, its draft waits for it.
        response, _delay = self.post(clip_url(clip, "render"))
        self.assertEqual(response.status_code, 202, response.content[:300])

        self.assertEqual(self.restore_rows(project), [])
        clip.refresh_from_db()
        self.assertEqual(clip.status, DRAFT)
        self.assertEqual(self.clip_render(project).status, RESERVED)

    def test_the_stale_sweep_reports_a_failed_restore_apart_from_its_settle(self):
        project, (clip, _failed) = self.finished()
        self.post(clip_url(clip, "render"), execute=False)
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.FAILED, status=VideoProject.Status.FAILED,
        )
        txn = self.clip_render(project)
        Transaction.objects.filter(pk=txn.pk).update(created_at=timezone.now() - timedelta(days=2))
        out = StringIO()

        with patch(
            "apps.videos.management.commands.settle_stale_reservations._restore_rerendered_clips",
            side_effect=DatabaseError("blip"),
        ), self.assertLogs("apps.videos.management.commands.settle_stale_reservations", level=logging.ERROR):
            call_command("settle_stale_reservations", "--apply", stdout=out)  # no CommandError: settled

        line = next(item for item in out.getvalue().splitlines() if item.startswith(f"transaction={txn.pk} "))
        fields = dict(part.split("=", 1) for part in line.split())
        self.assertEqual((fields["result"], fields["restore"]), ("rolled_back", "error=DatabaseError"))
        self.assertIn("errors=0 restore_errors=1", out.getvalue().splitlines()[-1])
        txn.refresh_from_db()
        self.assertEqual(txn.status, REFUNDED)

        # The recovery gives the clip back.
        self.restore_rows(project)
        clip.refresh_from_db()
        self.assertEqual((clip.status, clip.s3_object_key), (DONE, "videos/old-0.mp4"))

    def test_a_restore_that_goes_well_says_so(self):
        project, (clip, _failed) = self.finished()
        self.post(clip_url(clip, "render"), execute=False)
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.FAILED, status=VideoProject.Status.FAILED,
        )
        txn = self.clip_render(project)
        Transaction.objects.filter(pk=txn.pk).update(created_at=timezone.now() - timedelta(days=2))
        out = StringIO()

        call_command("settle_stale_reservations", "--apply", stdout=out)

        line = next(item for item in out.getvalue().splitlines() if item.startswith(f"transaction={txn.pk} "))
        fields = dict(part.split("=", 1) for part in line.split())
        self.assertEqual((fields["result"], fields["restore"]), ("rolled_back", "ok"))


class ConcurrentRestoreTests(ClipActionsHarness, APITransactionTestCase):
    """The routine's restore and a new paid render of the clip at the same time: the row lock orders them."""

    def test_a_render_paid_meanwhile_always_keeps_its_draft(self):
        project, (clip, _failed) = self.finished()
        with patch(CLIP_DELAY):  # its message never runs
            self.assertEqual(self.client.post(clip_url(clip, "render"), {}, format="json").status_code, 202)
        mark_failed(project.pk)
        with patch.object(ingestion, "_restore_rerendered_clips", side_effect=DatabaseError("blip")), \
                self.assertLogs("apps.videos.tasks.ingestion", level=logging.ERROR):
            ingestion._settle_clip_render_reservations(project.pk)
        barrier = threading.Barrier(2)
        responses = []

        def sweep():
            try:
                barrier.wait(10)
                recover_stuck_renders_now(apply=True)
            finally:
                connection.close()

        def render():
            client = APIClient()
            client.force_authenticate(user=self.user)
            client.credentials(HTTP_X_WORKSPACE_ID=str(self.workspace.pk))
            try:
                barrier.wait(10)
                responses.append(client.post(clip_url(clip, "render"), {}, format="json"))
            finally:
                connection.close()

        with patch(CLIP_DELAY):
            threads = [threading.Thread(target=sweep), threading.Thread(target=render)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)

        self.assertEqual([response.status_code for response in responses], [202])
        self.assertEqual(self.clip_render(project).status, RESERVED)
        self.assertEqual(VideoClip.objects.get(pk=clip.pk).status, DRAFT)  # waits for the render it paid
