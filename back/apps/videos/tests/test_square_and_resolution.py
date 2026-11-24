"""The square ratio really renders square, and the render honours render_resolution."""

from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Wallet
from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.services.ai.contracts import ProjectConfig
from apps.videos.services.render_engine import RenderEngine

User = get_user_model()


class TargetResolutionTests(SimpleTestCase):
    def test_the_square_choice_renders_square(self):
        self.assertEqual(RenderEngine._get_target_resolution(VideoProject.AspectRatio.SQUARE_1_1), (1080, 1080))

    def test_resolution_tiers_set_the_frame_height(self):
        self.assertEqual(RenderEngine._get_target_resolution("16:9", "720p"), (1280, 720))
        self.assertEqual(RenderEngine._get_target_resolution("16:9", "4K"), (3840, 2160))
        self.assertEqual(RenderEngine._get_target_resolution("9:16"), (608, 1080))  # unchanged default
        self.assertEqual(RenderEngine._get_target_resolution("9:16", "bogus"), (608, 1080))

    def test_the_prompt_contract_reads_the_old_value_as_square(self):
        self.assertEqual(ProjectConfig.model_validate({"aspect_ratio": "1:Post"}).aspect_ratio, "1:1")


class SquareMigrationTests(TestCase):
    def test_existing_rows_are_converted_both_ways(self):
        user = User.objects.create_user(username="sq", email="sq@test.com", password="pw")
        workspace = Workspace.objects.create(name="SQ", owner=user)
        project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="P")
        VideoProject.objects.filter(pk=project.pk).update(aspect_ratio="1:Post")
        migration = import_module("apps.videos.migrations.0009_square_aspect_ratio")

        migration.to_square_1_1(django_apps, None)
        project.refresh_from_db()
        self.assertEqual(project.aspect_ratio, "1:1")

        migration.to_legacy_square(django_apps, None)
        project.refresh_from_db()
        self.assertEqual(project.aspect_ratio, "1:Post")


class SerializerAliasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alias", email="alias@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="W", owner=self.user).members.add(self.user)
        for workspace in self.user.workspaces.all():
            wallet, _ = Wallet.objects.get_or_create(workspace=workspace)
            Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("100.00"))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def create(self, **payload):
        body = {"title": "P", "video_url": "https://www.youtube.com/watch?v=x", **payload}
        with patch("apps.videos.views.process_initial_ingestion.delay"):
            return self.client.post(reverse("project-list"), body, format="json")

    def test_clients_sending_the_old_value_get_a_square_project(self):
        for value in ("1:Post", "1:1"):
            with self.subTest(value=value):
                response = self.create(aspect_ratio=value)
                self.assertEqual(response.status_code, 201, response.content[:200])
                self.assertEqual(VideoProject.objects.get(pk=response.data["id"]).aspect_ratio, "1:1")

    def test_an_unknown_ratio_is_rejected_instead_of_stored(self):
        response = self.create(aspect_ratio="4:3")
        self.assertEqual(response.status_code, 400)


class PricingResolutionTests(TestCase):
    def test_pricing_gets_a_resolution_tier_not_the_aspect_ratio(self):
        from apps.videos.views import _pricing_resolution

        project = VideoProject(aspect_ratio="9:16", metadata={})
        self.assertEqual(_pricing_resolution(project), "720p")
        project.metadata = {"render_resolution": "1080p"}
        self.assertEqual(_pricing_resolution(project), "1080p")
        project.metadata = {"render_resolution": "=HACK"}
        self.assertEqual(_pricing_resolution(project), "720p")
