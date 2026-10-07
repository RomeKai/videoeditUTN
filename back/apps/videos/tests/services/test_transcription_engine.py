"""
Unit tests for TranscriptionEngine backend routing (AICORE-5 / ADR-008).

Acceptance criteria covered:
1. transcribe() keeps its public signature and {start, end, text} output.
2. transcribe_detailed() returns the contract plus provider usage metrics.
3. With AI_CORE_V2_ENABLED=False the legacy local Whisper path is used.
4. Importing the module does not import whisper, torch or network clients.
5. Groq failures propagate typed errors and NEVER fall back to local Whisper.
6. TRANSCRIPTION_BACKEND selects the backend and rejects invalid values.
"""

import builtins
import importlib
import importlib.util
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.videos.services import transcription_engine as te_module
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ProviderUsage,
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIContractValidationError,
    AIRateLimitError,
    is_retryable_error,
)
from apps.videos.services.transcription_engine import TranscriptionBackend, TranscriptionEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _groq_result() -> AIExecutionResult:
    words = [
        WordTimestamp(text="Hola", start=0.0, end=0.4),
        WordTimestamp(text="mundo", start=0.4, end=0.9),
    ]
    data = TranscriptionResult(
        full_text="Hola mundo",
        words=words,
        segments=[TranscriptionSegment(start=0.0, end=0.9, text="Hola mundo", words=words)],
        duration=0.9,
    )
    usage = ProviderUsage(
        provider="groq",
        model="whisper-large-v3-turbo",
        duration_seconds=0.12,
        estimated_cost_usd=0.000028,
    )
    return AIExecutionResult[TranscriptionResult](data=data, usage=usage)


_WHISPER_RAW = {
    "text": " Hola mundo",
    "language": "es",
    "segments": [
        {
            "start": 0.0,
            "end": 0.9,
            "text": " Hola mundo",
            "words": [
                {"word": " Hola", "start": 0.0, "end": 0.4, "probability": 0.98},
                {"word": " mundo", "start": 0.4, "end": 0.9, "probability": 0.95},
            ],
        }
    ],
}


class _FakePreprocessor:
    """Mimics AudioPreprocessor.process() and records cleanup on exit."""

    def __init__(self):
        self.exited = False

    @contextmanager
    def process(self, input_path):
        try:
            yield [MagicMock(name="chunk")]
        finally:
            self.exited = True


class _TranscriptionTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.audio_path = tempfile.mkstemp(suffix=".flac")
        os.close(fd)
        self.preprocessor = _FakePreprocessor()
        self.groq = MagicMock(name="GroqTranscriptionProvider")
        self.groq.transcribe.return_value = _groq_result()

    def tearDown(self):
        os.remove(self.audio_path)

    def _engine(self, **kwargs) -> TranscriptionEngine:
        return TranscriptionEngine(
            model_size="tiny",
            groq_provider_factory=lambda: self.groq,
            preprocessor_factory=lambda: self.preprocessor,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# 4. Import side effects
# ---------------------------------------------------------------------------

class TestImportIsCheap(unittest.TestCase):
    def test_module_import_does_not_import_heavy_or_network_modules(self):
        forbidden = {"whisper", "torch", "groq", "litellm"}
        seen = set()
        real_import = builtins.__import__

        def recording_import(name, *args, **kwargs):
            seen.add(name.split(".")[0])
            return real_import(name, *args, **kwargs)

        # Execute the module source under a throwaway name: reloading the real
        # module would swap the enum/class identities used by the other tests.
        spec = importlib.util.spec_from_file_location("_te_import_probe", te_module.__file__)
        probe = importlib.util.module_from_spec(spec)
        with patch.object(builtins, "__import__", side_effect=recording_import):
            spec.loader.exec_module(probe)

        self.assertFalse(seen & forbidden, f"Module import pulled in: {seen & forbidden}")

    def test_constructor_does_not_load_model_or_clients(self):
        with patch.object(builtins, "__import__", wraps=builtins.__import__) as spy:
            TranscriptionEngine(model_size="base")
        imported = {c.args[0].split(".")[0] for c in spy.call_args_list}
        self.assertFalse(imported & {"whisper", "torch", "groq"})


# ---------------------------------------------------------------------------
# 6. Backend resolution
# ---------------------------------------------------------------------------

class TestResolveBackend(unittest.TestCase):
    @override_settings(AI_CORE_V2_ENABLED=False, TRANSCRIPTION_BACKEND="groq")
    def test_flag_off_forces_local_even_if_backend_is_groq(self):
        self.assertIs(TranscriptionEngine.resolve_backend(), TranscriptionBackend.LOCAL)

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="groq")
    def test_flag_on_uses_groq(self):
        self.assertIs(TranscriptionEngine.resolve_backend(), TranscriptionBackend.GROQ)

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND=" LOCAL ")
    def test_backend_value_is_normalized(self):
        self.assertIs(TranscriptionEngine.resolve_backend(), TranscriptionBackend.LOCAL)

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="deepgram")
    def test_invalid_backend_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            TranscriptionEngine.resolve_backend()


# ---------------------------------------------------------------------------
# 1, 2, 5. Groq path
# ---------------------------------------------------------------------------

@override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="groq")
class TestGroqPath(_TranscriptionTestCase):
    def test_transcribe_returns_word_dicts_with_legacy_keys(self):
        words = self._engine().transcribe(self.audio_path, word_timestamps=True)
        self.assertEqual(
            words,
            [
                {"start": 0.0, "end": 0.4, "text": "Hola"},
                {"start": 0.4, "end": 0.9, "text": "mundo"},
            ],
        )

    def test_transcribe_returns_segments_when_words_not_requested(self):
        segments = self._engine().transcribe(self.audio_path, word_timestamps=False)
        self.assertEqual(segments, [{"start": 0.0, "end": 0.9, "text": "Hola mundo"}])

    def test_transcribe_sets_last_full_text(self):
        engine = self._engine()
        engine.transcribe(self.audio_path)
        self.assertEqual(engine.last_full_text, "Hola mundo")

    def test_transcribe_detailed_exposes_provider_metrics(self):
        result = self._engine().transcribe_detailed(self.audio_path)
        self.assertIsInstance(result.data, TranscriptionResult)
        self.assertEqual(result.usage.provider, "groq")
        self.assertEqual(result.usage.model, "whisper-large-v3-turbo")
        self.assertGreater(result.usage.duration_seconds, 0)

    def test_retryable_error_propagates_without_local_fallback(self):
        self.groq.transcribe.side_effect = AIRateLimitError(provider="groq")
        engine = self._engine()

        with patch.object(TranscriptionEngine, "_transcribe_local_raw") as local:
            with self.assertRaises(AIRateLimitError) as ctx:
                engine.transcribe(self.audio_path)

        local.assert_not_called()
        self.assertIsNone(engine._model)
        self.assertTrue(is_retryable_error(ctx.exception))

    def test_preprocessor_cleanup_runs_when_provider_fails(self):
        self.groq.transcribe.side_effect = AIRateLimitError(provider="groq")
        with self.assertRaises(AIRateLimitError):
            self._engine().transcribe(self.audio_path)
        self.assertTrue(self.preprocessor.exited)

    @override_settings(GROQ_API_KEY=None)
    def test_missing_groq_key_fails_fast_and_is_not_retryable(self):
        engine = TranscriptionEngine(preprocessor_factory=lambda: self.preprocessor)

        with patch.object(TranscriptionEngine, "_transcribe_local_raw") as local:
            with self.assertRaises(AIAuthenticationError) as ctx:
                engine.transcribe(self.audio_path)

        local.assert_not_called()
        self.assertFalse(is_retryable_error(ctx.exception))

    def test_missing_file_raises_before_calling_provider(self):
        with self.assertRaises(FileNotFoundError):
            self._engine().transcribe("/nonexistent/audio.mp4")
        self.groq.transcribe.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Legacy local path
# ---------------------------------------------------------------------------

@override_settings(AI_CORE_V2_ENABLED=False)
class TestLocalPath(_TranscriptionTestCase):
    def _engine_with_fake_model(self) -> TranscriptionEngine:
        engine = self._engine()
        engine._model = MagicMock(name="WhisperModel")
        engine._model.transcribe.return_value = _WHISPER_RAW
        return engine

    def test_flag_off_uses_local_whisper_and_never_calls_groq(self):
        engine = self._engine_with_fake_model()
        engine.transcribe(self.audio_path)
        engine._model.transcribe.assert_called_once_with(
            self.audio_path, fp16=False, word_timestamps=True
        )
        self.groq.transcribe.assert_not_called()

    def test_local_words_keep_legacy_format(self):
        words = self._engine_with_fake_model().transcribe(self.audio_path)
        self.assertEqual(
            words,
            [
                {"start": 0.0, "end": 0.4, "text": "Hola"},
                {"start": 0.4, "end": 0.9, "text": "mundo"},
            ],
        )

    def test_local_segments_keep_legacy_format(self):
        segments = self._engine_with_fake_model().transcribe(self.audio_path, word_timestamps=False)
        self.assertEqual(segments, [{"start": 0.0, "end": 0.9, "text": "Hola mundo"}])

    def test_local_detailed_maps_to_contract_with_zero_cost(self):
        result = self._engine_with_fake_model().transcribe_detailed(self.audio_path)
        self.assertEqual(result.data.full_text, "Hola mundo")
        self.assertEqual([w.text for w in result.data.words], ["Hola", "mundo"])
        self.assertEqual(result.data.words[0].confidence, 0.98)
        self.assertEqual(result.usage.provider, "local")
        self.assertEqual(result.usage.model, "whisper-tiny")
        self.assertEqual(result.usage.estimated_cost_usd, 0.0)

    def test_local_detailed_silent_audio_is_non_retryable(self):
        engine = self._engine()
        engine._model = MagicMock()
        engine._model.transcribe.return_value = {"text": "  ", "segments": []}
        with self.assertRaises(AIContractValidationError) as ctx:
            engine.transcribe_detailed(self.audio_path)
        self.assertFalse(is_retryable_error(ctx.exception))

    def test_local_transcribe_silent_audio_returns_empty_list_like_legacy(self):
        engine = self._engine()
        engine._model = MagicMock()
        engine._model.transcribe.return_value = {"text": "", "segments": []}
        self.assertEqual(engine.transcribe(self.audio_path), [])
        self.assertIsNone(engine.last_full_text)

    @override_settings(AI_CORE_V2_ENABLED=True, TRANSCRIPTION_BACKEND="local")
    def test_explicit_local_backend_with_flag_on(self):
        engine = self._engine_with_fake_model()
        engine.transcribe(self.audio_path)
        engine._model.transcribe.assert_called_once()
        self.groq.transcribe.assert_not_called()


# ---------------------------------------------------------------------------
# Static helpers (unchanged behavior)
# ---------------------------------------------------------------------------

class TestGroupWordsUnchanged(unittest.TestCase):
    def test_groups_by_max_words(self):
        words = [{"start": i, "end": i + 1, "text": f"w{i}"} for i in range(5)]
        grouped = TranscriptionEngine.group_words(words, max_words=2)
        self.assertEqual([g["text"] for g in grouped], ["w0 w1", "w2 w3", "w4"])


if __name__ == "__main__":
    unittest.main()
