"""
AICORE-14 (#66): capped reasoning for the selection LLM (ADR-012).

The request/response tests run through the pinned LiteLLM's real Gemini
transformation; only its HTTP POST is replaced, so nothing leaves the process.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import litellm
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from apps.videos.services.ai.contracts import ProviderUsage
from apps.videos.services.ai.litellm_selection import LiteLLMSelectionProvider
from apps.videos.services.ai.llm_options import (
    REASONING_EFFORTS,
    reasoning_effort_setting,
    reasoning_kwargs,
)
from apps.videos.services.ai.pricing import estimate_llm_cost
from apps.videos.services.selection_engine import GeminiFlashStrategy, SelectionEngine

PRIMARY = "gemini/gemini-3.8-flash"
FALLBACK = "gemini/gemini-3.5-flash-lite"
SEGMENTS = [{"start": 0.0, "end": 30.0, "text": "hola a todos"}]
ANSWER = json.dumps(
    {"clips": [{"start": 0.0, "end": 20.0, "title": "t", "virality_score": 80, "reasoning": "r"}]}
)
MODELS = dict(
    GEMINI_API_KEY="g-key", OPENAI_API_KEY="o-key",
    AI_DEFAULT_LLM_MODEL=PRIMARY, AI_FALLBACK_LLM_MODEL=FALLBACK,
)


def gemini_body(text=ANSWER, prompt=100, candidates=50, thoughts=200):
    return {
        "candidates": [{
            "content": {"parts": [{"text": text}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }],
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": candidates,
            "thoughtsTokenCount": thoughts,
            "totalTokenCount": prompt + candidates + thoughts,
        },
        "modelVersion": "gemini-3.8-flash",
    }


class FakeGemini:
    """Stands in for LiteLLM's HTTP POST: records the body, answers like Gemini."""

    def __init__(self, body=None):
        self.body = body or gemini_body()
        self.requests = []

    def patch(self):
        fake = self

        def post(handler, url, data=None, json=None, *args, **kwargs):
            payload = json if json is not None else globals()["json"].loads(data)
            fake.requests.append(payload)
            return httpx.Response(200, json=fake.body, request=httpx.Request("POST", url))

        return patch.object(HTTPHandler, "post", post)

    @property
    def thinking_config(self):
        return self.requests[-1].get("generationConfig", {}).get("thinkingConfig")


class ReasoningSettingTests(SimpleTestCase):
    def test_unset_means_provider_default(self):
        for value in (None, "", "   "):
            with self.subTest(value=value), override_settings(AI_LLM_REASONING_EFFORT=value):
                self.assertIsNone(reasoning_effort_setting())

    def test_valid_values_are_normalized(self):
        for value in REASONING_EFFORTS:
            with self.subTest(value=value), override_settings(AI_LLM_REASONING_EFFORT=f" {value.upper()} "):
                self.assertEqual(reasoning_effort_setting(), value)

    @override_settings(AI_LLM_REASONING_EFFORT="maximum")
    def test_invalid_value_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            reasoning_effort_setting()

    def test_only_models_that_can_reason_receive_the_parameter(self):
        self.assertEqual(reasoning_kwargs(PRIMARY, "low"), {"reasoning_effort": "low"})
        self.assertEqual(reasoning_kwargs("openai/gpt-4o-mini", "low"), {})
        self.assertEqual(reasoning_kwargs("unknown/model-x", "low"), {})
        self.assertEqual(reasoning_kwargs(PRIMARY, None), {})


@override_settings(**MODELS)
class ProviderRequestTests(SimpleTestCase):
    """Through LiteLLM's real Gemini transformation (pinned version)."""

    def select(self, fake):
        with fake.patch():
            return LiteLLMSelectionProvider().select_clips(
                transcript="hola", video_duration=30.0, segments=SEGMENTS
            )

    @override_settings(AI_LLM_REASONING_EFFORT="low")
    def test_effort_reaches_gemini_as_thinking_level_without_thought_summaries(self):
        fake = FakeGemini()
        self.select(fake)
        self.assertEqual(fake.thinking_config, {"thinkingLevel": "low", "includeThoughts": False})

    @override_settings(AI_LLM_REASONING_EFFORT="")
    def test_unset_effort_sends_no_thinking_config(self):
        fake = FakeGemini()
        self.select(fake)
        self.assertIsNone(fake.thinking_config)

    @override_settings(AI_LLM_REASONING_EFFORT="")
    def test_reasoning_tokens_are_billed_as_output(self):
        # 50 visible + 200 reasoning tokens. LiteLLM 1.108 reports 250 completion
        # tokens with 200 of them in completion_tokens_details; the usage keeps
        # them apart (reasoning_tokens, #61) and bills all 250 as output.
        result = self.select(FakeGemini(gemini_body(prompt=100, candidates=50, thoughts=200)))

        usage = result.usage
        self.assertEqual((usage.prompt_tokens, usage.completion_tokens), (100, 50))
        self.assertEqual(usage.reasoning_tokens, 200)
        self.assertEqual(usage.total_tokens, 350)
        expected = estimate_llm_cost(
            PRIMARY, ProviderUsage(provider="gemini", model=PRIMARY, prompt_tokens=100, completion_tokens=250)
        )
        self.assertAlmostEqual(usage.estimated_cost_usd, float(expected), places=9)
        self.assertEqual(len(result.data.clips), 1)

    @override_settings(AI_LLM_REASONING_EFFORT="minimal")
    def test_minimal_maps_to_the_lowest_level_the_model_supports(self):
        fake = FakeGemini()
        self.select(fake)
        self.assertEqual(fake.thinking_config["thinkingLevel"], "low")

    @override_settings(AI_LLM_REASONING_EFFORT="extreme")
    def test_invalid_effort_fails_before_any_call(self):
        fake = FakeGemini()
        with self.assertRaises(ImproperlyConfigured):
            self.select(fake)
        self.assertEqual(fake.requests, [])


@override_settings(**MODELS, AI_LLM_REASONING_EFFORT="high")
class ProviderKwargsTests(SimpleTestCase):
    def response(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = ANSWER
        response.usage.prompt_tokens = 10
        response.usage.completion_tokens = 5
        response.usage.cache_read_input_tokens = 0
        return response

    def test_primary_and_fallback_both_receive_the_effort(self):
        bad = MagicMock()
        bad.choices = [MagicMock()]
        bad.choices[0].message.content = "not json"
        with patch.object(litellm, "completion", side_effect=[bad, self.response()]) as completion:
            LiteLLMSelectionProvider().select_clips(
                transcript="hola", video_duration=30.0, segments=SEGMENTS
            )
        models = [call.kwargs["model"] for call in completion.call_args_list]
        efforts = [call.kwargs.get("reasoning_effort") for call in completion.call_args_list]
        self.assertEqual(models, [PRIMARY, FALLBACK])
        self.assertEqual(efforts, ["high", "high"])

    @override_settings(AI_FALLBACK_LLM_MODEL="openai/gpt-4o-mini")
    def test_a_fallback_that_cannot_reason_gets_no_effort(self):
        bad = MagicMock()
        bad.choices = [MagicMock()]
        bad.choices[0].message.content = "not json"
        with patch.object(litellm, "completion", side_effect=[bad, self.response()]) as completion:
            LiteLLMSelectionProvider().select_clips(
                transcript="hola", video_duration=30.0, segments=SEGMENTS
            )
        self.assertNotIn("reasoning_effort", completion.call_args_list[1].kwargs)


@override_settings(**MODELS, AI_CORE_V2_ENABLED=False)
class LegacyReasoningTests(SimpleTestCase):
    def test_legacy_call_receives_the_effort(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = ANSWER
        with override_settings(AI_LLM_REASONING_EFFORT="low"), \
                patch.object(litellm, "completion", return_value=response) as completion:
            GeminiFlashStrategy().select_clips("prompt")
        self.assertEqual(completion.call_args.kwargs["reasoning_effort"], "low")

    @override_settings(AI_LLM_REASONING_EFFORT="bogus")
    def test_legacy_invalid_effort_fails_before_the_call(self):
        with patch.object(litellm, "completion") as completion:
            with self.assertRaises(ImproperlyConfigured):
                SelectionEngine.select_viral_clips(
                    transcription_data={"full_text": "hola", "segments": SEGMENTS},
                    project_title="p", duration=30.0,
                )
        completion.assert_not_called()
