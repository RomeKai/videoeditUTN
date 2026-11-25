"""
Los textos que la API devuelve llegan con sus acentos. Algunos estaban guardados
con la codificación rota ("estÃ¡" en lugar de "está"), y el frontend no podía
reconocerlos.
"""

import pathlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.users.models import Workspace
from apps.videos.models import VideoProject

User = get_user_model()

MOJIBAKE = ("Ã", "Â¿", "Â¡")


class ApiTextEncodingTests(TestCase):
    def test_an_approval_of_a_project_not_in_review_explains_it_with_accents(self):
        user = User.objects.create_user(username="enc", email="enc@test.com", password="pw")
        if not user.workspaces.exists():
            Workspace.objects.create(name="Enc", owner=user).members.add(user)
        project = VideoProject.objects.create(
            workspace=user.workspaces.first(), uploaded_by=user, title="Enc",
            status=VideoProject.Status.RENDERING,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            reverse("project-approve-paper-edit", args=[project.pk]),
            {"approved_segments": [{"start": 0, "end": 1}]}, format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no está en espera de aprobación", response.data["error"])

    def test_no_source_file_of_the_videos_app_has_broken_accents(self):
        app = pathlib.Path(__file__).resolve().parents[1]
        broken = [
            str(path.relative_to(app))
            for path in app.rglob("*.py")
            if "migrations" not in path.parts and path.name != pathlib.Path(__file__).name
            and any(mark in path.read_text(encoding="utf-8") for mark in MOJIBAKE)
        ]
        self.assertEqual(broken, [])
