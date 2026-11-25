"""
Un booleano del proyecto que un formulario multipart no manda queda como
estaba. DRF lee un booleano ausente como falso (un checkbox sin marcar), así
que un PUT multipart sin `speaker_tracking`, `auto_render_bypass`,
`use_facetracking`, `remove_silences` o `subtitle_with_emojis` los apagaba.
Al crear, un booleano vacío ("") cuenta como no enviado: queda el default del
modelo y no es un campo explícito para Prompt-to-Edit.
"""

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import serializers
from rest_framework.test import APIClient

from apps.payments.models import Wallet
from apps.videos.models import VideoProject
from apps.videos.serializers import VideoProjectSerializer, _NotSentBooleanField
from apps.videos.services.ai.prompt_validator import explicit_config_fields
from apps.videos.services.media_validation import MediaInfo

User = get_user_model()

MODEL_BOOLEANS = sorted(
    field.name for field in VideoProject._meta.get_fields() if isinstance(field, models.BooleanField)
)
# Read from the raw request by VideoProjectSerializer.create (_raw_bool).
RAW_BOOLEANS = ("speaker_tracking", "auto_render_bypass", "add_subtitles", "use_facetracking", "remove_silences")
FROM_PROMPT = "/api/v1/projects/from-prompt/"


class MultipartBooleansTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bools", email="bools@test.com", password="pw")
        wallet = Wallet.objects.get(workspace=self.user.owned_workspaces.get())
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("1000.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def upload(self, url=None, **extra):
        probed = MediaInfo(duration_s=10.0, width=1920, height=1080, mime="video/mp4")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("apps.videos.services.media_validation.validate_upload", return_value=probed), \
                patch("apps.videos.views.process_initial_ingestion.delay"):
            response = self.client.post(url or reverse("project-list"), {
                "title": "Booleanos", **extra,
                "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
            }, format="multipart")
        self.assertEqual(response.status_code, 201, response.content[:300])
        return VideoProject.objects.get(pk=response.data["id"])

    def defaults(self):
        return {name: VideoProject._meta.get_field(name).default for name in MODEL_BOOLEANS}

    def values(self, project):
        project.refresh_from_db()
        return {name: getattr(project, name) for name in MODEL_BOOLEANS}

    def test_the_project_booleans_are_the_ones_checked_here(self):
        self.assertEqual(MODEL_BOOLEANS, [
            "add_subtitles", "auto_render_bypass", "remove_silences", "speaker_tracking",
            "subtitle_with_emojis", "use_facetracking",
        ])

    def test_an_update_without_the_booleans_keeps_what_the_project_had(self):
        project = self.upload()
        url = reverse("project-detail", args=[project.pk])
        for had in (True, False):
            for method, fmt in (("put", "multipart"), ("put", "json"), ("patch", "multipart")):
                with self.subTest(had=had, method=method, format=fmt):
                    VideoProject.objects.filter(pk=project.pk).update(**{name: had for name in MODEL_BOOLEANS})
                    response = getattr(self.client, method)(url, {"title": "Otro título"}, format=fmt)
                    self.assertEqual(response.status_code, 200, response.content[:300])
                    self.assertEqual(self.values(project), {name: had for name in MODEL_BOOLEANS})

    def test_a_multipart_put_that_sends_them_still_changes_them(self):
        project = self.upload()
        url = reverse("project-detail", args=[project.pk])
        for value in (True, False):
            with self.subTest(value=value):
                VideoProject.objects.filter(pk=project.pk).update(**{name: not value for name in MODEL_BOOLEANS})
                response = self.client.put(
                    url, {"title": "Otro", **{name: str(value).lower() for name in MODEL_BOOLEANS}}, format="multipart",
                )
                self.assertEqual(response.status_code, 200, response.content[:300])
                self.assertEqual(self.values(project), {name: value for name in MODEL_BOOLEANS})

    def test_a_multipart_create_without_them_takes_the_model_defaults(self):
        self.assertEqual(self.values(self.upload()), self.defaults())

    def test_an_empty_raw_boolean_at_creation_counts_as_not_sent(self):
        for name in RAW_BOOLEANS:
            with self.subTest(name):
                self.assertEqual(self.values(self.upload(**{name: ""})), self.defaults())
                project = self.upload(url=FROM_PROMPT, prompt="clips cortos con subtítulos", **{name: ""})
                self.assertEqual(self.values(project), self.defaults())
                self.assertNotIn(name, project.metadata["pte_explicit"])

    def test_a_raw_boolean_that_is_sent_still_wins_and_is_explicit(self):
        project = self.upload(url=FROM_PROMPT, prompt="sin subtítulos", add_subtitles="false", remove_silences="on")
        self.assertFalse(project.add_subtitles)
        self.assertTrue(project.remove_silences)
        self.assertEqual(
            [name for name in project.metadata["pte_explicit"] if name in RAW_BOOLEANS],
            ["add_subtitles", "remove_silences"],
        )


class NotSentBooleanFieldsTests(SimpleTestCase):
    def test_every_model_boolean_of_the_serializer_is_not_sent_when_left_out(self):
        fields = VideoProjectSerializer().fields
        for name in MODEL_BOOLEANS:
            with self.subTest(name):
                self.assertIsInstance(fields[name], _NotSentBooleanField)
        # use_seo is not a column: it is read at creation only and an update drops it.
        self.assertEqual(
            sorted(name for name, field in fields.items()
                   if isinstance(field, serializers.BooleanField) and not isinstance(field, _NotSentBooleanField)),
            ["use_seo"],
        )

    def test_an_empty_boolean_is_not_an_explicit_field(self):
        raw = {
            "add_subtitles": "", "speaker_tracking": "  ", "use_seo": "", "use_ai_thumbnail": None,
            "remove_silences": "false", "selection_criteria": "",
        }
        # A blank text still counts (documented in 04-prompt-to-edit): only the booleans change.
        self.assertEqual(explicit_config_fields(raw), ["remove_silences", "selection_criteria"])
