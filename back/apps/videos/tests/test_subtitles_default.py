"""
Un proyecto creado sin el campo `add_subtitles` sale con subtítulos. En un
formulario multipart, DRF lee un booleano ausente como falso (un checkbox sin
marcar), así que antes el default del modelo nunca se aplicaba. Una edición sin
el campo deja lo que el proyecto tenía.
"""

import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Wallet
from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.services.media_validation import MediaInfo

User = get_user_model()


class SubtitlesDefaultTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="subs", email="subs@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="Subs", owner=self.user).members.add(self.user)
        wallet, _ = Wallet.objects.get_or_create(workspace=self.user.workspaces.first())
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("1000.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def upload(self, **extra):
        probed = MediaInfo(duration_s=10.0, width=1920, height=1080, mime="video/mp4")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("apps.videos.services.media_validation.validate_upload", return_value=probed), \
                patch("apps.videos.views.process_initial_ingestion.delay"):
            response = self.client.post(reverse("project-list"), {
                "title": "Subtítulos", **extra,
                "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
            }, format="multipart")
        self.assertEqual(response.status_code, 201, response.content[:300])
        return VideoProject.objects.get(pk=response.data["id"])

    def test_a_multipart_project_without_the_field_has_subtitles(self):
        self.assertTrue(self.upload().add_subtitles)

    def test_the_field_can_still_turn_them_off(self):
        self.assertFalse(self.upload(add_subtitles="false").add_subtitles)

    def test_an_update_without_the_field_keeps_what_the_project_had(self):
        for had in (True, False):
            project = self.upload(add_subtitles=str(had).lower())
            url = reverse("project-detail", args=[project.pk])
            for method, fmt in (("put", "json"), ("put", "multipart"), ("patch", "multipart")):
                with self.subTest(had=had, method=method, format=fmt):
                    response = getattr(self.client, method)(url, {"title": "Otro título"}, format=fmt)
                    self.assertEqual(response.status_code, 200, response.content[:300])
                    project.refresh_from_db()
                    self.assertEqual(project.add_subtitles, had)

    def test_leaving_it_out_does_not_count_as_explicit_for_prompt_to_edit(self):
        from apps.videos.services.ai.prompt_validator import explicit_config_fields

        self.assertNotIn("add_subtitles", explicit_config_fields({"title": "x"}))
        self.assertIn("add_subtitles", explicit_config_fields({"add_subtitles": "true"}))
