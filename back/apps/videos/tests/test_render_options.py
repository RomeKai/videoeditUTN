"""
speaker_tracking turns into the layout that follows the speaker ('active'),
at creation and from a Prompt-to-Edit prompt; a layout the request sends
explicitly always wins. The ``layout`` alias is validated, and the raw
booleans of the creation request read 'on' as true, like DRF.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.payments.models import Transaction, Wallet
from apps.users.models import Workspace
from apps.videos.models import VideoProject
from apps.videos.services.ai.contracts import AIExecutionResult, ProjectConfig, ProviderUsage
from apps.videos.services.ai.prompt_validator import (
    ConfigValidator,
    apply_prompt_to_project,
    explicit_config_fields,
)

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

    def post(self, url=None, **payload):
        body = {"title": "P", "video_url": "https://www.youtube.com/watch?v=x", **payload}
        with patch("apps.videos.views.process_initial_ingestion.delay"):
            return self.client.post(url or reverse("project-list"), body, format="json")

    def create(self, url=None, **payload):
        response = self.post(url, **payload)
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

    def test_from_prompt_the_derived_layout_counts_as_explicit(self):
        """An explicit speaker_tracking chose 'active': a layout in the prompt must not replace it."""
        project = self.create(url="/api/v1/projects/from-prompt/", prompt="con fondo borroso",
                              speaker_tracking=True)
        self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)
        self.assertIn("render_layout", project.metadata["pte_explicit"])

        with patch(MODERATE, return_value=True), patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = _parsed(render_layout="blurred")
            outcome = apply_prompt_to_project(project.pk)

        project.refresh_from_db()
        self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)
        self.assertIn({"field": "render_layout", "reason": "explicit"}, outcome["ignored"])

    def test_an_empty_layout_alias_counts_as_not_sent(self):
        for empty in ("", "  ", None):
            with self.subTest(layout=empty):
                self.assertEqual(self.create(layout=empty).render_layout, VideoProject.Layout.FILL)
                project = self.create(layout=empty, speaker_tracking=True)
                self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)

    def test_a_layout_alias_that_is_not_a_layout_is_rejected_before_saving(self):
        for bad in ("bogus", "x" * 11, 3):
            with self.subTest(layout=bad):
                response = self.post(layout=bad)
                self.assertEqual(response.status_code, 400)
                self.assertIn("layout", response.data)
        self.assertFalse(VideoProject.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_a_valid_layout_alias_is_stored(self):
        self.assertEqual(self.create(layout="versus").render_layout, VideoProject.Layout.VERSUS)

    def test_raw_booleans_read_on_as_true(self):
        project = self.create(
            speaker_tracking="on", use_facetracking="on", remove_silences="on",
            auto_render_bypass="on", add_subtitles="off",
        )
        self.assertTrue(project.speaker_tracking)
        self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)
        self.assertTrue(project.use_facetracking)
        self.assertTrue(project.remove_silences)
        self.assertTrue(project.auto_render_bypass)
        self.assertFalse(project.add_subtitles)


def _parsed(**settings):
    usage = ProviderUsage(provider="gemini", model="gemini/gemini-3.8-flash", total_tokens=50)
    return AIExecutionResult[ProjectConfig](data=ProjectConfig(**settings), usage=usage, attempts=[usage])


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
        # The prompt never asked for a layout: nothing of it was dropped.
        self.assertEqual(outcome.ignored, [])

    def test_an_empty_layout_alias_is_not_explicit(self):
        self.assertEqual(explicit_config_fields({"layout": ""}), [])
        self.assertEqual(explicit_config_fields({"layout": None}), [])
        self.assertEqual(explicit_config_fields({"layout": "fit"}), ["render_layout"])

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
        with patch(MODERATE, return_value=True), patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = _parsed(speaker_tracking=True)
            outcome = apply_prompt_to_project(project.pk)

        project.refresh_from_db()
        self.assertTrue(project.speaker_tracking)
        self.assertEqual(project.render_layout, VideoProject.Layout.ACTIVE)
        self.assertEqual(outcome["applied"], {"speaker_tracking": True, "render_layout": "active"})
