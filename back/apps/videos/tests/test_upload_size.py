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
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.upload_limits import (
    BRAND_KIT_FILE_MAX_BYTES, UPLOAD_FORM_OVERHEAD_BYTES, UploadSizeLimitMiddleware, brand_kit_max_bytes,
    format_bytes, max_request_bytes,
)
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

    def oversized(self, path, client=None, length=CAP + UPLOAD_FORM_OVERHEAD_BYTES + 1, method="POST"):
        body = Untouchable()
        response = (client or self.client).generic(
            method, path, b"", content_type="multipart/form-data; boundary=limite",
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

    def test_before_the_session_too_and_logged_as_info(self):
        """Nothing is read for anyone; anyone can send the header, so it is not a warning."""
        with self.assertLogs("apps.core.upload_limits", level="INFO") as logs:
            response, body = self.oversized(PROJECTS, client=APIClient())

        self.assertEqual(response.status_code, 413)
        self.assertEqual(body.reads, 0)
        self.assertEqual([record.levelname for record in logs.records], ["INFO"])
        self.assertIn("code=file_too_large", logs.output[0])

    def test_the_cap_of_the_video_is_only_for_its_routes(self):
        """Any other request may weigh MAX_REQUEST_BYTES: a 413 `request_too_large` over it, before it is read."""
        over = max_request_bytes() + 1
        project = VideoProject.objects.create(workspace=self.workspace, uploaded_by=self.user, title="Otro")
        for method, path in (
            ("POST", "/admin/login/"),  # reads request.POST before any session (the CSRF check)
            ("POST", reverse("project-probe-link")),
            ("POST", reverse("project-approve-paper-edit", args=[project.pk])),
            ("POST", "/api/token/"),
            ("GET", reverse("project-detail", args=[project.pk])),
        ):
            response, body = self.oversized(path, client=APIClient(), length=over, method=method)

            self.assertEqual(response.status_code, 413, path)
            self.assertEqual(response.json(), {
                "error": f"El pedido pesa más de {format_bytes(max_request_bytes())}, el máximo que podemos recibir.",
                "code": "request_too_large",
                "limit_bytes": max_request_bytes(),
            })
            self.assertEqual(body.reads, 0)
        # The PATCH that replaces the video is a route of the video.
        response, body = self.oversized(reverse("project-detail", args=[project.pk]), method="PATCH")
        self.assertEqual((response.status_code, response.json()["code"]), (413, "file_too_large"))

    def test_a_brand_kit_may_weigh_the_sum_of_its_files(self):
        limit = brand_kit_max_bytes() + UPLOAD_FORM_OVERHEAD_BYTES
        response, body = self.oversized(reverse("brandkit-list"), length=limit + 1)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {
            "error": "Los archivos pesan más de 112 MB, el máximo que podemos recibir.",
            "code": "file_too_large",
            "limit_bytes": 112 * 1024 ** 2,
        })
        self.assertEqual(body.reads, 0)

    def test_each_file_of_a_brand_kit_has_its_cap(self):
        import io

        from PIL import Image

        png = io.BytesIO()
        Image.new("RGB", (1, 1)).save(png, format="PNG")
        contents = {
            "primary_font": b"0" * 10, "secondary_font": b"0" * 10, "watermark_logo": png.getvalue(),
            "intro_video": b"0" * 30, "outro_video": b"0" * 30,
        }
        names = {"primary_font": "f.ttf", "secondary_font": "g.otf", "watermark_logo": "l.png", "intro_video": "i.mp4", "outro_video": "o.mp4"}
        texts = {"primary_font": "La fuente", "secondary_font": "La fuente", "watermark_logo": "El logo", "intro_video": "El video", "outro_video": "El video"}
        for field, content in contents.items():
            cap = len(content)
            with patch.dict(BRAND_KIT_FILE_MAX_BYTES, {field: cap - 1}):
                refused = self.client.post(reverse("brandkit-list"), {
                    "name": f"Marca {field}", field: SimpleUploadedFile(names[field], content),
                }, format="multipart")
            self.assertEqual(refused.status_code, 400, field)
            self.assertEqual(refused.json(), {field: [f"{texts[field]} pesa más de {cap - 1} B."]})

            with patch.dict(BRAND_KIT_FILE_MAX_BYTES, {field: cap}):
                created = self.client.post(reverse("brandkit-list"), {
                    "name": f"Marca {field}", field: SimpleUploadedFile(names[field], content),
                }, format="multipart")
            self.assertEqual(created.status_code, 201, created.content[:300])

    def test_the_caps_of_a_brand_kit_are_the_ones_of_the_web_app(self):
        self.assertEqual(BRAND_KIT_FILE_MAX_BYTES, {
            "primary_font": 5 * 1024 ** 2, "secondary_font": 5 * 1024 ** 2, "watermark_logo": 2 * 1024 ** 2,
            "intro_video": 50 * 1024 ** 2, "outro_video": 50 * 1024 ** 2,
        })

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


class BoundaryTests(TestCase):
    """At the limit of its route a body goes on; one byte more is a 413 (``>``, not ``>=``)."""

    def answer(self, method, path, length):
        request = getattr(RequestFactory(), method.lower())(path, CONTENT_LENGTH=str(length))
        request.META["CONTENT_LENGTH"] = str(length)
        return UploadSizeLimitMiddleware(lambda request: HttpResponse("ok"))(request).status_code

    @override_settings(MAX_UPLOAD_BYTES=3000, MAX_REQUEST_BYTES=2000)
    def test_each_route_at_its_limit_and_one_byte_over(self):
        for method, path, limit in (
            ("POST", PROJECTS, 3000 + UPLOAD_FORM_OVERHEAD_BYTES),
            ("POST", FROM_PROMPT, 3000 + UPLOAD_FORM_OVERHEAD_BYTES),
            ("PATCH", "/api/v1/projects/7d7f7b1e-1111-4a4a-8888-000000000000/", 3000 + UPLOAD_FORM_OVERHEAD_BYTES),
            ("POST", reverse("brandkit-list"), brand_kit_max_bytes() + UPLOAD_FORM_OVERHEAD_BYTES),
            ("POST", "/admin/login/", 2000),
            ("POST", "/no-existe/", 2000),
        ):
            self.assertEqual(self.answer(method, path, limit), 200, path)
            self.assertEqual(self.answer(method, path, limit + 1), 413, path)
