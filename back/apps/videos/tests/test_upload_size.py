"""
El tope de peso de una subida (hueco 22): MAX_UPLOAD_BYTES, 2 GB por defecto.

- Un pedido cuyo Content-Length pasa el tope (más el margen del resto del
  formulario) responde 413 antes de que nadie lea el cuerpo: ni la vista ni el
  parser, ni la autenticación. Vale para cualquier ruta.
- Un archivo apenas más pesado que el tope, que entra en ese margen, responde
  el mismo 413 al validarse: antes de ffprobe, del storage y de la reserva.
- GET /pricing/ le da el tope al navegador, que revisa el archivo antes de subirlo.
"""
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.upload_limits import UPLOAD_FORM_OVERHEAD_BYTES, format_bytes
from apps.payments.models import Transaction, Wallet
from apps.videos.models import VideoProject
from apps.videos.services.media_validation import MediaInfo

User = get_user_model()

PROJECTS = reverse("project-list")
FROM_PROMPT = reverse("project-from-prompt")
PRICING = reverse("pricing")
CAP = 1000
BALANCE = Decimal("1000.00")
FIXTURE = Path(__file__).resolve().parents[4] / "front" / "src" / "api" / "contracts" / "drf" / "responses.json"


def body_for(limit):
    return {
        "error": f"El archivo pesa más de {format_bytes(limit)}, el máximo que podemos recibir.",
        "code": "file_too_large",
        "limit_bytes": limit,
    }


class Untouchable:
    """A request body that fails the test if anything reads it."""

    def __init__(self):
        self.reads = 0

    def read(self, *args, **kwargs):
        self.reads += 1
        raise AssertionError("se leyó el cuerpo de un pedido que pasa el tope")

    readline = read


class UploadSizeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pesado", email="pesado@test.com", password="pw")
        self.workspace = self.user.owned_workspaces.get()
        Wallet.objects.filter(workspace=self.workspace).update(available_balance=BALANCE, reserved_balance=0)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.media = Path(media.name)
        limits = override_settings(MEDIA_ROOT=media.name, MAX_UPLOAD_BYTES=CAP)
        limits.enable()
        self.addCleanup(limits.disable)
        probed = MediaInfo(duration_s=100.0, width=1920, height=1080, mime="video/mp4")
        validate = patch("apps.videos.services.media_validation.validate_upload", return_value=probed)
        self.validate = validate.start()
        self.addCleanup(validate.stop)
        delay = patch("apps.videos.views.process_initial_ingestion.delay")
        delay.start()
        self.addCleanup(delay.stop)

    def upload(self, size, url=PROJECTS, **extra):
        return self.client.post(url, {
            "title": "Subida", **extra,
            "source_file": SimpleUploadedFile("v.mp4", b"0" * size, content_type="video/mp4"),
        }, format="multipart")

    def assert_nothing_happened(self):
        self.assertFalse(VideoProject.objects.exists())
        self.assertEqual([path for path in self.media.rglob("*") if path.is_file()], [])
        self.assertFalse(Transaction.objects.exists())
        wallet = Wallet.objects.get(workspace=self.workspace)
        self.assertEqual((wallet.available_balance, wallet.reserved_balance), (BALANCE, Decimal("0")))

    def oversized(self, path, client=None, length=CAP + UPLOAD_FORM_OVERHEAD_BYTES + 1):
        body = Untouchable()
        response = (client or self.client).generic(
            "POST", path, b"", content_type="multipart/form-data; boundary=limite",
            CONTENT_LENGTH=str(length), **{"wsgi.input": body},
        )
        return response, body

    def test_a_body_over_the_cap_is_a_413_before_anything_reads_it(self):
        for path in (PROJECTS, FROM_PROMPT):
            response, body = self.oversized(path)

            self.assertEqual(response.status_code, 413, response.content[:300])
            self.assertEqual(response.json(), body_for(CAP))
            self.assertEqual(body.reads, 0)
        self.validate.assert_not_called()
        self.assert_nothing_happened()

    def test_any_path_and_without_a_session(self):
        """Before the authentication too: nothing is read for anyone."""
        for path in (PROJECTS, reverse("brandkit-list"), "/admin/login/"):
            response, body = self.oversized(path, client=APIClient())

            self.assertEqual(response.status_code, 413, path)
            self.assertEqual(response.json()["code"], "file_too_large")
            self.assertEqual(body.reads, 0)

    def test_a_body_within_the_room_of_the_form_is_read(self):
        """The form around the file fits in UPLOAD_FORM_OVERHEAD_BYTES: such a body reaches the view."""
        response = self.upload(CAP, selection_criteria="x" * 200)

        self.assertEqual(response.status_code, 201, response.content[:300])

    def test_a_file_just_over_the_cap_is_a_413_before_the_probe_the_storage_and_the_reservation(self):
        for url, extra in ((PROJECTS, {}), (FROM_PROMPT, {"prompt": "Sacá clips"})):
            response = self.upload(CAP + 1, url=url, **extra)

            self.assertEqual(response.status_code, 413, response.content[:300])
            self.assertEqual(response.json(), body_for(CAP))
        self.validate.assert_not_called()
        self.assert_nothing_happened()

    def test_a_file_at_the_cap_is_created(self):
        response = self.upload(CAP)

        self.assertEqual(response.status_code, 201, response.content[:300])
        self.assertTrue(Transaction.objects.filter(status=Transaction.Status.RESERVED).exists())

    def test_replacing_the_file_with_a_heavier_one_is_a_413_too(self):
        created = self.upload(CAP)
        self.assertEqual(created.status_code, 201, created.content[:300])
        project = VideoProject.objects.get(pk=created.data["id"])

        response = self.client.patch(
            reverse("project-detail", args=[project.pk]),
            {"source_file": SimpleUploadedFile("otro.mp4", b"0" * (CAP + 1), content_type="video/mp4")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 413, response.content[:300])
        self.assertEqual(VideoProject.objects.get(pk=project.pk).source_file.name, project.source_file.name)

    def test_pricing_gives_the_cap_to_the_browser(self):
        response = APIClient().get(PRICING)

        self.assertEqual(response.json()["upload_max_size_bytes"], CAP)


class DefaultCapTests(TestCase):
    def test_two_gigabytes_by_default_with_the_body_of_the_contract(self):
        gigabytes = 2 * 1024 ** 3
        response = APIClient().generic(
            "POST", PROJECTS, b"", content_type="multipart/form-data; boundary=limite",
            CONTENT_LENGTH=str(gigabytes + UPLOAD_FORM_OVERHEAD_BYTES + 1), **{"wsgi.input": Untouchable()},
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), body_for(gigabytes))
        self.assertEqual(response.json()["error"], "El archivo pesa más de 2 GB, el máximo que podemos recibir.")
        self.assertEqual(APIClient().get(PRICING).json()["upload_max_size_bytes"], gigabytes)
        if FIXTURE.exists():
            # What front/src/api/contracts/drf/responses.json says the web app gets.
            fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["errors"]["create_413_file_too_large"]
            self.assertEqual(fixture, {"status": 413, "body": response.json()})

    def test_the_size_is_written_like_the_web_app_does(self):
        self.assertEqual(format_bytes(2 * 1024 ** 3), "2 GB")
        self.assertEqual(format_bytes(1536 * 1024 ** 2), "1,5 GB")
        self.assertEqual(format_bytes(500 * 1024 ** 2), "500 MB")
        self.assertEqual(format_bytes(1000), "1000 B")
