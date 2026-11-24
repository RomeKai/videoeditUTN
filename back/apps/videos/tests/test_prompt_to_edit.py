"""PTE-01b (#73): POST /api/v1/projects/from-prompt/ and the plan-checked config."""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.payments.models import SubscriptionPlan, Transaction, Wallet
from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.contracts import AIExecutionResult, ProjectConfig, ProviderUsage
from apps.videos.services.ai.errors import AIServerError
from apps.videos.services.ai.prompt_validator import ConfigValidator, apply_prompt_to_project
from apps.videos.tests.test_capacity_pipeline import CapacityHarness
from apps.videos.tests.test_ingestion_pipeline import IngestionHarness

User = get_user_model()

URL = "/api/v1/projects/from-prompt/"
DISPATCH = "apps.videos.views.process_initial_ingestion.delay"
PARSER = "apps.videos.services.ai.prompt_parser.PromptParser"
MODERATE = "apps.core.security.AI_Security_Shield.check_content_safety"
PROMPT = "vertical con fondo borroso, en 4K, cortá los momentos de humor"


def plan(name, **limits):
    created = SubscriptionPlan.objects.create(name=name, **limits)
    return SubscriptionPlan.objects.get(pk=created.pk)  # Decimal fields, as in production


def parsed(attempts=1, **settings):
    usage = ProviderUsage(provider="gemini", model="gemini/gemini-3.8-flash", total_tokens=120,
                          estimated_cost_usd=0.0001)
    return AIExecutionResult[ProjectConfig](
        data=ProjectConfig(**settings), usage=usage, attempts=[usage] * attempts,
    )


class FromPromptEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pte", email="pte@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="PTE", owner=self.user)
        self.workspace.members.add(self.user)
        for workspace in self.user.workspaces.all():  # the user may own an auto-created one too
            Wallet.objects.update_or_create(
                workspace=workspace, defaults={"available_balance": Decimal("100.00")}
            )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.dispatch = patch(DISPATCH).start()
        self.addCleanup(patch.stopall)

    def post(self, **data):
        payload = {"title": "Desde prompt", "video_url": "https://youtu.be/x", **data}
        return self.client.post(URL, payload, format="json")

    def test_creates_reserves_and_dispatches_like_a_normal_upload(self):
        res = self.post(prompt=PROMPT)

        self.assertEqual(res.status_code, 201, res.data)
        project = VideoProject.objects.get(pk=res.data["id"])
        self.assertEqual(project.metadata["pte_prompt"], PROMPT)
        self.assertEqual(res.data["prompt_config"]["status"], "pending")
        self.assertEqual(res.data["cost_details"]["tokens_reserved"], Decimal("10.0"))
        self.assertTrue(Transaction.objects.filter(pk=res.data["cost_details"]["transaction_id"]).exists())
        self.dispatch.assert_called_once_with(project.id)

    def test_the_fields_sent_explicitly_are_recorded_so_they_win(self):
        res = self.post(prompt=PROMPT, aspect_ratio="16:9", max_clips=3, layout="fit")

        project = VideoProject.objects.get(pk=res.data["id"])
        self.assertEqual(
            project.metadata["pte_explicit"], ["aspect_ratio", "max_clips", "render_layout"]
        )
        self.assertEqual(project.aspect_ratio, "16:9")

    def test_an_empty_or_missing_prompt_is_rejected_before_reserving(self):
        for data in ({}, {"prompt": ""}, {"prompt": "   "}):
            with self.subTest(data=data):
                res = self.post(**data)
                self.assertEqual(res.status_code, 400)
                self.assertIn("prompt", res.data)
        self.assertFalse(VideoProject.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.dispatch.assert_not_called()

    def test_a_prompt_over_the_limit_is_rejected(self):
        with self.settings(AI_PROMPT_MAX_CHARS=20):
            res = self.post(prompt="x" * 21)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(VideoProject.objects.exists())

    def test_the_same_validation_as_a_normal_creation_applies(self):
        res = self.client.post(URL, {"title": "Sin fuente", "prompt": PROMPT}, format="json")
        self.assertEqual(res.status_code, 400)

        Wallet.objects.update(available_balance=Decimal("0"))
        res = self.post(prompt=PROMPT)
        self.assertEqual(res.status_code, 402)
        self.assertFalse(VideoProject.objects.exists())

    def test_authentication_is_required(self):
        res = APIClient().post(URL, {"prompt": PROMPT, "video_url": "https://youtu.be/x"}, format="json")
        self.assertIn(res.status_code, (401, 403))


class ConfigValidatorTests(TestCase):
    def setUp(self):
        self.free = plan("Free", max_resolution="720p")
        self.pro = plan("Pro", max_resolution="4K", has_seo_optimization=True, has_thumbnail_engine=True)

    def test_free_plan_with_a_prompt_that_asks_for_4k(self):
        config = ProjectConfig(render_resolution="4K", aspect_ratio="16:9")

        outcome = ConfigValidator.validate(config, self.free)

        self.assertEqual(outcome.applied, {"aspect_ratio": "16:9"})
        self.assertEqual(outcome.ignored, [{"field": "render_resolution", "reason": "plan"}])

    def test_a_request_the_plan_allows_but_that_raises_the_price_is_ignored(self):
        outcome = ConfigValidator.validate(ProjectConfig(render_resolution="4K", use_seo=True), self.pro)

        self.assertEqual(outcome.applied, {})
        self.assertEqual(
            sorted(i["field"] for i in outcome.ignored if i["reason"] == "cost"),
            ["render_resolution", "use_seo"],
        )

    def test_requests_that_keep_the_price_are_applied(self):
        outcome = ConfigValidator.validate(
            ProjectConfig(render_resolution="720p", use_ai_thumbnail=False), self.free
        )
        self.assertEqual(outcome.applied, {"render_resolution": "720p", "use_ai_thumbnail": False})

    def test_paid_add_ons_outside_the_plan_are_ignored_for_the_plan(self):
        outcome = ConfigValidator.validate(ProjectConfig(use_seo=True, use_ai_thumbnail=True), self.free)
        self.assertEqual({i["reason"] for i in outcome.ignored}, {"plan"})

    def test_explicit_request_fields_win(self):
        outcome = ConfigValidator.validate(
            ProjectConfig(aspect_ratio="9:16", max_clips=4), None, explicit_fields=["aspect_ratio"]
        )
        self.assertEqual(outcome.applied, {"max_clips": 4})
        self.assertEqual(outcome.ignored, [{"field": "aspect_ratio", "reason": "explicit"}])


class ApplyPromptTests(TestCase):
    def setUp(self):
        # The prompt is moderated before the parser (ADR-011 D-M2); never the real API here.
        moderate = patch(MODERATE, return_value=True)
        self.mock_moderate = moderate.start()
        self.addCleanup(moderate.stop)
        user = User.objects.create_user(username="apply", email="apply@test.com", password="pw")
        self.workspace = Workspace.objects.create(
            name="Apply", owner=user, subscription_plan=plan("Free", max_resolution="720p")
        )
        self.project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=user, title="P",
            metadata={"pte_prompt": PROMPT, "pte_explicit": ["aspect_ratio"]},
        )

    def test_applies_what_is_allowed_and_reports_the_rest(self):
        result = parsed(
            aspect_ratio="9:16", render_layout="blurred", render_resolution="4K",
            selection_criteria="momentos de humor", max_clips=3,
        )
        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = result
            parse = parser_cls.return_value.parse
            outcome = apply_prompt_to_project(self.project.pk)

        parse.assert_called_once()
        self.project.refresh_from_db()
        self.assertEqual(self.project.render_layout, "blurred")
        self.assertEqual(self.project.aspect_ratio, "9:16")  # default kept: the request set it
        self.assertEqual(self.project.metadata["selection_criteria"], "momentos de humor")
        self.assertEqual(self.project.metadata["max_clips"], 3)
        self.assertNotIn("render_resolution", self.project.metadata)
        pte = self.project.metadata["pte"]
        self.assertEqual(pte, outcome)
        self.assertEqual(pte["status"], "applied")
        self.assertIn({"field": "render_resolution", "reason": "plan"}, pte["ignored"])
        self.assertIn({"field": "aspect_ratio", "reason": "explicit"}, pte["ignored"])
        self.assertEqual(
            AIUsageRecord.objects.filter(project=self.project, stage="prompt_parse").count(), 1
        )

    def test_a_parser_failure_keeps_the_defaults_and_records_the_attempts(self):
        error = AIServerError("down")
        error.usage_attempts = [
            ProviderUsage(provider="gemini", model="m", role="primary", error_code="AIServerError"),
            ProviderUsage(provider="gemini", model="f", role="fallback", error_code="AIServerError"),
        ]
        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = error
            outcome = apply_prompt_to_project(self.project.pk)

        self.project.refresh_from_db()
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error_code"], "AIServerError")
        self.assertEqual(self.project.render_layout, VideoProject.Layout.FILL)
        self.assertEqual(
            AIUsageRecord.objects.filter(project=self.project, stage="prompt_parse").count(), 2
        )

    def test_it_runs_once(self):
        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = parsed(max_clips=2)
            parse = parser_cls.return_value.parse
            apply_prompt_to_project(self.project.pk)
            self.assertIsNone(apply_prompt_to_project(self.project.pk))
        parse.assert_called_once()

    def test_a_project_without_a_prompt_is_untouched(self):
        VideoProject.objects.filter(pk=self.project.pk).update(metadata={})
        with patch(PARSER) as parser_cls:
            parse = parser_cls.return_value.parse
            self.assertIsNone(apply_prompt_to_project(self.project.pk))
        parse.assert_not_called()


class IngestionIntegrationTests(IngestionHarness, TestCase):
    def test_the_config_is_applied_before_selection_and_render_reuse_it(self):
        project = self.make_project(metadata={"pte_prompt": PROMPT, "pte_explicit": []})
        result = parsed(max_clips=2, selection_criteria="momentos de humor", render_layout="blurred")

        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = result
            parse = parser_cls.return_value.parse
            self.run_task(project)
            self.run_task(project)  # a redelivery does not parse again

        parse.assert_called_once()
        kwargs = self.mock_select.call_args.kwargs
        self.assertEqual(kwargs["max_clips"], 2)
        self.assertEqual(kwargs["selection_criteria"], "momentos de humor")
        project.refresh_from_db()
        self.assertEqual(project.render_layout, "blurred")
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    def test_a_parser_failure_does_not_stop_the_ingestion(self):
        project = self.make_project(metadata={"pte_prompt": PROMPT})

        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = AIServerError("down")
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(project.metadata["pte"]["status"], "failed")


class TransientParserErrorTests(ApplyPromptTests):
    """Review of #86: a 429/503 on both models must not lose the user's prompt."""

    def test_a_transient_error_is_raised_while_the_task_can_retry(self):
        error = AIServerError("down")
        error.usage_attempts = [ProviderUsage(provider="gemini", model="m", role="primary", error_code="AIServerError")]
        from apps.videos.services.ai.prompt_validator import PromptParseRetry

        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = error
            with self.assertRaises(PromptParseRetry):
                apply_prompt_to_project(self.project.pk, can_retry=True)

        self.project.refresh_from_db()
        self.assertNotIn("pte", self.project.metadata)  # the next attempt parses again
        self.assertEqual(AIUsageRecord.objects.filter(project=self.project, stage="prompt_parse").count(), 1)

    def test_a_definitive_error_fails_at_once_even_with_retries_left(self):
        from apps.videos.services.ai.errors import AIAuthenticationError

        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = AIAuthenticationError("bad key")
            outcome = apply_prompt_to_project(self.project.pk, can_retry=True)

        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error_code"], "AIAuthenticationError")


class TransientParserIngestionTests(IngestionHarness, TestCase):
    def test_the_ingestion_retries_and_applies_the_prompt(self):
        project = self.make_project(metadata={"pte_prompt": PROMPT, "pte_explicit": []})

        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = [AIServerError("down"), parsed(max_clips=2)]
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(parser_cls.return_value.parse.call_count, 2)
        self.assertEqual(project.metadata["pte"]["status"], "applied")
        self.assertEqual(project.metadata["max_clips"], 2)
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(self.mock_select.call_args.kwargs["max_clips"], 2)

    def test_after_the_last_retry_the_ingestion_goes_on_with_the_defaults(self):
        from apps.videos.tasks import PTE_PARSE_MAX_RETRIES

        project = self.make_project(metadata={"pte_prompt": PROMPT})
        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = AIServerError("down")
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(parser_cls.return_value.parse.call_count, PTE_PARSE_MAX_RETRIES + 1)
        self.assertEqual(project.metadata["pte"]["status"], "failed")
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)

    def test_parser_retries_never_spend_the_retries_the_stages_need(self):
        from apps.videos.tests.test_ingestion_pipeline import selection_result

        project = self.make_project(metadata={"pte_prompt": PROMPT})
        self.mock_select.side_effect = [AIServerError("down")] * 4 + [selection_result()]
        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = AIServerError("down")
            self.run_task(project)

        project.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        self.assertEqual(self.mock_select.call_count, 5)
        self.assertEqual(project.pipeline_attempts, 4)  # only the selection failures count


class ParserCapacityTests(CapacityHarness, TestCase):
    """AICORE-11: the parser's LLM call reserves capacity like every other call."""

    def test_no_room_parks_the_project_before_parsing_and_resumes_later(self):
        harness = self
        project = harness.make_project(metadata={"pte_prompt": PROMPT, "pte_explicit": []})
        harness.store.denials = [30.0]

        with patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = parsed(max_clips=2)
            harness.run_task(project)
            project.refresh_from_db()
            parser_cls.return_value.parse.assert_not_called()
            harness.requeue.assert_called_once()
            self.assertEqual(project.pipeline_stage_status.get("audio_extracted"), "pending_capacity")

            harness.run_task(project)  # what the re-queued message runs

        project.refresh_from_db()
        parser_cls.return_value.parse.assert_called_once()
        self.assertEqual(project.metadata["pte"]["status"], "applied")
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)
        parser_reserve = harness.store.reserves[1]
        self.assertTrue(any("gemini" in key for key in parser_reserve))


class ParserCapacityTimeoutTests(CapacityHarness, TestCase):
    def test_past_the_maximum_wait_the_project_goes_on_with_its_settings(self):
        from django.test import override_settings

        project = self.make_project(metadata={"pte_prompt": PROMPT, "pte_explicit": []})
        self.store.denials = [80.0, 80.0]  # only the parser is denied; the stages get room

        with patch(PARSER) as parser_cls, override_settings(AI_CAPACITY_MAX_WAIT_SECONDS=100):
            self.run_task(project)
            self.run_task(project)  # the re-queued run: 80 + 80 > 100

        project.refresh_from_db()
        parser_cls.return_value.parse.assert_not_called()
        self.assertEqual(project.metadata["pte"]["status"], "failed")
        self.assertEqual(project.metadata["pte"]["error_code"], "CapacityTimeoutError")
        self.assertIsNone(project.metadata.get("capacity_wait"))
        self.assertEqual(project.status, VideoProject.Status.AWAITING_APPROVAL)


class PromptModerationTests(TestCase):
    """ADR-011 D-M2: the prompt is moderated before it reaches the parser LLM."""

    def setUp(self):
        user = User.objects.create_user(username="pmod", email="pmod@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="PMod", owner=user)
        self.project = VideoProject.objects.create(
            workspace=self.workspace, uploaded_by=user, title="P",
            metadata={"pte_prompt": PROMPT, "pte_explicit": []},
        )

    def moderation_rows(self):
        return list(AIUsageRecord.objects.filter(
            project=self.project, stage=AIUsageRecord.Stage.MODERATION,
        ).values_list("success", "error_code"))

    def test_a_blocked_prompt_never_reaches_the_parser(self):
        from apps.core.security import UnsafeContentError

        with patch(MODERATE, side_effect=UnsafeContentError("blocked")) as moderate, \
                patch(PARSER) as parser_cls:
            with self.assertRaises(UnsafeContentError):
                apply_prompt_to_project(self.project.id)

        moderate.assert_called_once_with(PROMPT)
        parser_cls.return_value.parse.assert_not_called()
        self.project.refresh_from_db()
        self.assertNotIn("pte", self.project.metadata)
        self.assertEqual(self.moderation_rows(), [(False, "UnsafeContentError")])

    def test_no_verdict_is_raised_for_the_task_to_retry(self):
        from apps.core.security import ModerationUnavailableError

        with patch(MODERATE, side_effect=ModerationUnavailableError()), patch(PARSER) as parser_cls:
            with self.assertRaises(ModerationUnavailableError):
                apply_prompt_to_project(self.project.id)

        parser_cls.return_value.parse.assert_not_called()

    def test_a_safe_prompt_is_moderated_once_then_parsed(self):
        from apps.videos.services.ai.prompt_validator import PromptParseRetry

        with patch(MODERATE, return_value=True) as moderate, patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.side_effect = [AIServerError("down"), parsed(max_clips=2)]
            with self.assertRaises(PromptParseRetry):
                apply_prompt_to_project(self.project.id, can_retry=True)
            apply_prompt_to_project(self.project.id, can_retry=True)  # the re-queued run

        moderate.assert_called_once_with(PROMPT)
        self.assertEqual(parser_cls.return_value.parse.call_count, 2)
        self.project.refresh_from_db()
        self.assertTrue(self.project.metadata["pte_moderated"])
        self.assertEqual(self.project.metadata["pte"]["status"], "applied")
        self.assertEqual(self.moderation_rows(), [(True, None)])

    def test_an_override_lets_the_prompt_through(self):
        from apps.videos.models import ModerationOverride

        ModerationOverride.objects.create(project=self.project, reason="falso positivo")
        with patch(MODERATE) as moderate, patch(PARSER) as parser_cls:
            parser_cls.return_value.parse.return_value = parsed(max_clips=2)
            apply_prompt_to_project(self.project.id)

        moderate.assert_not_called()
        parser_cls.return_value.parse.assert_called_once()


class PromptModerationIngestionTests(IngestionHarness, TestCase):
    def test_a_blocked_prompt_fails_the_project_and_refunds_it(self):
        from apps.core.security import UnsafeContentError
        from apps.payments.services.wallet_service import reserve_funds

        wallet, _ = Wallet.objects.get_or_create(workspace=self.workspace)
        Wallet.objects.filter(pk=wallet.pk).update(available_balance=Decimal("100.00"), reserved_balance=0)
        project = self.make_project(metadata={"pte_prompt": PROMPT, "pte_explicit": []})
        txn = reserve_funds(wallet.id, Decimal("10.00"))
        VideoProject.objects.filter(pk=project.pk).update(
            metadata={**project.metadata, "reservation_id": str(txn.id)}
        )
        self.mock_moderate.side_effect = UnsafeContentError("blocked")

        with patch(PARSER) as parser_cls:
            self.run_task(project)

        parser_cls.return_value.parse.assert_not_called()
        self.mock_transcribe.assert_not_called()
        project.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(project.status, VideoProject.Status.FAILED)
        self.assertEqual(project.pipeline_error_code, "UnsafeContentError")
        self.assertEqual(txn.status, Transaction.Status.FAILED)
