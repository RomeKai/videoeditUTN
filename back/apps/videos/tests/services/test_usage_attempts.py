"""
Billed attempts must never be lost (AICORE-8 review findings).

- Selection: a billed-but-unparseable primary call, followed by a fallback, keeps
  BOTH attempts in ``AIExecutionResult.attempts`` / ``AIError.usage_attempts``.
- Transcription: chunks billed before a later chunk fails travel on the error.
"""

import json
import unittest
from unittest.mock import MagicMock, mock_open, patch

from apps.videos.services.ai.contracts import AIExecutionResult, ClipSelectionResult, ProviderUsage
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIContractValidationError,
    AIError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
    is_billing_uncertain,
    is_retryable_error,
)
from apps.videos.services.ai.pricing import PRICING_VERSION, estimate_asr_cost
from apps.videos.tests.services.test_groq_transcription import (
    _build_provider_with_mock_client,
    _fake_groq_response,
    _make_chunk,
)
from apps.videos.tests.services.test_litellm_selection import (
    _build_provider,
    _make_llm_response,
    _valid_clips_response,
)

SECRET = "SECRET_TRANSCRIPT_X"


def _invalid_json_response(prompt_tokens=1000, completion_tokens=100):
    response = _make_llm_response({}, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    response.choices[0].message.content = f"not json {SECRET}"
    return response


class ContractAdditionsTests(unittest.TestCase):
    def test_execution_result_attempts_default_to_empty_and_are_not_shared(self):
        usage = ProviderUsage(provider="p", model="m")
        first = AIExecutionResult[ClipSelectionResult](data=ClipSelectionResult(), usage=usage)
        second = AIExecutionResult[ClipSelectionResult](data=ClipSelectionResult(), usage=usage)

        self.assertEqual(first.attempts, [])
        first.attempts.append(usage)
        self.assertEqual(second.attempts, [])

    def test_provider_usage_error_code_defaults_to_none(self):
        self.assertIsNone(ProviderUsage(provider="p", model="m").error_code)

    def test_ai_error_usage_attempts_default_to_empty_without_changing_semantics(self):
        for error_cls, retryable in (
            (AIServerError, True),
            (AIRateLimitError, True),
            (AIAuthenticationError, False),
            (AIContractValidationError, False),
        ):
            with self.subTest(error=error_cls.__name__):
                error = error_cls()
                self.assertEqual(error.usage_attempts, [])
                self.assertIsNot(error.usage_attempts, error_cls().usage_attempts)
                self.assertEqual(is_retryable_error(error), retryable)
        self.assertEqual(AIError("m", provider="p").usage_attempts, [])

    def test_only_timeouts_and_connection_failures_leave_the_billing_unknown(self):
        self.assertTrue(is_billing_uncertain(AITimeoutError()))
        self.assertFalse(is_billing_uncertain(AIServerError("5xx")))
        self.assertFalse(is_billing_uncertain(AIAuthenticationError()))
        self.assertFalse(is_billing_uncertain(ValueError("x")))


class SelectionAttemptsTests(unittest.TestCase):
    def _run(self, provider, side_effect):
        import litellm

        with patch.object(litellm, "completion", side_effect=side_effect):
            with patch.object(provider, "_has_fallback", return_value=True):
                return provider.select_clips(transcript=SECRET, video_duration=300.0)

    def test_primary_answers_has_a_single_attempt_equal_to_usage(self):
        provider = _build_provider()

        result = self._run(provider, [_make_llm_response(_valid_clips_response())])

        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0], result.usage)
        self.assertEqual(result.attempts[0].role, "primary")
        self.assertIsNone(result.attempts[0].error_code)

    def test_billed_primary_with_invalid_output_is_kept_when_fallback_answers(self):
        provider = _build_provider(primary="openai/gpt-4o-mini", fallback="gemini/gemini-2.5-flash")

        result = self._run(
            provider,
            [
                _invalid_json_response(prompt_tokens=1000, completion_tokens=100),
                _make_llm_response(_valid_clips_response(), prompt_tokens=800, completion_tokens=50),
            ],
        )

        primary, fallback = result.attempts
        self.assertEqual((primary.role, fallback.role), ("primary", "fallback"))
        self.assertEqual(primary.model, "openai/gpt-4o-mini")
        self.assertEqual((primary.prompt_tokens, primary.completion_tokens), (1000, 100))
        self.assertGreater(primary.estimated_cost_usd, 0)
        self.assertEqual(primary.error_code, "AIContractValidationError")
        self.assertIsNone(fallback.error_code)
        self.assertEqual(result.usage, fallback)
        self.assertEqual(result.usage.role, "fallback")

    def test_unanswered_primary_is_an_unbilled_failed_attempt(self):
        provider = _build_provider()

        result = self._run(
            provider, [AIRateLimitError("slow down"), _make_llm_response(_valid_clips_response())]
        )

        primary, fallback = result.attempts
        self.assertEqual(primary.role, "primary")
        self.assertEqual(primary.error_code, "AIRateLimitError")
        self.assertEqual((primary.prompt_tokens, primary.completion_tokens, primary.total_tokens), (0, 0, 0))
        self.assertEqual(primary.estimated_cost_usd, 0.0)
        self.assertEqual(primary.pricing_version, PRICING_VERSION)
        self.assertEqual(fallback.role, "fallback")

    def test_timed_out_primary_has_unknown_cost_not_zero(self):
        provider = _build_provider()

        result = self._run(
            provider, [TimeoutError("late"), _make_llm_response(_valid_clips_response())]
        )

        primary = result.attempts[0]
        self.assertEqual(primary.error_code, "AITimeoutError")
        self.assertIsNone(primary.estimated_cost_usd)

    def test_all_attempts_failing_attach_every_attempt_to_the_error(self):
        provider = _build_provider()

        with self.assertRaises(AIRateLimitError) as ctx:
            self._run(provider, [_invalid_json_response(), AIRateLimitError("429")])

        attempts = ctx.exception.usage_attempts
        self.assertEqual([a.role for a in attempts], ["primary", "fallback"])
        self.assertEqual(attempts[0].error_code, "AIContractValidationError")
        self.assertGreater(attempts[0].prompt_tokens, 0)
        self.assertEqual(attempts[1].error_code, "AIRateLimitError")
        self.assertEqual(attempts[1].total_tokens, 0)

    def test_no_fallback_still_reports_the_billed_primary(self):
        provider = _build_provider()
        import litellm

        with patch.object(litellm, "completion", return_value=_invalid_json_response()):
            with patch.object(provider, "_has_fallback", return_value=False):
                with self.assertRaises(AIContractValidationError) as ctx:
                    provider.select_clips(transcript=SECRET, video_duration=300.0)

        (attempt,) = ctx.exception.usage_attempts
        self.assertEqual(attempt.role, "primary")
        self.assertGreater(attempt.prompt_tokens, 0)

    def test_attempts_carry_class_names_only_never_provider_text(self):
        provider = _build_provider()

        with self.assertRaises(AIError) as ctx:
            self._run(provider, [_invalid_json_response(), Exception(f"boom {SECRET}")])

        dumped = json.dumps([a.model_dump() for a in ctx.exception.usage_attempts])
        self.assertNotIn(SECRET, dumped)
        self.assertNotIn("boom", dumped)


class TranscriptionPartialUsageTests(unittest.TestCase):
    def _provider(self, side_effect):
        client = MagicMock()
        client.audio.transcriptions.create.side_effect = side_effect
        return _build_provider_with_mock_client(client)

    @staticmethod
    def _response(payload):
        response = MagicMock()
        response.model_dump.return_value = payload
        return response

    def _chunks(self):
        return [
            _make_chunk(index=0, start=0.0, end=600.0),
            _make_chunk(index=1, start=598.0, end=1200.0),
            _make_chunk(index=2, start=1198.0, end=1500.0),
        ]

    def test_failure_after_billed_chunks_attaches_their_cost_and_audio(self):
        provider = self._provider(
            [self._response(_fake_groq_response()), AIServerError("down")]
        )

        with patch("builtins.open", mock_open(read_data=b"audio")):
            with self.assertRaises(AIServerError) as ctx:
                provider.transcribe(self._chunks())

        (partial,) = ctx.exception.usage_attempts
        self.assertEqual(partial.provider, "groq")
        self.assertEqual(partial.model, "whisper-large-v3-turbo")
        self.assertEqual(partial.role, "primary")
        self.assertEqual(partial.audio_seconds, 600.0)
        self.assertAlmostEqual(
            partial.estimated_cost_usd, float(round(estimate_asr_cost("whisper-large-v3-turbo", 600.0), 6))
        )
        self.assertEqual(partial.error_code, "AIServerError")
        self.assertEqual(partial.pricing_version, PRICING_VERSION)
        self.assertGreaterEqual(partial.duration_seconds, 0.0)

    def test_first_chunk_failing_has_nothing_billed_to_attach(self):
        provider = self._provider([AIServerError("down")])

        with patch("builtins.open", mock_open(read_data=b"audio")):
            with self.assertRaises(AIServerError) as ctx:
                provider.transcribe(self._chunks())

        self.assertEqual(ctx.exception.usage_attempts, [])

    def test_billed_chunk_whose_response_cannot_be_parsed_still_counts(self):
        bad = self._response({"words": [{"word": f"x {SECRET}", "start": "oops", "end": 1}]})
        provider = self._provider([self._response(_fake_groq_response()), bad])

        with patch("builtins.open", mock_open(read_data=b"audio")):
            with self.assertRaises(AIContractValidationError) as ctx:
                provider.transcribe(self._chunks())

        (partial,) = ctx.exception.usage_attempts
        self.assertEqual(partial.audio_seconds, 600.0 + 602.0)
        self.assertEqual(partial.error_code, "AIContractValidationError")
        # Only the class name of the parse failure may leave this module.
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertFalse(is_retryable_error(ctx.exception))

    def test_timeout_on_a_later_chunk_makes_the_partial_cost_unknown(self):
        # Named like the Groq SDK classes: the provider classifies by class name.
        class APITimeoutError(Exception):
            pass

        class APIConnectionError(Exception):
            pass

        for sdk_error, expected in (
            (APITimeoutError("slow"), AITimeoutError),
            (APIConnectionError("reset"), AIConnectionError),
        ):
            with self.subTest(error=expected.__name__):
                provider = self._provider([self._response(_fake_groq_response()), sdk_error])

                with patch("builtins.open", mock_open(read_data=b"audio")):
                    with self.assertRaises(expected) as ctx:
                        provider.transcribe(self._chunks())

                (partial,) = ctx.exception.usage_attempts
                # Chunk 1 was billed, but chunk 2 may have been too: not a known cost.
                self.assertIsNone(partial.estimated_cost_usd)
                self.assertEqual(partial.audio_seconds, 600.0)
                self.assertEqual(partial.error_code, expected.__name__)

    def test_non_ai_error_keeps_its_type_and_retry_semantics_and_carries_the_partial(self):
        from apps.videos.tasks import _is_retryable_pipeline_error

        provider = self._provider(
            [self._response(_fake_groq_response()), self._response(_fake_groq_response())]
        )
        boom = OSError("disk hiccup")

        with patch("builtins.open", mock_open(read_data=b"audio")):
            with patch.object(provider, "_apply_offset", side_effect=boom):
                with self.assertRaises(OSError) as ctx:
                    provider.transcribe(self._chunks())

        self.assertIs(ctx.exception, boom)
        self.assertTrue(_is_retryable_pipeline_error(ctx.exception))
        (partial,) = ctx.exception.usage_attempts
        self.assertEqual(partial.audio_seconds, 600.0 + 602.0)
        self.assertEqual(partial.error_code, "OSError")

    def test_celery_soft_time_limit_is_not_rewrapped(self):
        from celery.exceptions import SoftTimeLimitExceeded

        provider = self._provider(
            [self._response(_fake_groq_response()), self._response(_fake_groq_response())]
        )

        with patch("builtins.open", mock_open(read_data=b"audio")):
            with patch.object(provider, "_apply_offset", side_effect=SoftTimeLimitExceeded()):
                with self.assertRaises(SoftTimeLimitExceeded) as ctx:
                    provider.transcribe(self._chunks())

        self.assertEqual(len(ctx.exception.usage_attempts), 1)

    def test_unpriced_model_keeps_cost_unknown_in_the_partial(self):
        provider = self._provider([self._response(_fake_groq_response()), AIServerError("down")])
        provider._model = "whisper-unpriced"

        with patch("builtins.open", mock_open(read_data=b"audio")):
            with self.assertRaises(AIServerError) as ctx:
                provider.transcribe(self._chunks())

        self.assertIsNone(ctx.exception.usage_attempts[0].estimated_cost_usd)

    def test_success_path_is_unchanged(self):
        provider = self._provider(
            [self._response(_fake_groq_response()), self._response(_fake_groq_response())]
        )

        with patch("builtins.open", mock_open(read_data=b"audio")):
            result = provider.transcribe(self._chunks()[:2])

        self.assertEqual(result.usage.audio_seconds, 600.0 + 602.0)
        self.assertEqual(result.attempts, [])
        self.assertIsNone(result.usage.error_code)


if __name__ == "__main__":
    unittest.main()
