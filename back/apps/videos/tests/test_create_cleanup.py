"""
Una creación que falla no deja nada atrás, y una que no se puede encolar no
deja tokens reservados:

- El archivo subido se guarda en el storage antes del INSERT. Si la creación
  falla después (el INSERT, el resto del create del serializer, el precio,
  el plan, la reserva), se borra con la fila. Con USE_R2 también: en el
  request el archivo solo está en el storage de Django.
- La reserva y su vínculo (metadata.reservation_id) son una transacción, y la
  ingesta se encola cuando esa transacción hace commit.
- Si el broker no toma el mensaje, nadie levantaría el proyecto: falla con
  pipeline_error_code=IngestionNotQueued, se devuelve la reserva y el pedido
  responde 503.
"""

import logging
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.videos.models import VideoProject
from apps.videos.services.ai.pipeline_state import PipelineStage
from apps.videos.services.media_validation import MediaInfo

User = get_user_model()

PROJECTS = reverse("project-list")
FROM_PROMPT = reverse("project-from-prompt")
LINK = "https://www.youtube.com/watch?v=x"
DELAY = "apps.videos.views.process_initial_ingestion.delay"
BALANCE = Decimal("1000.00")
# The code front/src/lib/error-messages.ts translates (views.INGESTION_NOT_QUEUED).
INGESTION_NOT_QUEUED = "IngestionNotQueued"
PROJECT_TABLE = VideoProject._meta.db_table


class CreationHarness:
    """A funded personal workspace, a private MEDIA_ROOT and a probed 100 s upload."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="limpio", email="limpio@test.com", password="pw")
        self.workspace = self.user.owned_workspaces.get()
        self.wallet = Wallet.objects.get(workspace=self.workspace)
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=BALANCE, reserved_balance=0)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        media_root = override_settings(MEDIA_ROOT=media.name)
        media_root.enable()
        self.addCleanup(media_root.disable)
        probed = MediaInfo(duration_s=100.0, width=1920, height=1080, mime="video/mp4")
        validate = patch("apps.videos.services.media_validation.validate_upload", return_value=probed)
        validate.start()
        self.addCleanup(validate.stop)

    def upload(self, url=PROJECTS, **extra):
        return self.client.post(url, {
            "title": "Subida", **extra,
            "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
        }, format="multipart")

    def stored(self):
        return sorted(path for path in self.media.rglob("*") if path.is_file())

    def balances(self):
        self.wallet.refresh_from_db()
        return self.wallet.available_balance, self.wallet.reserved_balance

    def assert_nothing_left(self, response, status_code):
        self.assertEqual(response.status_code, status_code, response.content[:300])
        self.assertEqual(self.stored(), [])
        self.assertFalse(VideoProject.objects.exists())
        self.assertFalse(Transaction.objects.filter(status=Transaction.Status.RESERVED).exists())
        self.assertEqual(self.balances(), (BALANCE, Decimal("0")))


class UploadCleanupTests(CreationHarness, TestCase):
    def setUp(self):
        super().setUp()
        delay = patch(DELAY)
        self.delay = delay.start()
        self.addCleanup(delay.stop)

    def set_plan(self, **limits):
        name = f"Plan de prueba {SubscriptionPlan.objects.count()}"
        self.workspace.subscription_plan = SubscriptionPlan.objects.create(name=name, **limits)
        self.workspace.save(update_fields=["subscription_plan"])

    def test_a_created_project_keeps_its_upload(self):
        response = self.upload()

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(self.stored(), [Path(project.source_file.path)])

    def test_a_failure_after_the_insert_deletes_the_upload(self):
        with patch.object(MediaInfo, "as_metadata", side_effect=RuntimeError("boom")):
            response = self.upload()

        self.assert_nothing_left(response, 500)

    def test_a_failed_insert_deletes_the_upload(self):
        def fail_the_insert(execute, sql, params, many, context):
            if sql.startswith(f'INSERT INTO "{PROJECT_TABLE}"'):
                raise DatabaseError("insert failed")
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_the_insert):
            response = self.upload()

        self.assert_nothing_left(response, 500)

    def test_a_plan_limit_deletes_the_upload(self):
        self.set_plan(max_video_duration_seconds=60)

        self.assert_nothing_left(self.upload(), 403)

    def test_a_feature_the_plan_does_not_include_deletes_the_upload(self):
        self.set_plan(max_video_duration_seconds=600, has_seo_optimization=False)

        self.assert_nothing_left(self.upload(use_seo="true"), 403)

    def test_a_pricing_error_deletes_the_upload(self):
        with patch(
            "apps.payments.services.pricing_engine.PricingEngine.calculate_render_cost",
            side_effect=RuntimeError("boom"),
        ):
            response = self.upload()

        self.assert_nothing_left(response, 400)

    def test_insufficient_funds_deletes_the_upload(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("1.00"))

        response = self.upload()

        self.assertEqual(response.status_code, 402, response.content[:300])
        self.assertEqual(self.stored(), [])
        self.assertFalse(VideoProject.objects.exists())

    def test_a_reservation_error_deletes_the_upload(self):
        with patch("apps.payments.services.wallet_service.reserve_funds", side_effect=RuntimeError("boom")):
            response = self.upload()

        self.assert_nothing_left(response, 500)

    def test_a_reservation_that_cannot_be_linked_is_undone(self):
        """The link to the project is in the reservation's transaction: never one without the other."""
        def fail_the_link(execute, sql, params, many, context):
            # The metadata travels as a Jsonb adapter: its .obj is the dict.
            values = [str(getattr(value, "obj", value)) for value in params or ()]
            if sql.startswith(f'UPDATE "{PROJECT_TABLE}"') and any("reservation_id" in v for v in values):
                raise DatabaseError("update failed")
            return execute(sql, params, many, context)

        with connection.execute_wrapper(fail_the_link):
            response = self.upload()

        self.assert_nothing_left(response, 500)
        self.assertFalse(Transaction.objects.exists())
        self.delay.assert_not_called()

    def test_from_prompt_deletes_the_upload_too(self):
        self.set_plan(max_video_duration_seconds=60)

        self.assert_nothing_left(self.upload(url=FROM_PROMPT, prompt="tres clips de humor"), 403)

    def test_a_link_refused_after_the_insert_leaves_no_project(self):
        response = self.client.post(PROJECTS, {"title": "Link", "video_url": LINK, "render_resolution": "1080p"}, format="json")

        self.assert_nothing_left(response, 400)

    @override_settings(USE_R2=True)
    def test_with_r2_the_upload_is_deleted_from_django_storage(self):
        """With USE_R2 the request only writes Django's storage: R2 gets the file in the ingestion."""
        self.set_plan(max_video_duration_seconds=60)

        with patch("apps.videos.services.storage_service._get_r2_client") as r2:
            self.assert_nothing_left(self.upload(), 403)
            self.set_plan(max_video_duration_seconds=600, has_seo_optimization=False)
            self.assert_nothing_left(self.upload(use_seo="true"), 403)

        r2.assert_not_called()

    def test_an_upload_that_cannot_be_deleted_is_logged_and_the_refusal_stands(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("1.00"))
        storage = VideoProject._meta.get_field("source_file").storage

        with patch.object(storage, "delete", side_effect=OSError("disk")), \
                self.assertLogs("apps.videos.services.storage_service", level=logging.WARNING) as logs:
            response = self.upload()

        self.assertEqual(response.status_code, 402, response.content[:300])
        self.assertFalse(VideoProject.objects.exists())
        self.assertIn("upload.cleanup_failed", logs.output[0])
        self.assertIn("error=OSError", logs.output[0])
        self.assertNotIn("disk", logs.output[0])


class QueueAfterCommitTests(CreationHarness, TestCase):
    def test_the_ingestion_is_queued_once_the_reservation_commits(self):
        with patch(DELAY) as delay:
            with self.captureOnCommitCallbacks() as callbacks:
                response = self.upload()
            self.assertEqual(response.status_code, 201, response.content[:300])
            # Still inside the request's transaction: nothing was sent yet.
            delay.assert_not_called()

            for callback in callbacks:
                callback()

        project = VideoProject.objects.get(pk=response.data["id"])
        delay.assert_called_once_with(project.id)
        self.assertEqual(
            Transaction.objects.get(pk=project.metadata["reservation_id"]).status, Transaction.Status.RESERVED,
        )

    def test_inside_an_outer_transaction_a_broker_failure_still_refunds(self):
        """With ATOMIC_REQUESTS the callback runs after the response: the project is failed and refunded all the same."""
        with patch(DELAY, side_effect=OperationalError("broker down")):
            with self.captureOnCommitCallbacks(execute=True):
                self.upload()

        project = VideoProject.objects.get()
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, INGESTION_NOT_QUEUED)
        self.assertEqual(Transaction.objects.get().status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (BALANCE, Decimal("0")))


class BrokerDownTests(CreationHarness, TransactionTestCase):
    """TransactionTestCase: the reservation really commits, as in a request."""

    def assert_failed_and_refunded(self, response):
        self.assertEqual(response.status_code, 503, response.content[:300])
        self.assertEqual(response.data["code"], INGESTION_NOT_QUEUED)
        self.assertNotIn("prompt_config", response.data)
        project = VideoProject.objects.get(pk=response.data["project_id"])
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_stage, PipelineStage.FAILED)
        self.assertEqual(project.pipeline_error_code, INGESTION_NOT_QUEUED)
        txn = Transaction.objects.get(pk=project.metadata["reservation_id"])
        self.assertEqual(txn.status, Transaction.Status.FAILED)
        self.assertEqual(self.balances(), (BALANCE, Decimal("0")))
        return project

    def test_an_upload_the_broker_does_not_take_fails_and_is_refunded(self):
        with patch(DELAY, side_effect=OperationalError("broker down")) as delay, \
                self.assertLogs("apps.videos.views", level=logging.ERROR) as logs:
            response = self.upload()

        project = self.assert_failed_and_refunded(response)
        delay.assert_called_once_with(project.id)
        # The project stays, failed, with its file: it is not an orphan.
        self.assertEqual(self.stored(), [Path(project.source_file.path)])
        self.assertIn(f"project.ingestion_not_queued project_id={project.id} error=OperationalError", logs.output[0])
        self.assertNotIn("broker down", "".join(logs.output))

    def test_from_prompt_fails_and_is_refunded_too(self):
        with patch(DELAY, side_effect=OperationalError("broker down")):
            response = self.client.post(FROM_PROMPT, {"title": "Prompt", "video_url": LINK, "prompt": "tres clips"}, format="json")

        self.assert_failed_and_refunded(response)

    def test_a_link_fails_and_is_refunded_too(self):
        with patch(DELAY, side_effect=OperationalError("broker down")):
            response = self.client.post(PROJECTS, {"title": "Link", "video_url": LINK}, format="json")

        self.assert_failed_and_refunded(response)

    def test_the_ingestion_is_sent_after_the_commit(self):
        seen = {}

        def delay(project_id):
            # What a worker would read: committed rows, no open transaction.
            seen["in_transaction"] = connection.in_atomic_block
            project = VideoProject.objects.get(pk=project_id)
            seen["reservation"] = Transaction.objects.get(pk=project.metadata["reservation_id"]).status

        with patch(DELAY, side_effect=delay):
            response = self.upload()

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertEqual(seen, {"in_transaction": False, "reservation": Transaction.Status.RESERVED})

    def test_a_worker_that_got_the_message_after_all_keeps_the_project(self):
        """The broker raised, but the message went through and a worker moved the project on."""
        def delay(project_id):
            VideoProject.objects.filter(pk=project_id).update(pipeline_stage=PipelineStage.AUDIO_EXTRACTED)
            raise OperationalError("broker down")

        with patch(DELAY, side_effect=delay):
            response = self.upload()

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.pipeline_stage, PipelineStage.AUDIO_EXTRACTED)
        self.assertEqual(project.pipeline_error_code, "")
        self.assertEqual(
            Transaction.objects.get(pk=project.metadata["reservation_id"]).status, Transaction.Status.RESERVED,
        )
