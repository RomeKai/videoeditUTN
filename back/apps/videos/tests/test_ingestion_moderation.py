"""
AICORE-13 (#65): moderation stage in the ingestion pipeline (ADR-011 D-M2,
D-M4, D-M5, D-M7).
"""

import logging
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.security import (
    ModerationBlockedError,
    ModerationUnavailableError,
    UnsafeContentError,
)
from apps.payments.models import Transaction, Wallet
from apps.payments.services.wallet_service import reserve_funds
from apps.videos.models import ModerationOverride, VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage, StageStatus
from apps.videos.tasks import _refund_ingestion_reservation
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness

User = get_user_model()
SECRET = "texto-privado-del-transcript"


class ModerationStageHarness(IngestionHarness):
    def setUp(self):
        super().setUp()
        self.wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=self.wallet.pk).update(
            available_balance=Decimal("100.00"), reserved_balance=Decimal("0")
        )

    def make_reserved_project(self, amount="10.00", **kwargs):
        project = self.make_project(**kwargs)
        txn = reserve_funds(self.wallet.id, Decimal(amount))
        VideoProject.objects.filter(pk=project.pk).update(
            metadata={**(project.metadata or {}), "reservation_id": str(txn.id)}
        )
        project.refresh_from_db()
        return project, txn

    def assert_refunded(self, txn):
        txn.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.wallet.available_balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0"))

    def assert_still_reserved(self, txn):
        txn.refresh_from_db()
        self.assertEqual(txn.status, Transaction.Status.RESERVED)


class ModerationStageTests(ModerationStageHarness, TestCase):
    def test_moderation_runs_between_transcription_and_selection(self):
        order = []
        self.mock_moderate.side_effect = lambda text: order.append("moderate") or True
        self.mock_select.side_effect = lambda *a, **k: order.append("select") or self.mock_select.return_value
        project = self.make_project()

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(order, ["moderate", "select"])
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.pipeline_stage_status["moderated"], StageStatus.COMPLETED)
        self.assertEqual(project.metadata["moderation"]["outcome"], "passed")

    def test_payload_holds_the_user_text_and_the_transcript(self):
        project = self.make_project(metadata={"selection_criteria": "momentos de humor"})

        self.run_task(project)

        payload = self.mock_moderate.call_args.args[0]
        self.assertIn("Pipeline", payload)  # project title
        self.assertIn("momentos de humor", payload)
        self.assertIn("Hello world", payload)  # transcript

    def test_blocked_content_fails_the_project_before_selection_and_refunds(self):
        self.mock_moderate.side_effect = UnsafeContentError("blocked")
        project, txn = self.make_reserved_project()

        self.run_task(project)

        project.refresh_from_db()
        self.mock_select.assert_not_called()
        self.mock_moderate.assert_called_once()  # never retried
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_error_code, "UnsafeContentError")
        self.assertEqual(project.pipeline_stage_status["moderated"], StageStatus.FAILED)
        self.assertEqual(project.metadata["moderation"]["outcome"], "UnsafeContentError")
        self.assert_refunded(txn)

    def test_transient_unavailability_is_retried_without_refund(self):
        self.mock_moderate.side_effect = [ModerationUnavailableError(), True]
        project, txn = self.make_reserved_project()

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(self.mock_moderate.call_count, 2)
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.mock_transcribe.assert_called_once()  # the retry resumes at moderation
        self.assert_still_reserved(txn)

    def test_no_verdict_after_every_retry_fails_and_refunds(self):
        self.mock_moderate.side_effect = ModerationUnavailableError()
        project, txn = self.make_reserved_project()

        self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(self.mock_moderate.call_count, 6)  # first try + 5 retries
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, "ModerationUnavailableError")
        self.mock_select.assert_not_called()
        self.assert_refunded(txn)

    def test_misconfigured_moderation_fails_at_once_and_refunds(self):
        self.mock_moderate.side_effect = ModerationBlockedError()
        project, txn = self.make_reserved_project()

        self.run_task(project)

        project.refresh_from_db()
        self.mock_moderate.assert_called_once()
        self.assertEqual(project.pipeline_error_code, "ModerationBlockedError")
        self.assert_refunded(txn)

    def test_a_selection_retry_does_not_moderate_again(self):
        from apps.videos.services.ai.errors import AIServerError

        self.mock_select.side_effect = [AIServerError("down"), self.mock_select.return_value]
        project = self.make_project()

        self.run_task(project)

        self.mock_moderate.assert_called_once()
        self.assertEqual(self.mock_select.call_count, 2)

    def test_logs_carry_no_user_text(self):
        self.mock_transcribe.return_value.data.full_text = SECRET
        self.mock_moderate.side_effect = UnsafeContentError("blocked")
        project = self.make_project()
        VideoProject.objects.filter(pk=project.pk).update(title=SECRET)

        with self.assertLogs("apps.videos.tasks", level=logging.INFO) as logs:
            self.run_task(project)

        self.assertNotIn(SECRET, "\n".join(logs.output))


class ModerationOverrideTests(ModerationStageHarness, TestCase):
    def test_override_lets_a_blocked_project_through_without_calling_the_api(self):
        self.mock_moderate.side_effect = UnsafeContentError("blocked")
        project = self.make_project()
        self.run_task(project)
        ModerationOverride.objects.create(project=project, reason="Revisión humana: falso positivo")

        self.run_task(project)

        project.refresh_from_db()
        self.mock_moderate.assert_called_once()  # only the first, blocked run
        self.mock_transcribe.assert_called_once()  # transcript reused
        self.mock_select.assert_called_once()
        self.assertEqual(project.pipeline_stage, PipelineStage.CLIPS_SELECTED)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.metadata["moderation"]["outcome"], "overridden")


class RefundHelperTests(ModerationStageHarness, TestCase):
    def failed_project(self, **metadata):
        project = self.make_project()
        VideoProject.objects.filter(pk=project.pk).update(
            pipeline_stage=PipelineStage.FAILED, metadata=metadata
        )
        return project

    def test_refund_is_idempotent(self):
        project, txn = self.make_reserved_project()
        VideoProject.objects.filter(pk=project.pk).update(pipeline_stage=PipelineStage.FAILED)

        _refund_ingestion_reservation(project.id)
        _refund_ingestion_reservation(project.id)

        self.assert_refunded(txn)

    def test_project_without_reservation_is_skipped(self):
        project = self.failed_project()
        _refund_ingestion_reservation(project.id)  # no error

    def test_project_that_is_not_failed_is_never_refunded(self):
        project, txn = self.make_reserved_project()
        _refund_ingestion_reservation(project.id)
        self.assert_still_reserved(txn)

    def test_unknown_reservation_never_raises(self):
        project = self.failed_project(reservation_id="00000000-0000-0000-0000-000000000000")
        _refund_ingestion_reservation(project.id)


class ModerationOverrideAdminTests(TestCase):
    def setUp(self):
        from apps.users.models import Workspace

        self.admin = User.objects.create_superuser(
            username="admin", email="admin@test.com", password="pw"
        )
        workspace = Workspace.objects.create(name="W", owner=self.admin)
        self.project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=self.admin, title="P",
            pipeline_stage=PipelineStage.FAILED,
        )
        self.client.force_login(self.admin)
        self.url = reverse("admin:videos_moderationoverride_add")

    def post(self):
        return self.client.post(self.url, {"project": str(self.project.pk), "reason": "Falso positivo"})

    def test_saving_records_the_admin_and_requeues_a_failed_project(self):
        with patch("apps.videos.tasks.process_initial_ingestion.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post()

        self.assertEqual(response.status_code, 302)
        override = ModerationOverride.objects.get()
        self.assertEqual(override.created_by, self.admin)
        self.assertEqual(override.reason, "Falso positivo")
        delay.assert_called_once_with(str(self.project.pk))

    def test_a_project_that_is_not_failed_is_not_requeued(self):
        VideoProject.objects.filter(pk=self.project.pk).update(
            pipeline_stage=PipelineStage.CLIPS_SELECTED
        )
        with patch("apps.videos.tasks.process_initial_ingestion.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.post()
        delay.assert_not_called()

    def test_overrides_cannot_be_edited_or_deleted(self):
        override = ModerationOverride.objects.create(project=self.project, reason="r")
        change_url = reverse("admin:videos_moderationoverride_change", args=[override.pk])
        delete_url = reverse("admin:videos_moderationoverride_delete", args=[override.pk])

        self.client.post(change_url, {"project": str(self.project.pk), "reason": "otro"})
        override.refresh_from_db()
        self.assertEqual(override.reason, "r")
        self.assertEqual(self.client.get(delete_url).status_code, 403)


@override_settings(AI_CORE_V2_ENABLED=True)
class CreateViewReservationLinkTests(TestCase):
    def test_create_links_the_reservation_to_the_project(self):
        from apps.users.models import Workspace

        user = User.objects.create_user(username="u", email="u@test.com", password="pw")
        if not user.workspaces.exists():
            Workspace.objects.create(name="W", owner=user).members.add(user)
        # The view picks user.workspaces.first() (unordered): fund every one.
        for workspace in user.workspaces.all():
            wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
            Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("100.00"))
        client = APIClient()
        client.force_authenticate(user=user)

        with patch("apps.videos.views.process_initial_ingestion.delay"):
            response = client.post(
                reverse("project-list"),
                {"title": "Remoto", "video_url": "https://www.youtube.com/watch?v=x"},
                format="json",
            )

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        txn = Transaction.objects.get(pk=project.metadata["reservation_id"])
        self.assertEqual(txn.status, Transaction.Status.RESERVED)
