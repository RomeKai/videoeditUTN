"""
settle_stale_reservations: token reservations left RESERVED (from before
charging existed, a settle that failed on a database error, a render whose
worker died) are reported and, with --apply, settled by the state of their
project, with the same rules the pipeline applies.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from apps.payments.models import Transaction, Wallet
from apps.payments.services.wallet_service import reserve_funds
from apps.users.models import Workspace
from apps.videos.management.commands import settle_stale_reservations as command
from apps.videos.models import VideoClip, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage

User = get_user_model()

TITLE = "SECRET_PROJECT_TITLE"


class SettleStaleReservationsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sweep", email="sweep@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="Sweep", owner=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=self.wallet.pk).update(
            available_balance=Decimal("100.00"), reserved_balance=Decimal("0"),
        )

    # -- helpers --

    def reserve(self, amount="10.00", hours_ago=30):
        txn = reserve_funds(self.wallet.id, Decimal(amount), description=f"Procesamiento: {TITLE}")
        Transaction.objects.filter(pk=txn.pk).update(
            created_at=timezone.now() - timedelta(hours=hours_ago),
        )
        return txn

    def project(self, link=None, txn=None, **fields):
        metadata = {link: str(txn.id)} if link else {}
        return VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=self.user, title=TITLE, metadata=metadata, **fields,
        )

    def creation(self, **fields):
        txn = self.reserve()
        return txn, self.project("reservation_id", txn, **fields)

    def render(self, **fields):
        txn = self.reserve()
        return txn, self.project("render_reservation_id", txn, **fields)

    def sweep(self, *args):
        out = StringIO()
        call_command("settle_stale_reservations", *args, stdout=out)
        return out.getvalue()

    @staticmethod
    def line_for(output, txn):
        line = next(item for item in output.splitlines() if item.startswith(f"transaction={txn.id} "))
        return dict(part.split("=", 1) for part in line.split())

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def status(self, txn):
        txn.refresh_from_db()
        return txn.status

    # -- dry run --

    def test_by_default_it_only_reports(self):
        txn, _ = self.creation(pipeline_stage=PipelineStage.CLIPS_SELECTED)

        line = self.line_for(self.sweep(), txn)

        self.assertEqual((line["action"], line["result"]), ("commit", "dry_run"))
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("10.00")))

    # -- creation reservation --

    def test_a_project_with_its_clips_selected_is_charged(self):
        for stage in (
            PipelineStage.CLIPS_SELECTED, PipelineStage.RENDER_DISPATCHED,
            PipelineStage.COMPLETED, PipelineStage.PARTIAL,
        ):
            with self.subTest(stage=stage):
                txn, _ = self.creation(pipeline_stage=stage)

                line = self.line_for(self.sweep("--apply"), txn)

                self.assertEqual((line["action"], line["result"]), ("commit", "committed"))
                self.assertEqual(self.status(txn), Transaction.Status.COMPLETED)

        self.assertEqual(self.balances(), (Decimal("60.00"), Decimal("0")))

    def test_a_failed_project_that_has_clips_is_charged(self):
        txn, project = self.creation(pipeline_stage=PipelineStage.FAILED)
        VideoClip.objects.create(project=project, title="c", start_time=0, end_time=5)

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["reason"]), ("commit", "has_clips"))
        self.assertEqual(self.status(txn), Transaction.Status.COMPLETED)

    def test_a_failed_project_without_clips_is_refunded(self):
        txn, _ = self.creation(pipeline_stage=PipelineStage.FAILED)

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["result"]), ("rollback", "rolled_back"))
        self.assertEqual(self.status(txn), Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_project_still_ingesting_is_only_reported(self):
        txn, _ = self.creation(pipeline_stage=PipelineStage.TRANSCRIBED)

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["reason"]), ("report", "in_progress"))
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)

    # -- render reservation --

    def test_a_delivered_render_is_charged(self):
        txn, _ = self.render(status=VideoProject.Status.COMPLETED, final_export_r2_key="final/key.mp4")

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["kind"], line["action"]), ("render", "commit"))
        self.assertEqual(self.status(txn), Transaction.Status.COMPLETED)

    def test_a_render_with_its_export_saved_is_charged_even_if_marked_failed(self):
        txn, _ = self.render(status=VideoProject.Status.FAILED, final_export_r2_key="final/key.mp4")

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["reason"]), ("commit", "render_delivered"))
        self.assertEqual(self.status(txn), Transaction.Status.COMPLETED)

    def test_a_completed_project_without_its_export_is_not_charged_for_the_render(self):
        txn, _ = self.render(status=VideoProject.Status.COMPLETED)

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual(line["action"], "report")
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)

    def test_a_failed_render_is_refunded(self):
        txn, _ = self.render(status=VideoProject.Status.FAILED)

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["result"]), ("rollback", "rolled_back"))
        self.assertEqual(self.status(txn), Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (Decimal("100.00"), Decimal("0")))

    def test_a_render_still_running_is_only_reported(self):
        txn, _ = self.render(status=VideoProject.Status.RENDERING)

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["reason"]), ("report", "render_in_progress"))
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)

    # -- scope and safety --

    def test_a_reservation_without_a_project_is_only_reported(self):
        txn = self.reserve()

        line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["kind"], line["project"], line["action"]), ("-", "-", "report"))
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)

    def test_recent_reservations_are_left_alone(self):
        txn = self.reserve(hours_ago=2)
        self.project("reservation_id", txn, pipeline_stage=PipelineStage.FAILED)

        self.assertNotIn(str(txn.id), self.sweep("--apply"))
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)

        line = self.line_for(self.sweep("--apply", "--min-age-hours", "1"), txn)
        self.assertEqual(line["result"], "rolled_back")

    def test_settled_transactions_are_not_listed(self):
        txn, _ = self.creation(pipeline_stage=PipelineStage.CLIPS_SELECTED)
        self.sweep("--apply")

        self.assertNotIn(str(txn.id), self.sweep("--apply"))

    def test_the_decision_is_taken_again_before_settling(self):
        txn, project = self.creation(pipeline_stage=PipelineStage.FAILED)
        real_decide = command.decide
        calls = []

        def redispatched_after_the_report(kind, values):
            calls.append(values["pipeline_stage"])
            if len(calls) == 1:
                # Someone re-dispatches the failed project before it is settled.
                VideoProject.objects.filter(pk=project.pk).update(
                    pipeline_stage=PipelineStage.AUDIO_EXTRACTED,
                )
            return real_decide(kind, values)

        with patch.object(command, "decide", side_effect=redispatched_after_the_report):
            line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual(calls, [PipelineStage.FAILED, PipelineStage.AUDIO_EXTRACTED])
        self.assertEqual((line["action"], line["result"]), ("report", "skipped"))
        self.assertEqual(self.status(txn), Transaction.Status.RESERVED)

    def test_a_reservation_the_pipeline_settled_meanwhile_is_skipped(self):
        from apps.payments.services.wallet_service import commit_reservation

        txn, _ = self.creation(pipeline_stage=PipelineStage.CLIPS_SELECTED)
        real_decide = command.decide
        calls = []

        def pipeline_charges_first(kind, values):
            calls.append(kind)
            if len(calls) == 2:
                # The pipeline charges it between the report and the settle.
                commit_reservation(txn.id)
            return real_decide(kind, values)

        with patch.object(command, "decide", side_effect=pipeline_charges_first):
            line = self.line_for(self.sweep("--apply"), txn)

        self.assertEqual((line["action"], line["result"]), ("commit", "already_settled"))
        self.assertEqual(self.status(txn), Transaction.Status.COMPLETED)
        self.assertEqual(self.balances(), (Decimal("90.00"), Decimal("0")))  # charged once

    def test_one_failure_never_stops_the_sweep_but_fails_the_command(self):
        first, _ = self.creation(pipeline_stage=PipelineStage.CLIPS_SELECTED)
        second, _ = self.creation(pipeline_stage=PipelineStage.CLIPS_SELECTED)
        from apps.payments.services.wallet_service import commit_reservation as real_commit

        def first_fails(transaction_id):
            if str(transaction_id) == str(first.id):
                raise DatabaseError("connection lost")
            return real_commit(transaction_id)

        out = StringIO()
        with patch("apps.payments.services.wallet_service.commit_reservation", side_effect=first_fails), \
                self.assertRaises(CommandError):
            call_command("settle_stale_reservations", "--apply", stdout=out)

        self.assertEqual(self.line_for(out.getvalue(), first)["result"], "error=DatabaseError")
        self.assertEqual(self.status(first), Transaction.Status.RESERVED)
        self.assertEqual(self.status(second), Transaction.Status.COMPLETED)

    def test_the_output_has_one_line_per_transaction_and_no_user_text(self):
        self.creation(pipeline_stage=PipelineStage.CLIPS_SELECTED)
        self.render(status=VideoProject.Status.RENDERING)
        self.reserve()

        output = self.sweep()

        self.assertNotIn(TITLE, output)
        lines = output.splitlines()
        self.assertEqual(len(lines), 4)  # three transactions and the summary
        self.assertTrue(lines[-1].startswith("summary mode=dry_run"))
        self.assertIn("commit=1 rollback=0 report=2 errors=0", lines[-1])

    def test_a_negative_age_is_rejected(self):
        with self.assertRaises(CommandError):
            self.sweep("--min-age-hours", "-1")

    def test_new_transactions_record_when_they_were_made(self):
        before = timezone.now()
        txn = reserve_funds(self.wallet.id, Decimal("1.00"))

        self.assertGreaterEqual(Transaction.objects.get(pk=txn.pk).created_at, before)
