"""PTE-01a (#72): ProjectConfig contract and PromptParser on the AI core call path."""

import json
import logging
import typing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.contracts import ProjectConfig
from apps.videos.services.ai.errors import AIAuthenticationError, AIServerError
from apps.videos.services.ai.prompt_parser import (
    PromptParser,
    lenient_config,
    record_prompt_parse_usage,
)

CASES = json.loads(
    (Path(__file__).parent / "golden" / "prompt_parser_cases.json").read_text(encoding="utf-8")
)
PRIMARY = "gemini/gemini-3.8-flash"
FALLBACK = "gemini/gemini-3.5-flash-lite"


def answer(data):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=40),
        model=None,
    )


class FakeLiteLLM:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.cache = None
        self.api_key = "GLOBAL-UNTOUCHED"

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def run_parser(fake, prompt, video_duration=None, keys=None):
    keys = keys if keys is not None else {"gemini": "gemini-key"}
    with patch.dict("sys.modules", {"litellm": fake}), override_settings(
        AI_DEFAULT_LLM_MODEL=PRIMARY, AI_FALLBACK_LLM_MODEL=FALLBACK, AI_LLM_TIMEOUT_SECONDS=30,
    ):
        parser = PromptParser(key_resolver=lambda model: keys.get(model.split("/")[0]))
        return parser.parse(prompt, video_duration)


def status_error(status):
    error = Exception(f"provider error {status}")
    error.status_code = status
    return error


class GoldenPromptTests(SimpleTestCase):
    """Rioplatense prompts with the LLM mocked: what reaches the project for each answer."""

    def test_golden_cases(self):
        self.assertGreaterEqual(len(CASES), 10)
        for case in CASES:
            with self.subTest(case=case["name"]):
                fake = FakeLiteLLM(answer(case["llm"]))

                result = run_parser(fake, case["prompt"], case.get("video_duration"))

                self.assertEqual(result.data.requested(), case["expected"])
                system, user = fake.calls[0]["messages"]
                # The prompt is user data: only inside <user_input>, escaped.
                self.assertNotIn(case["prompt"][:20], system["content"])
                self.assertEqual(user["content"].count("<user_input>"), 1)
                self.assertEqual(user["content"].count("</user_input>"), 1)

    def test_an_empty_prompt_gives_defaults_without_calling_the_llm(self):
        for prompt in ("", "   ", None):
            with self.subTest(prompt=prompt):
                fake = FakeLiteLLM()
                result = run_parser(fake, prompt)
                self.assertEqual(result.data, ProjectConfig())
                self.assertEqual(result.attempts, [])
                self.assertEqual(fake.calls, [])

    def test_the_prompt_is_capped(self):
        fake = FakeLiteLLM(answer({}))
        with override_settings(AI_PROMPT_MAX_CHARS=50):
            run_parser(fake, "a" * 49 + "Z" * 500)
        user = fake.calls[0]["messages"][1]["content"]
        self.assertIn("a" * 49 + "Z", user)
        self.assertNotIn("ZZ", user)


class ContractTests(SimpleTestCase):
    MODEL_CHOICES = {
        "editing_style": VideoProject.EditingStyle,
        "aspect_ratio": VideoProject.AspectRatio,
        "render_layout": VideoProject.Layout,
        "gameplay_position": VideoProject.GameplayPosition,
        "camera_selection_position": VideoProject.CameraSelectionPosition,
        "subtitle_size": VideoProject.SubtitleSize,
        "subtitle_position": VideoProject.SubtitlePosition,
    }

    def test_choices_match_the_model(self):
        for field, choices in self.MODEL_CHOICES.items():
            with self.subTest(field=field):
                annotation = ProjectConfig.model_fields[field].annotation
                literal = next(a for a in typing.get_args(annotation) if a is not type(None))
                self.assertEqual(set(typing.get_args(literal)), set(choices.values))

    def test_only_existing_project_fields_and_metadata_keys(self):
        model_fields = {f.name for f in VideoProject._meta.get_fields()}
        metadata_keys = {"max_clips", "selection_criteria"}
        self.assertLessEqual(set(ProjectConfig.model_fields), model_fields | metadata_keys)

    def test_no_price_plan_tier_or_approval_field(self):
        for name in ProjectConfig.model_fields:
            for forbidden in ("price", "plan", "tier", "cost", "intelligence", "auto_render"):
                self.assertNotIn(forbidden, name)

    def test_invalid_fields_are_dropped_one_by_one(self):
        config, dropped = lenient_config(
            {"aspect_ratio": "4:3", "render_layout": "fit", "max_clips": 0, "subtitle_color": "#00ff00"}
        )
        self.assertEqual(config.requested(), {"render_layout": "fit", "subtitle_color": "#00FF00"})
        self.assertEqual(dropped, 2)

    def test_the_criterion_is_normalized_like_the_api_does(self):
        config, _ = lenient_config({"selection_criteria": "  momentos\nde humor  "})
        self.assertEqual(config.selection_criteria, "momentos de humor")


class CallPathTests(SimpleTestCase):
    def test_key_per_call_timeout_no_litellm_retries_and_schema_from_the_contract(self):
        fake = FakeLiteLLM(answer({"max_clips": 3}))

        result = run_parser(fake, "tres clips")

        call = fake.calls[0]
        self.assertEqual(call["api_key"], "gemini-key")
        self.assertEqual(fake.api_key, "GLOBAL-UNTOUCHED")
        self.assertEqual(call["timeout"], 30.0)
        self.assertEqual(call["num_retries"], 0)
        schema = call["response_format"]["response_schema"]
        self.assertEqual(set(schema["properties"]), set(ProjectConfig.model_fields))
        self.assertEqual([a.role for a in result.attempts], ["primary"])
        self.assertEqual(result.usage.prompt_tokens, 200)

    def test_a_server_error_falls_back_once(self):
        fake = FakeLiteLLM(status_error(503), answer({"max_clips": 2}))

        result = run_parser(fake, "dos clips")

        self.assertEqual([c["model"] for c in fake.calls], [PRIMARY, FALLBACK])
        self.assertEqual(result.data.max_clips, 2)
        self.assertEqual([a.role for a in result.attempts], ["primary", "fallback"])

    def test_an_answer_that_is_not_json_goes_to_the_fallback(self):
        not_json = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="no es json"))],
            usage=SimpleNamespace(prompt_tokens=200, completion_tokens=5), model=None,
        )
        fake = FakeLiteLLM(not_json, answer({}))

        result = run_parser(fake, "algo")

        self.assertEqual(result.attempts[0].error_code, "AIContractValidationError")
        self.assertEqual(result.attempts[0].prompt_tokens, 200)  # billed: real usage kept

    def test_both_failing_raise_a_classified_error_with_every_attempt(self):
        fake = FakeLiteLLM(status_error(503), status_error(503))

        with self.assertRaises(AIServerError) as ctx:
            run_parser(fake, "algo")
        self.assertEqual([a.role for a in ctx.exception.usage_attempts], ["primary", "fallback"])

    def test_without_keys_nothing_is_called(self):
        fake = FakeLiteLLM()
        with self.assertRaises(AIAuthenticationError):
            run_parser(fake, "algo", keys={})
        self.assertEqual(fake.calls, [])

    def test_logs_carry_neither_the_prompt_nor_the_answer(self):
        secret_prompt = "PROMPT-SECRETO-X momentos de humor"
        fake = FakeLiteLLM(answer({"selection_criteria": "RESPUESTA-SECRETA-X", "aspect_ratio": "4:3"}))
        with self.assertLogs("apps.videos.services.ai", level="DEBUG") as logs:
            run_parser(fake, secret_prompt)
        output = "\n".join(logs.output)
        self.assertNotIn("PROMPT-SECRETO-X", output)
        self.assertNotIn("RESPUESTA-SECRETA-X", output)
        self.assertNotIn("4:3", output)


class UsageRecordTests(TestCase):
    def setUp(self):
        from apps.users.models import Workspace

        user = get_user_model().objects.create_user(username="pp", email="pp@test.com", password="pw")
        workspace = Workspace.objects.create(name="PP", owner=user)
        self.project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="P")

    def test_every_attempt_is_recorded_with_the_prompt_parse_stage(self):
        fake = FakeLiteLLM(status_error(503), answer({"max_clips": 2}))
        result = run_parser(fake, "dos clips")

        written = record_prompt_parse_usage(self.project.pk, result)

        rows = AIUsageRecord.objects.filter(project=self.project)
        self.assertEqual(written, 2)
        self.assertEqual(set(rows.values_list("stage", flat=True)), {"prompt_parse"})
        self.assertEqual(sorted(rows.values_list("role", flat=True)), ["fallback", "primary"])

    def test_a_failed_parse_records_its_attempts_too(self):
        fake = FakeLiteLLM(status_error(503), status_error(503))
        try:
            run_parser(fake, "algo")
        except AIServerError as exc:
            written = record_prompt_parse_usage(self.project.pk, exc)
        self.assertEqual(written, 2)
        self.assertFalse(AIUsageRecord.objects.filter(project=self.project, success=True).exists())

    def test_an_empty_prompt_records_nothing(self):
        result = run_parser(FakeLiteLLM(), "  ")
        self.assertEqual(record_prompt_parse_usage(self.project.pk, result), 0)
