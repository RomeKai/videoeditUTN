"""
Unit tests for AI Core contracts, error hierarchy, and configuration.
Ensures validation logic, schema constraints, retryability rules, and feature flags work as expected.
"""

import os
import sys
import unittest
from pydantic import ValidationError

# Ensure project root is on sys.path and DJANGO_SETTINGS_MODULE is set
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACK_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if BACK_DIR not in sys.path:
    sys.path.insert(0, BACK_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings.base")

import django
try:
    django.setup()
except Exception:
    pass

from django.conf import settings
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    TranscriptionResult,
    TranscriptionSegment,
    ViralClip,
    WordTimestamp,
)
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIContentFilterError,
    AIContractValidationError,
    AIError,
    AIInvalidRequestError,
    AIQuotaExceededError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
    NonRetryableAIError,
    RetryableAIError,
    is_retryable_error,
)


class TestTranscriptionContracts(unittest.TestCase):
    """Tests for word and segment transcription contracts."""

    def test_word_timestamp_valid(self):
        word = WordTimestamp(text="hola", start=1.2, end=1.8, confidence=0.98)
        self.assertEqual(word.text, "hola")
        self.assertEqual(word.start, 1.2)
        self.assertEqual(word.end, 1.8)
        self.assertAlmostEqual(word.duration, 0.6)
        self.assertEqual(word.confidence, 0.98)

    def test_word_timestamp_whitespace_stripped(self):
        word = WordTimestamp(text="  mundo  ", start=2.0, end=2.5)
        self.assertEqual(word.text, "mundo")

    def test_word_timestamp_invalid_range(self):
        with self.assertRaises(ValidationError):
            WordTimestamp(text="error", start=3.0, end=2.0)

    def test_word_timestamp_negative_time(self):
        with self.assertRaises(ValidationError):
            WordTimestamp(text="error", start=-1.0, end=2.0)

    def test_transcription_segment_with_words(self):
        w1 = WordTimestamp(text="Este", start=0.0, end=0.4)
        w2 = WordTimestamp(text="clip", start=0.4, end=0.9)
        segment = TranscriptionSegment(start=0.0, end=0.9, text="Este clip", words=[w1, w2])
        self.assertEqual(len(segment.words), 2)
        self.assertAlmostEqual(segment.duration, 0.9)
        self.assertFalse(segment.is_emoji)

    def test_transcription_segment_invalid_times(self):
        with self.assertRaises(ValidationError):
            TranscriptionSegment(start=5.0, end=4.0, text="Invalid")

    def test_transcription_result_filtering(self):
        w1 = WordTimestamp(text="Inicio", start=0.0, end=1.0)
        w2 = WordTimestamp(text="Medio", start=5.0, end=6.0)
        w3 = WordTimestamp(text="Fin", start=15.0, end=16.0)

        result = TranscriptionResult(
            full_text="Inicio Medio Fin",
            words=[w1, w2, w3],
            language="es",
            duration=16.0,
        )
        self.assertEqual(result.word_count, 3)

        filtered = result.filter_by_timerange(4.0, 10.0)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].text, "Medio")


class TestViralClipContracts(unittest.TestCase):
    """Tests for clip selection contracts."""

    def test_viral_clip_valid(self):
        clip = ViralClip(
            start=10.0,
            end=45.0,
            title="Momento épico del podcast",
            virality_score=92.5,
            reasoning="Gran tensión dramática y remate cómico.",
            hook_text="No vas a creer esto...",
            hashtags=["#podcast", "#viral"],
        )
        self.assertEqual(clip.title, "Momento épico del podcast")
        self.assertAlmostEqual(clip.duration, 35.0)
        self.assertEqual(clip.virality_score, 92.5)

    def test_viral_clip_invalid_virality_bounds(self):
        with self.assertRaises(ValidationError):
            ViralClip(
                start=0.0,
                end=10.0,
                title="Bad score",
                virality_score=150.0,  # Max is 100
                reasoning="Exceeds bounds",
            )

        with self.assertRaises(ValidationError):
            ViralClip(
                start=0.0,
                end=10.0,
                title="Negative score",
                virality_score=-5.0,  # Min is 0
                reasoning="Negative bounds",
            )

    def test_viral_clip_end_before_start(self):
        with self.assertRaises(ValidationError):
            ViralClip(
                start=20.0,
                end=15.0,
                title="Inverted time",
                virality_score=80.0,
                reasoning="Impossible timestamps",
            )

    def test_viral_clip_zero_duration(self):
        with self.assertRaises(ValidationError):
            ViralClip(
                start=10.0,
                end=10.0,
                title="Zero duration",
                virality_score=80.0,
                reasoning="Zero length clip",
            )

    def test_clip_selection_result(self):
        c1 = ViralClip(
            start=0.0,
            end=20.0,
            title="Hook inicial",
            virality_score=88.0,
            reasoning="Captura atención inmediata.",
        )
        selection = ClipSelectionResult(
            clips=[c1],
            project_title="Episodio 1",
            editing_style="dynamic",
            target_count=1,
        )
        self.assertEqual(len(selection.clips), 1)
        self.assertEqual(selection.clips[0].title, "Hook inicial")


class TestProviderUsageContracts(unittest.TestCase):
    """Tests for token and duration accounting metrics."""

    def test_provider_usage_total_tokens_computed(self):
        usage = ProviderUsage(
            provider="gemini",
            model="gemini-2.0-flash",
            prompt_tokens=1500,
            completion_tokens=250,
            duration_seconds=1.42,
            estimated_cost_usd=0.00025,
        )
        self.assertEqual(usage.total_tokens, 1750)
        self.assertFalse(usage.cached)

    def test_provider_usage_negative_tokens_rejected(self):
        with self.assertRaises(ValidationError):
            ProviderUsage(
                provider="groq",
                model="whisper-large-v3-turbo",
                prompt_tokens=-10,
            )

    def test_ai_execution_result_envelope(self):
        usage = ProviderUsage(provider="groq", model="whisper-large-v3-turbo", duration_seconds=3.2)
        exec_result = AIExecutionResult[str](data="transcripción de prueba", usage=usage, success=True)
        self.assertTrue(exec_result.success)
        self.assertEqual(exec_result.data, "transcripción de prueba")
        self.assertEqual(exec_result.usage.provider, "groq")


class TestAIErrorHierarchy(unittest.TestCase):
    """Tests for distinguishing retryable vs non-retryable errors."""

    def test_retryable_errors(self):
        rate_limit = AIRateLimitError("Too many requests", retry_after=30.0, provider="groq")
        self.assertTrue(rate_limit.is_retryable)
        self.assertTrue(isinstance(rate_limit, RetryableAIError))
        self.assertEqual(rate_limit.retry_after, 30.0)
        self.assertTrue(is_retryable_error(rate_limit))

        timeout = AITimeoutError("Request timed out", provider="gemini")
        self.assertTrue(timeout.is_retryable)
        self.assertTrue(is_retryable_error(timeout))

        server_err = AIServerError("Internal server error 503", status_code=503)
        self.assertTrue(server_err.is_retryable)
        self.assertTrue(is_retryable_error(server_err))

        conn_err = AIConnectionError("Connection reset by peer")
        self.assertTrue(conn_err.is_retryable)
        self.assertTrue(is_retryable_error(conn_err))

    def test_non_retryable_errors(self):
        auth_err = AIAuthenticationError("Invalid API key", provider="openai")
        self.assertFalse(auth_err.is_retryable)
        self.assertTrue(isinstance(auth_err, NonRetryableAIError))
        self.assertFalse(is_retryable_error(auth_err))

        invalid_req = AIInvalidRequestError("Malformed prompt payload", status_code=400)
        self.assertFalse(invalid_req.is_retryable)
        self.assertFalse(is_retryable_error(invalid_req))

        safety_err = AIContentFilterError("Hate speech detected", safety_ratings={"HARM": "HIGH"})
        self.assertFalse(safety_err.is_retryable)
        self.assertEqual(safety_err.safety_ratings["HARM"], "HIGH")
        self.assertFalse(is_retryable_error(safety_err))

        quota_err = AIQuotaExceededError("Credit balance is zero")
        self.assertFalse(quota_err.is_retryable)
        self.assertFalse(is_retryable_error(quota_err))

        contract_err = AIContractValidationError("Missing 'clips' key in response")
        self.assertFalse(contract_err.is_retryable)
        self.assertFalse(is_retryable_error(contract_err))

    def test_is_retryable_standard_exceptions(self):
        self.assertTrue(is_retryable_error(TimeoutError("socket timeout")))
        self.assertTrue(is_retryable_error(ConnectionError("host unreachable")))
        self.assertFalse(is_retryable_error(ValueError("bad value")))


class TestSettingsAndFeatureFlags(unittest.TestCase):
    """Tests for settings, feature flags, and environment configurations."""

    def test_feature_flag_defaults_to_false(self):
        self.assertFalse(
            getattr(settings, "AI_CORE_V2_ENABLED", None),
            "AI_CORE_V2_ENABLED must default to False during the pilot phase.",
        )

    def test_ai_settings_configured(self):
        self.assertTrue(hasattr(settings, "OPENAI_API_KEY"))
        self.assertTrue(hasattr(settings, "GEMINI_API_KEY"))
        self.assertTrue(hasattr(settings, "GROQ_API_KEY"))
        self.assertTrue(hasattr(settings, "AI_DEFAULT_LLM_MODEL"))
        self.assertTrue(hasattr(settings, "AI_FALLBACK_LLM_MODEL"))
        self.assertTrue(hasattr(settings, "AI_DEFAULT_TRANSCRIPTION_MODEL"))


if __name__ == "__main__":
    unittest.main()
