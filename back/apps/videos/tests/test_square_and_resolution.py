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
        self.assertEqual(RenderEngine._get_target_resolution(VideoProject.AspectRatio.SQUARE_1_1, "1080p"), (1080, 1080))

    def test_resolution_tiers_set_the_frame_height(self):
        self.assertEqual(RenderEngine._get_target_resolution("16:9", "720p"), (1280, 720))
        self.assertEqual(RenderEngine._get_target_resolution("16:9", "4K"), (3840, 2160))
        self.assertEqual(RenderEngine._get_target_resolution("9:16", "1080p"), (608, 1080))

    def test_without_a_tier_the_render_is_720p_like_the_price(self):
        # The price of a project without render_resolution is 720p; it used to
        # render at 1080, delivering more than what was charged.
        self.assertEqual(RenderEngine._get_target_resolution("9:16"), RenderEngine._get_target_resolution("9:16", "720p"))
        self.assertEqual(RenderEngine._get_target_resolution("9:16")[1], 720)
        self.assertEqual(RenderEngine._get_target_resolution("9:16", "bogus")[1], 720)

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


class ChosenResolutionTests(TestCase):
    """The resolution is chosen at creation, priced, checked against the plan and fixed."""

    def setUp(self):
        from apps.payments.models import SubscriptionPlan

        self.user = User.objects.create_user(username="res", email="res@test.com", password="pw")
        if not self.user.workspaces.exists():
            Workspace.objects.create(name="Res", owner=self.user).members.add(self.user)
        self.workspace = self.user.workspaces.first()  # the one the create view uses
        wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("1000.00"))
        self.plan_720 = SubscriptionPlan.objects.create(
            name="Solo 720", max_resolution="720p", max_video_duration_seconds=3600,
        )
        # Every tier and no discount, so the price is the formula's. A new
        # workspace starts on Gratis, which stops at 720p.
        self.plan_4k = SubscriptionPlan.objects.create(
            name="Hasta 4K", max_resolution="4K", max_video_duration_seconds=3600,
        )
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=self.plan_4k)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def upload(self, **extra):
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from apps.videos.services.media_validation import MediaInfo

        probed = MediaInfo(duration_s=100.0, width=1920, height=1080, mime="video/mp4")
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), \
                patch("apps.videos.services.media_validation.validate_upload", return_value=probed), \
                patch("apps.videos.views.process_initial_ingestion.delay"):
            return self.client.post(reverse("project-list"), {
                "title": "Con resolución", **extra,
                "source_file": SimpleUploadedFile("v.mp4", b"0" * 10, content_type="video/mp4"),
            }, format="multipart")

    def reserved(self, response):
        from apps.payments.models import Transaction

        project = VideoProject.objects.get(pk=response.data["id"])
        return Transaction.objects.get(pk=project.metadata["reservation_id"]).amount

    def test_the_chosen_resolution_is_stored_and_priced(self):
        response = self.upload(render_resolution="1080p")

        self.assertEqual(response.status_code, 201, response.content[:300])
        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertEqual(project.metadata["render_resolution"], "1080p")
        self.assertEqual(self.reserved(response), Decimal("75.00"))  # 100 s x 0,5 x 1,5
        self.assertNotIn("render_resolution", response.data)  # write-only

    def test_without_a_choice_the_project_is_720p(self):
        response = self.upload()

        project = VideoProject.objects.get(pk=response.data["id"])
        self.assertNotIn("render_resolution", project.metadata)
        self.assertEqual(self.reserved(response), Decimal("50.00"))  # 100 s x 0,5

    def test_a_resolution_above_the_plan_is_rejected_before_charging(self):
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=self.plan_720)

        response = self.upload(title="4K", render_resolution="4K")

        self.assertEqual(response.status_code, 403, response.content[:300])
        self.assertFalse(VideoProject.objects.filter(title="4K").exists())

    def test_an_unknown_resolution_is_a_validation_error(self):
        self.assertEqual(self.upload(render_resolution="8K").status_code, 400)

    def test_a_link_project_stays_at_720p(self):
        # The flat link price does not know the duration: a higher tier could not
        # be priced, and an auto-rendered link would have rendered it for free.
        with patch("apps.videos.views.process_initial_ingestion.delay"):
            rejected = self.client.post(reverse("project-list"), {
                "title": "Link 1080", "video_url": "https://www.youtube.com/watch?v=x",
                "render_resolution": "1080p", "auto_render_bypass": "true",
            }, format="json")
            accepted = self.client.post(reverse("project-list"), {
                "title": "Link 720", "video_url": "https://www.youtube.com/watch?v=x",
            }, format="json")

        self.assertEqual(rejected.status_code, 400, rejected.content[:300])
        self.assertFalse(VideoProject.objects.filter(title="Link 1080").exists())
        self.assertEqual(accepted.status_code, 201, accepted.content[:300])
        self.assertEqual(self.reserved(accepted), Decimal("10.00"))  # the flat link price, no 0,01 floor

    def test_a_link_project_with_seo_on_a_plan_without_it_is_still_rejected(self):
        from apps.payments.models import SubscriptionPlan

        no_seo = SubscriptionPlan.objects.create(name="Sin SEO", has_seo_optimization=False, max_video_duration_seconds=3600)
        Workspace.objects.filter(pk=self.workspace.pk).update(subscription_plan=no_seo)
        with patch("apps.videos.views.process_initial_ingestion.delay"):
            response = self.client.post(reverse("project-list"), {
                "title": "Link SEO", "video_url": "https://www.youtube.com/watch?v=x", "use_seo": True,
            }, format="json")

        self.assertEqual(response.status_code, 403, response.content[:300])

    def test_the_resolution_cannot_change_after_creation(self):
        response = self.upload()
        project_id = response.data["id"]

        self.client.patch(reverse("project-detail", args=[project_id]), {"render_resolution": "4K"}, format="json")

        self.assertNotIn("render_resolution", VideoProject.objects.get(pk=project_id).metadata)
