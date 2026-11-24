"""
speaker_tracking turns into the layout that follows the speaker ('active'),
at creation and from a Prompt-to-Edit prompt; a layout the request sends
explicitly always wins.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Wallet
from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.services.ai.contracts import AIExecutionResult, ProjectConfig, ProviderUsage
from apps.videos.services.ai.prompt_validator import ConfigValidator, apply_prompt_to_project

User = get_user_model()

PARSER = "apps.videos.services.ai.prompt_parser.PromptParser"
MODERATE = "apps.core.security.AI_Security_Shield.check_content_safety"


class SpeakerTrackingAtCreationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="speaker", email="speaker@test.com", password="pw")
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
            response = self.client.post(reverse("project-list"), body, format="json")
        self.assertEqual(response.status_code, 201, response.content[:200])
        return VideoProject.objects.get(pk=response.data["id"])

    def test_following_the_speaker_without_a_layout_uses_the_active_layout(self):
        for value in (True, "true", "1"):
            with self.subTest(value=value):
                project = self.create(speaker_tracking=value)
                self.assertTrue(project.speaker_tracking)
                self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)

    def test_an_explicit_layout_wins(self):
        for payload in ({"render_layout": "split"}, {"layout": "versus"}):
            with self.subTest(payload=payload):
                project = self.create(speaker_tracking=True, **payload)
                self.assertTrue(project.speaker_tracking)
                self.assertEqual(project.render_layout, next(iter(payload.values())))

    def test_without_speaker_tracking_the_default_layout_stays(self):
        for payload in ({}, {"speaker_tracking": False}):
            with self.subTest(payload=payload):
                project = self.create(**payload)
                self.assertEqual(project.render_layout, VideoProject.Layout.FILL)


class SpeakerTrackingFromPromptTests(TestCase):
    def test_speaker_tracking_without_a_layout_applies_the_active_layout(self):
        outcome = ConfigValidator.validate(ProjectConfig(speaker_tracking=True), None)

        self.assertEqual(outcome.applied, {"speaker_tracking": True, "render_layout": "active"})
        self.assertEqual(outcome.ignored, [])

    def test_a_layout_in_the_prompt_is_kept(self):
        outcome = ConfigValidator.validate(ProjectConfig(speaker_tracking=True, render_layout="split"), None)

        self.assertEqual(outcome.applied, {"speaker_tracking": True, "render_layout": "split"})

    def test_an_explicit_layout_from_the_request_wins(self):
        outcome = ConfigValidator.validate(
            ProjectConfig(speaker_tracking=True), None, explicit_fields=["render_layout"],
        )

        self.assertEqual(outcome.applied, {"speaker_tracking": True})
        self.assertEqual(outcome.ignored, [{"field": "render_layout", "reason": "explicit"}])

    def test_no_layout_when_speaker_tracking_is_off_or_not_applied(self):
        off = ConfigValidator.validate(ProjectConfig(speaker_tracking=False), None)
        self.assertEqual(off.applied, {"speaker_tracking": False})

        explicit = ConfigValidator.validate(
            ProjectConfig(speaker_tracking=True), None, explicit_fields=["speaker_tracking"],
        )
        self.assertEqual(explicit.applied, {})
        self.assertEqual(explicit.ignored, [{"field": "speaker_tracking", "reason": "explicit"}])

    def test_the_project_is_saved_with_the_active_layout(self):
        user = User.objects.create_user(username="pte-speaker", email="pte-speaker@test.com", password="pw")
        workspace = Workspace.objects.create(name="PTE speaker", owner=user)
        project = VideoProject.objects.create(
            workspace=workspace, uploaded_by=user, title="P",
            metadata={"pte_prompt": "seguí a quien habla", "pte_explicit": []},
        )
        usage = ProviderUsage(provider="gemini", model="gemini/gemini-3.8-flash", total_tokens=50)
        result = AIExecutionResult[ProjectConfig](
            data=ProjectConfig(speaker_tracking=True), usage=usage, attempts=[usage],
        )
        with patch(MODERATE, return_value=True), patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = result
            outcome = apply_prompt_to_project(project.pk)

        project.refresh_from_db()
        self.assertTrue(project.speaker_tracking)
        self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)
        self.assertEqual(outcome["applied"], {"speaker_tracking": True, "render_layout": "active"})
