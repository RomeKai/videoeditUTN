"""
Los campos numéricos crudos de la creación (`max_clips`,
`subtitle_scale_factor`) y los de columna que el create leía crudos
(`manual_camera_x`, `manual_camera_y`, `manual_camera_zoom`,
`subtitle_words_per_segment`): vacíos cuentan como no enviados, un valor que
no es un número es un 400 en el campo y no un 500, y una creación que falla
después del INSERT no deja la fila.
"""

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Transaction, Wallet
from apps.videos.models import VideoProject
from apps.videos.services.media_validation import MediaInfo

User = get_user_model()

COLUMNS = ("manual_camera_x", "manual_camera_y", "manual_camera_zoom", "subtitle_words_per_segment")
RAW_ONLY = ("max_clips", "subtitle_scale_factor")
FROM_PROMPT = "/api/v1/projects/from-prompt/"
LINK = "https://www.youtube.com/watch?v=x"


class RawNumbersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nums", email="nums@test.com", password="pw")
        self.wallet = Wallet.objects.get(workspace=self.user.owned_workspaces.get())
        Wallet.objects.filter(pk=self.wallet.pk).update(available_balance=Decimal("1000.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def upload(self, url=None, **extra):
        probed = MediaInfo(duration_s=10.0, width=1920, height=1080, mime="video/mp4")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("apps.videos.services.media_validation.validate_upload", return_value=probed), \
                patch("apps.videos.views.process_initial_ingestion.delay"):
            return self.client.post(url or reverse("project-list"), {
                "title": "Números", **extra,
                "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
            }, format="multipart")

    def link(self, **extra):
        with patch("apps.videos.views.process_initial_ingestion.delay"):
            return self.client.post(reverse("project-list"), {"title": "Link", "video_url": LINK, **extra}, format="json")

    def created(self, response):
        self.assertEqual(response.status_code, 201, response.content[:300])
        return VideoProject.objects.get(pk=response.data["id"])

    def assert_nothing_created(self):
        self.assertFalse(VideoProject.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.available_balance, self.wallet.reserved_balance), (Decimal("1000"), Decimal("0")))

    def test_an_empty_number_counts_as_not_sent(self):
        defaults = {name: VideoProject._meta.get_field(name).default for name in COLUMNS}
        for name in (*COLUMNS, *RAW_ONLY):
            with self.subTest(name):
                project = self.created(self.upload(**{name: ""}))
                self.assertEqual({column: getattr(project, column) for column in COLUMNS}, defaults)
                self.assertNotIn("max_clips", project.metadata)

    def test_an_empty_number_is_not_explicit_for_prompt_to_edit(self):
        project = self.created(self.upload(url=FROM_PROMPT, prompt="tres clips", max_clips="", subtitle_words_per_segment=""))
        self.assertNotIn("max_clips", project.metadata["pte_explicit"])
        self.assertNotIn("subtitle_words_per_segment", project.metadata["pte_explicit"])

    def test_numbers_that_are_sent_are_stored(self):
        project = self.created(self.upload(
            max_clips="4", manual_camera_x="0.3", manual_camera_y="0.6", manual_camera_zoom="1.5",
            subtitle_words_per_segment="3.0", subtitle_scale_factor="1.2",
        ))
        self.assertEqual(project.metadata["max_clips"], 4)
        self.assertEqual((project.manual_camera_x, project.manual_camera_y, project.manual_camera_zoom), (0.3, 0.6, 1.5))
        self.assertEqual(project.subtitle_words_per_segment, 3)

        project = self.created(self.link(max_clips=2, manual_camera_x=0.25, subtitle_words_per_segment=5))
        self.assertEqual((project.metadata["max_clips"], project.manual_camera_x, project.subtitle_words_per_segment), (2, 0.25, 5))

    def test_a_value_that_is_not_a_number_is_400_and_nothing_is_created(self):
        for name in (*COLUMNS, *RAW_ONLY):
            for value in ("muchos", "nan", "inf"):
                for send in (self.upload, self.link):
                    with self.subTest(name=name, value=value, send=send.__name__):
                        response = send(**{name: value})
                        self.assertEqual(response.status_code, 400, response.content[:300])
                        self.assertIn(name, response.data)
                        self.assert_nothing_created()
        response = self.link(max_clips=True)
        self.assertEqual(response.status_code, 400, response.content[:300])
        self.assertEqual(response.data, {"max_clips": ["Tiene que ser un número entero."]})

    def test_a_failure_after_the_insert_leaves_no_project(self):
        with patch.object(MediaInfo, "as_metadata", side_effect=RuntimeError("boom")):
            response = self.upload()

        self.assertEqual(response.status_code, 500)
        self.assert_nothing_created()
