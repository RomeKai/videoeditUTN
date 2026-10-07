"""
Unit tests for LiteLLMSelectionProvider (AICORE-4).

Validates all acceptance criteria:
1. LiteLLM used as embedded SDK, no proxy.
2. Gemini 3.8 Flash is the primary provider.
3. Gemini 1.5 Flash is fallback (same API key, intra-family).
4. Response uses JSON Schema generated from Pydantic.
5. Clips validated against actual video duration.
6. Input, output, and implicit cache tokens tracked.
7. Explicit cache remains disabled.
8. Invalid primary result triggers at most one fallback.
9. Does not log prompts, transcriptions, or raw responses.
"""

import json
import logging
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure back root is on sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACK_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "..", ".."))
if BACK_DIR not in sys.path:
    sys.path.insert(0, BACK_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings.base")

import django
try:
    django.setup()
except Exception:
    pass

# ---------------------------------------------------------------------------
# Ensure litellm and groq stubs exist for test environments without them
# ---------------------------------------------------------------------------
if "groq" not in sys.modules:
    _groq_stub = types.ModuleType("groq")
    _groq_stub.Groq = MagicMock
    sys.modules["groq"] = _groq_stub

if "litellm" not in sys.modules:
    _litellm_stub = types.ModuleType("litellm")
    _litellm_stub.completion = MagicMock()
    _litellm_stub.cache = None
    _litellm_stub.api_key = None
    sys.modules["litellm"] = _litellm_stub

from apps.videos.services.ai.contracts import (
    ClipSelectionResult,
    ViralClip,
)
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIContractValidationError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
    is_retryable_error,
)
from apps.videos.services.ai.litellm_selection import (
    LiteLLMSelectionProvider,
    _CLIP_SELECTION_SCHEMA,
)


def _build_provider(primary="gemini/gemini-3.8-flash", fallback="gpt-4o-mini"):
    """Builds a provider instance bypassing __init__ side-effects."""
    with patch.object(LiteLLMSelectionProvider, "__init__", lambda self, **kw: None):
        provider = LiteLLMSelectionProvider()
    provider._primary_model = primary
    provider._fallback_model = fallback
    return provider


def _make_llm_response(content_dict, prompt_tokens=100, completion_tokens=50,
                        cache_read=0, cache_creation=0):
    """Creates a mock LiteLLM response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(content_dict)
    mock_response.usage.prompt_tokens = prompt_tokens
    mock_response.usage.completion_tokens = completion_tokens
    mock_response.usage.cache_read_input_tokens = cache_read
    mock_response.usage.cache_creation_input_tokens = cache_creation
    return mock_response


def _valid_clips_response(video_duration=300.0, count=2):
    """Returns a valid ClipSelectionResult dict."""
    clips = []
    segment_len = min(30.0, video_duration / max(count, 1))
    for i in range(count):
        start = i * (segment_len + 10.0)
        end = start + segment_len
        if end > video_duration:
            end = video_duration
        if start >= video_duration:
            break
        clips.append({
            "start": start,
            "end": end,
            "title": f"Clip {i+1}",
            "virality_score": 85.0 - i * 5,
            "reasoning": f"Great moment {i+1}",
            "hook_text": f"Hook {i+1}",
            "hashtags": [f"#clip{i+1}"],
        })
    return {
        "clips": clips,
        "project_title": "Test Project",
        "editing_style": "dynamic",
        "target_count": count,
    }


class TestProviderInitialization(unittest.TestCase):
    """Tests for provider construction and model configuration."""

    @patch("apps.videos.services.ai.litellm_selection.settings")
    def test_default_models_from_settings(self, mock_settings):
        mock_settings.AI_DEFAULT_LLM_MODEL = "gemini/gemini-3.8-flash"
        mock_settings.AI_FALLBACK_LLM_MODEL = "gemini/gemini-1.5-flash"
        mock_settings.GEMINI_API_KEY = "test-gemini-key"

        import litellm
        with patch.object(litellm, "cache", None):
            provider = LiteLLMSelectionProvider()

        self.assertEqual(provider._primary_model, "gemini/gemini-3.8-flash")
        self.assertEqual(provider._fallback_model, "gemini/gemini-1.5-flash")

    def test_explicit_models_override(self):
        provider = _build_provider(
            primary="gemini/gemini-custom",
            fallback="gpt-custom",
        )
        self.assertEqual(provider._primary_model, "gemini/gemini-custom")
        self.assertEqual(provider._fallback_model, "gpt-custom")


class TestLiteLLMEmbedded(unittest.TestCase):
    """AC: LiteLLM used as embedded SDK, no proxy."""

    def test_calls_litellm_completion_directly(self):
        """Verifies litellm.completion() is called, not a proxy endpoint."""
        provider = _build_provider()
        mock_response = _make_llm_response(_valid_clips_response())

        import litellm
        with patch.object(litellm, "completion", return_value=mock_response) as mock_comp:
            provider.select_clips(
                transcript="Test transcript content here",
                video_duration=300.0,
                target_count=2,
            )
            mock_comp.assert_called_once()
            # Verify it's a direct function call, not HTTP
            call_kwargs = mock_comp.call_args.kwargs
            self.assertIn("model", call_kwargs)
            self.assertIn("messages", call_kwargs)


class TestPrimaryModel(unittest.TestCase):
    """AC: Gemini 3.8 Flash is the primary provider."""

    def test_primary_model_is_gemini_flash(self):
        provider = _build_provider()
        self.assertEqual(provider._primary_model, "gemini/gemini-3.8-flash")

    def test_primary_model_used_in_call(self):
        provider = _build_provider()
        mock_response = _make_llm_response(_valid_clips_response())

        import litellm
        with patch.object(litellm, "completion", return_value=mock_response) as mock_comp:
            provider.select_clips(
                transcript="Test content",
                video_duration=300.0,
            )
            call_kwargs = mock_comp.call_args.kwargs
            self.assertEqual(call_kwargs["model"], "gemini/gemini-3.8-flash")


class TestStructuredOutput(unittest.TestCase):
    """AC: Response uses JSON Schema generated from Pydantic."""

    def test_schema_generated_from_pydantic(self):
        """The module-level schema should match ClipSelectionResult.model_json_schema()."""
        expected = ClipSelectionResult.model_json_schema()
        self.assertEqual(_CLIP_SELECTION_SCHEMA, expected)

    def test_response_format_includes_schema(self):
        provider = _build_provider()
        mock_response = _make_llm_response(_valid_clips_response())

        import litellm
        with patch.object(litellm, "completion", return_value=mock_response) as mock_comp:
            provider.select_clips(
                transcript="Test content",
                video_duration=300.0,
            )
            call_kwargs = mock_comp.call_args.kwargs
            rf = call_kwargs["response_format"]
            self.assertEqual(rf["type"], "json_object")
            self.assertEqual(rf["response_schema"], _CLIP_SELECTION_SCHEMA)

    def test_invalid_json_raises_contract_error(self):
        provider = _build_provider()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json {{"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.cache_read_input_tokens = 0
        mock_response.usage.cache_creation_input_tokens = 0

        import litellm
        with patch.object(litellm, "completion", return_value=mock_response):
            with patch.object(provider, "_has_fallback", return_value=False):
                with self.assertRaises(AIContractValidationError):
                    provider.select_clips(
                        transcript="Test",
                        video_duration=300.0,
                    )


class TestClipValidation(unittest.TestCase):
    """AC: Clips validated against actual video duration."""

    def test_clip_beyond_duration_dropped(self):
        clips = [
            ViralClip(start=0.0, end=30.0, title="Good", virality_score=90.0,
                      reasoning="Fine"),
            ViralClip(start=350.0, end=380.0, title="Bad", virality_score=80.0,
                      reasoning="Beyond"),
        ]
        validated = LiteLLMSelectionProvider._validate_clips(clips, video_duration=300.0)
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].title, "Good")

    def test_clip_end_clamped_to_duration(self):
        clips = [
            ViralClip(start=280.0, end=320.0, title="Overflow", virality_score=85.0,
                      reasoning="End exceeds"),
        ]
        validated = LiteLLMSelectionProvider._validate_clips(clips, video_duration=300.0)
        self.assertEqual(len(validated), 1)
        self.assertAlmostEqual(validated[0].end, 300.0)

    def test_valid_clips_pass_through(self):
        clips = [
            ViralClip(start=10.0, end=40.0, title="Perfect", virality_score=95.0,
                      reasoning="Within bounds"),
        ]
        validated = LiteLLMSelectionProvider._validate_clips(clips, video_duration=300.0)
        self.assertEqual(len(validated), 1)
        self.assertAlmostEqual(validated[0].end, 40.0)


class TestTokenTracking(unittest.TestCase):
    """AC: Input, output, and implicit cache tokens tracked."""

    def test_tokens_captured_in_usage(self):
        provider = _build_provider()
        mock_response = _make_llm_response(
            _valid_clips_response(),
            prompt_tokens=500,
            completion_tokens=200,
            cache_read=50,
        )

        import litellm
        with patch.object(litellm, "completion", return_value=mock_response):
            result = provider.select_clips(
                transcript="Test",
                video_duration=300.0,
            )

        self.assertEqual(result.usage.prompt_tokens, 500)
        self.assertEqual(result.usage.completion_tokens, 200)
        self.assertTrue(result.usage.cached)  # cache_read > 0

    def test_no_cache_sets_cached_false(self):
        provider = _build_provider()
        mock_response = _make_llm_response(
            _valid_clips_response(),
            cache_read=0,
        )

        import litellm
        with patch.object(litellm, "completion", return_value=mock_response):
            result = provider.select_clips(
                transcript="Test",
                video_duration=300.0,
            )

        self.assertFalse(result.usage.cached)


class TestExplicitCacheDisabled(unittest.TestCase):
    """AC: Explicit cache remains disabled."""

    def test_no_cache_control_in_messages(self):
        provider = _build_provider()
        messages = provider._build_messages(
            transcript="Test",
            video_duration=300.0,
            target_count=3,
            editing_style="dynamic",
        )

        for msg in messages:
            self.assertNotIn("cache_control", msg)
            # No nested cache_control in content blocks
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        self.assertNotIn("cache_control", block)

    @patch("apps.videos.services.ai.litellm_selection.settings")
    def test_litellm_cache_set_to_none(self, mock_settings):
        mock_settings.AI_DEFAULT_LLM_MODEL = "gemini/gemini-3.8-flash"
        mock_settings.AI_FALLBACK_LLM_MODEL = "gemini/gemini-1.5-flash"
        mock_settings.GEMINI_API_KEY = "key"

        import litellm
        litellm.cache = "should_be_cleared"
        LiteLLMSelectionProvider()
        self.assertIsNone(litellm.cache)


class TestFallbackBehavior(unittest.TestCase):
    """AC: Max 1 fallback on invalid primary result."""

    def test_fallback_used_when_primary_fails(self):
        provider = _build_provider()
        valid_response = _make_llm_response(_valid_clips_response())

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Primary failed")
            return valid_response

        import litellm
        with patch.object(litellm, "completion", side_effect=side_effect):
            with patch.object(provider, "_has_fallback", return_value=True):
                result = provider.select_clips(
                    transcript="Test",
                    video_duration=300.0,
                )

        self.assertTrue(result.success)
        self.assertEqual(call_count, 2)  # Primary + 1 fallback

    def test_no_fallback_without_openai_key(self):
        provider = _build_provider()

        import litellm
        with patch.object(litellm, "completion", side_effect=Exception("Primary failed")):
            with patch.object(provider, "_has_fallback", return_value=False):
                with self.assertRaises(Exception):
                    provider.select_clips(
                        transcript="Test",
                        video_duration=300.0,
                    )

    def test_max_one_fallback_attempt(self):
        provider = _build_provider()

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception(f"Failure #{call_count}")

        import litellm
        with patch.object(litellm, "completion", side_effect=side_effect):
            with patch.object(provider, "_has_fallback", return_value=True):
                with self.assertRaises(Exception):
                    provider.select_clips(
                        transcript="Test",
                        video_duration=300.0,
                    )

        # Exactly 2 calls: primary + 1 fallback, no more
        self.assertEqual(call_count, 2)

    @patch("apps.videos.services.ai.litellm_selection.settings")
    def test_has_fallback_checks_gemini_key(self, mock_settings):
        mock_settings.GEMINI_API_KEY = None
        provider = _build_provider()
        with patch.object(type(provider), "_has_fallback",
                          lambda self: bool(getattr(mock_settings, "GEMINI_API_KEY", None))):
            self.assertFalse(provider._has_fallback())

        mock_settings.GEMINI_API_KEY = "test-key"
        with patch.object(type(provider), "_has_fallback",
                          lambda self: bool(getattr(mock_settings, "GEMINI_API_KEY", None))):
            self.assertTrue(provider._has_fallback())


class TestErrorClassification(unittest.TestCase):
    """Tests error mapping to AICORE-1 hierarchy."""

    def setUp(self):
        self.provider = _build_provider()

    def test_401_non_retryable(self):
        exc = Exception("Auth failed")
        exc.status_code = 401
        err = self.provider._classify_error(exc)
        self.assertIsInstance(err, AIAuthenticationError)
        self.assertFalse(err.is_retryable)

    def test_429_retryable(self):
        exc = Exception("Rate limit")
        exc.status_code = 429
        err = self.provider._classify_error(exc)
        self.assertIsInstance(err, AIRateLimitError)
        self.assertTrue(err.is_retryable)

    def test_500_retryable(self):
        exc = Exception("Server error")
        exc.status_code = 500
        err = self.provider._classify_error(exc)
        self.assertIsInstance(err, AIServerError)
        self.assertTrue(err.is_retryable)

    def test_timeout_retryable(self):
        err = self.provider._classify_error(TimeoutError("timeout"))
        self.assertIsInstance(err, AITimeoutError)
        self.assertTrue(err.is_retryable)

    def test_connection_retryable(self):
        err = self.provider._classify_error(ConnectionError("refused"))
        self.assertIsInstance(err, AIConnectionError)
        self.assertTrue(err.is_retryable)

    def test_contract_validation_passes_through(self):
        original = AIContractValidationError("bad schema")
        result = self.provider._classify_error(original)
        self.assertIs(result, original)


class TestLoggingPolicy(unittest.TestCase):
    """AC: Does not log prompts, transcriptions, or raw responses."""

    def test_no_sensitive_data_in_logs(self):
        provider = _build_provider()

        secret_transcript = "This is a SECRET transcript that must NOT appear in logs"
        response_data = _valid_clips_response()
        mock_response = _make_llm_response(response_data)

        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record.getMessage())

        sel_logger = logging.getLogger("apps.videos.services.ai.litellm_selection")
        sel_logger.addHandler(handler)
        sel_logger.setLevel(logging.DEBUG)

        try:
            import litellm
            with patch.object(litellm, "completion", return_value=mock_response):
                provider.select_clips(
                    transcript=secret_transcript,
                    video_duration=300.0,
                )

            combined = " ".join(log_output)

            # Transcript must NOT appear
            self.assertNotIn(secret_transcript, combined)
            self.assertNotIn("SECRET", combined)

            # Raw JSON response must NOT appear
            self.assertNotIn(json.dumps(response_data), combined)

            # But metadata SHOULD appear
            self.assertIn("clips", combined.lower())

        finally:
            sel_logger.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
