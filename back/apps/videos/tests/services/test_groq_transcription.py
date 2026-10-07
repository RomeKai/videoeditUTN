"""
Unit tests for GroqTranscriptionProvider (AICORE-3).

Validates all acceptance criteria:
1. Uses whisper-large-v3-turbo model.
2. Requests timestamps by word and segment (verbose_json).
3. Applies offset correctly for each audio chunk.
4. Deduplicates words caused by chunk overlap.
5. Calculates latency, duration, and estimated cost.
6. 401/403 errors are NOT retryable.
7. 429, timeout, connection, and 5xx errors ARE retryable.
8. Missing GROQ_API_KEY produces a configuration error.
9. Does not log raw audio or full transcript text.
"""

import logging
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, mock_open

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
# Ensure the `groq` package is importable even when not installed.
# We create a lightweight stub module so that `from groq import Groq`
# inside groq_transcription.py resolves to a mock.
# ---------------------------------------------------------------------------
_groq_stub = types.ModuleType("groq")
_groq_stub.Groq = MagicMock  # Default; tests override via patch as needed
sys.modules.setdefault("groq", _groq_stub)

from apps.videos.services.ai.audio_preprocessor import AudioChunk
from apps.videos.services.ai.contracts import (
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)
from apps.videos.services.ai.errors import (
    AIAuthenticationError,
    AIConnectionError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
    is_retryable_error,
)

# Import AFTER the groq stub is registered
from apps.videos.services.ai.groq_transcription import GroqTranscriptionProvider


def _make_chunk(index=0, start=0.0, end=600.0, path="/tmp/chunk.flac"):
    """Helper to build AudioChunk instances for testing."""
    return AudioChunk(
        file_path=path,
        start_time=start,
        end_time=end,
        duration=end - start,
        chunk_index=index,
        size_bytes=1024,
        is_single_chunk=(index == 0 and start == 0.0),
    )


def _fake_groq_response(text="Hola mundo", words=None, segments=None, duration=10.0):
    """Returns a dict mimicking Groq verbose_json output."""
    if words is None:
        words = [
            {"word": "Hola", "start": 0.0, "end": 0.5},
            {"word": "mundo", "start": 0.6, "end": 1.1},
        ]
    if segments is None:
        segments = [
            {
                "start": 0.0,
                "end": 1.1,
                "text": text,
                "words": words,
            }
        ]
    return {
        "text": text,
        "words": words,
        "segments": segments,
        "language": "es",
        "duration": duration,
    }


def _build_provider_with_mock_client(mock_client=None):
    """
    Builds a GroqTranscriptionProvider with a mocked Groq client,
    bypassing the real SDK import entirely.
    """
    if mock_client is None:
        mock_client = MagicMock()
    with patch.object(
        GroqTranscriptionProvider, "__init__", lambda self, **kw: None
    ):
        provider = GroqTranscriptionProvider()
    provider._client = mock_client
    provider._model = "whisper-large-v3-turbo"
    return provider


class TestGroqProviderInitialization(unittest.TestCase):
    """AC: Missing GROQ_API_KEY produces a configuration error."""

    @patch("apps.videos.services.ai.groq_transcription.settings")
    def test_missing_api_key_raises_auth_error(self, mock_settings):
        mock_settings.GROQ_API_KEY = None
        mock_settings.AI_DEFAULT_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

        with self.assertRaises(AIAuthenticationError) as ctx:
            GroqTranscriptionProvider()

        self.assertIn("GROQ_API_KEY", str(ctx.exception))
        self.assertFalse(ctx.exception.is_retryable)

    @patch("apps.videos.services.ai.groq_transcription.settings")
    def test_explicit_api_key_overrides_settings(self, mock_settings):
        mock_settings.GROQ_API_KEY = None
        mock_settings.AI_DEFAULT_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

        with patch.object(_groq_stub, "Groq", MagicMock()) as MockGroq:
            provider = GroqTranscriptionProvider(api_key="gsk_test_key")
            MockGroq.assert_called_once_with(api_key="gsk_test_key")

    @patch("apps.videos.services.ai.groq_transcription.settings")
    def test_settings_key_used_when_no_explicit_key(self, mock_settings):
        mock_settings.GROQ_API_KEY = "gsk_from_settings"
        mock_settings.AI_DEFAULT_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

        with patch.object(_groq_stub, "Groq", MagicMock()) as MockGroq:
            provider = GroqTranscriptionProvider()
            MockGroq.assert_called_once_with(api_key="gsk_from_settings")


class TestGroqModelConfiguration(unittest.TestCase):
    """AC: Uses whisper-large-v3-turbo and requests word+segment timestamps."""

    def test_uses_whisper_large_v3_turbo(self):
        provider = _build_provider_with_mock_client()
        self.assertEqual(provider._model, "whisper-large-v3-turbo")

    def test_api_call_uses_correct_params(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = _fake_groq_response()
        mock_client.audio.transcriptions.create.return_value = mock_response

        provider = _build_provider_with_mock_client(mock_client)

        with patch("builtins.open", mock_open(read_data=b"fake_audio")):
            raw, latency = provider._call_groq_api("/tmp/test.flac")

        call_kwargs = mock_client.audio.transcriptions.create.call_args
        self.assertEqual(call_kwargs.kwargs["model"], "whisper-large-v3-turbo")
        self.assertEqual(call_kwargs.kwargs["response_format"], "verbose_json")
        self.assertEqual(call_kwargs.kwargs["timestamp_granularities"], ["word", "segment"])


class TestTimestampOffset(unittest.TestCase):
    """AC: Applies correctly the offset of each chunk."""

    def test_single_chunk_no_offset(self):
        """First chunk at start_time=0 should NOT shift timestamps."""
        result = TranscriptionResult(
            full_text="Hola mundo",
            words=[
                WordTimestamp(text="Hola", start=0.0, end=0.5),
                WordTimestamp(text="mundo", start=0.6, end=1.1),
            ],
            duration=10.0,
        )

        shifted = GroqTranscriptionProvider._apply_offset(result, 0.0)
        self.assertAlmostEqual(shifted.words[0].start, 0.0)
        self.assertAlmostEqual(shifted.words[1].end, 1.1)

    def test_multi_chunk_offset_applied(self):
        """Second chunk at start_time=598.0 should shift all timestamps by 598."""
        result = TranscriptionResult(
            full_text="Continuamos aqui",
            words=[
                WordTimestamp(text="Continuamos", start=0.0, end=0.8),
                WordTimestamp(text="aqui", start=0.9, end=1.3),
            ],
            duration=600.0,
        )

        shifted = GroqTranscriptionProvider._apply_offset(result, 598.0)
        self.assertAlmostEqual(shifted.words[0].start, 598.0)
        self.assertAlmostEqual(shifted.words[0].end, 598.8)
        self.assertAlmostEqual(shifted.words[1].start, 598.9)
        self.assertAlmostEqual(shifted.words[1].end, 599.3)

    def test_segments_also_shifted(self):
        """Segments and their inner words should also get the offset."""
        result = TranscriptionResult(
            full_text="Segmento test",
            words=[WordTimestamp(text="Segmento", start=0.0, end=0.5)],
            segments=[
                TranscriptionSegment(
                    start=0.0,
                    end=1.0,
                    text="Segmento test",
                    words=[WordTimestamp(text="Segmento", start=0.0, end=0.5)],
                )
            ],
            duration=5.0,
        )

        shifted = GroqTranscriptionProvider._apply_offset(result, 100.0)
        self.assertAlmostEqual(shifted.segments[0].start, 100.0)
        self.assertAlmostEqual(shifted.segments[0].end, 101.0)
        self.assertAlmostEqual(shifted.segments[0].words[0].start, 100.0)


class TestOverlapDeduplication(unittest.TestCase):
    """AC: Eliminates duplicate words caused by overlap."""

    def test_overlap_words_removed(self):
        """Words whose start falls in the overlap zone should be dropped."""
        # Previous chunk ended at 600.0, overlap is 2.0
        # Cutoff = 600.0 - (2.0 / 2) = 599.0
        # Words with start < 599.0 should be dropped
        words = [
            WordTimestamp(text="duplicada", start=598.5, end=599.0),  # < 599.0, drop
            WordTimestamp(text="transicion", start=599.0, end=599.5),  # >= 599.0, keep
            WordTimestamp(text="nueva", start=600.5, end=601.0),  # >= 599.0, keep
        ]

        deduped = GroqTranscriptionProvider._deduplicate_overlap(
            words=words,
            prev_end=600.0,
            overlap=2.0,
        )

        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0].text, "transicion")
        self.assertEqual(deduped[1].text, "nueva")

    def test_no_overlap_keeps_all(self):
        """When cutoff is below all words, all are kept."""
        words = [
            WordTimestamp(text="primera", start=0.0, end=0.5),
            WordTimestamp(text="palabra", start=0.6, end=1.0),
        ]

        # cutoff = 0.0 - 1.0 = -1.0, all words pass
        deduped = GroqTranscriptionProvider._deduplicate_overlap(
            words=words,
            prev_end=0.0,
            overlap=2.0,
        )
        self.assertEqual(len(deduped), 2)


class TestMetricsCalculation(unittest.TestCase):
    """AC: Calculates latency, duration, and estimated cost."""

    def test_cost_estimation(self):
        # 1 hour = $0.111
        cost = GroqTranscriptionProvider._estimate_cost(3600.0)
        self.assertAlmostEqual(cost, 0.111, places=4)

        # 30 minutes = $0.0555
        cost = GroqTranscriptionProvider._estimate_cost(1800.0)
        self.assertAlmostEqual(cost, 0.0555, places=4)

        # 0 seconds = $0
        cost = GroqTranscriptionProvider._estimate_cost(0.0)
        self.assertEqual(cost, 0.0)

    def test_transcribe_populates_usage_metrics(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = _fake_groq_response(duration=120.0)
        mock_client.audio.transcriptions.create.return_value = mock_response

        provider = _build_provider_with_mock_client(mock_client)
        chunk = _make_chunk(index=0, start=0.0, end=120.0)

        with patch("builtins.open", mock_open(read_data=b"audio")):
            exec_result = provider.transcribe([chunk])

        self.assertTrue(exec_result.success)
        self.assertEqual(exec_result.usage.provider, "groq")
        self.assertEqual(exec_result.usage.model, "whisper-large-v3-turbo")
        self.assertGreater(exec_result.usage.duration_seconds, 0.0)

        expected_cost = (120.0 / 3600.0) * 0.111
        self.assertAlmostEqual(exec_result.usage.estimated_cost_usd, expected_cost, places=5)


class TestErrorClassification(unittest.TestCase):
    """AC: 401/403 not retried. 429, timeout, connection, 5xx retryable."""

    def setUp(self):
        self.provider = _build_provider_with_mock_client()

    def test_401_maps_to_auth_error(self):
        exc = Exception("Unauthorized")
        exc.status_code = 401
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIAuthenticationError)
        self.assertEqual(result.status_code, 401)

    def test_403_maps_to_auth_error(self):
        exc = Exception("Forbidden")
        exc.status_code = 403
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIAuthenticationError)
        self.assertEqual(result.status_code, 403)

    def test_429_maps_to_rate_limit(self):
        exc = Exception("Rate limited")
        exc.status_code = 429
        exc.response = None
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIRateLimitError)

    def test_500_maps_to_server_error(self):
        exc = Exception("Internal Server Error")
        exc.status_code = 500
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIServerError)

    def test_502_maps_to_server_error(self):
        exc = Exception("Bad Gateway")
        exc.status_code = 502
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIServerError)

    def test_503_maps_to_server_error(self):
        exc = Exception("Service Unavailable")
        exc.status_code = 503
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIServerError)

    def test_timeout_maps_to_timeout_error(self):
        exc = TimeoutError("Connection timed out")
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AITimeoutError)

    def test_connection_error_maps_to_connection_error(self):
        exc = ConnectionError("Connection refused")
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIConnectionError)

    def test_groq_timeout_class_name(self):
        """Groq SDK timeout exceptions detected by class name."""
        class APITimeoutError(Exception):
            pass

        exc = APITimeoutError("Request timed out")
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AITimeoutError)

    def test_groq_connection_class_name(self):
        """Groq SDK connection exceptions detected by class name."""
        class APIConnectionError(Exception):
            pass

        exc = APIConnectionError("Connection failed")
        result = self.provider._classify_error(exc)
        self.assertIsInstance(result, AIConnectionError)


class TestRetryability(unittest.TestCase):
    """AC: 401/403 is_retryable=False. 429/timeout/connection/5xx is_retryable=True."""

    def setUp(self):
        self.provider = _build_provider_with_mock_client()

    def test_auth_errors_not_retryable(self):
        for code in (401, 403):
            exc = Exception(f"HTTP {code}")
            exc.status_code = code
            err = self.provider._classify_error(exc)
            self.assertFalse(err.is_retryable, f"HTTP {code} should NOT be retryable")
            self.assertFalse(is_retryable_error(err))

    def test_transient_errors_retryable(self):
        # 429
        exc_429 = Exception("Rate limit")
        exc_429.status_code = 429
        exc_429.response = None
        err_429 = self.provider._classify_error(exc_429)
        self.assertTrue(err_429.is_retryable, "429 should be retryable")
        self.assertTrue(is_retryable_error(err_429))

        # Timeout
        err_timeout = self.provider._classify_error(TimeoutError("timeout"))
        self.assertTrue(err_timeout.is_retryable, "Timeout should be retryable")
        self.assertTrue(is_retryable_error(err_timeout))

        # Connection
        err_conn = self.provider._classify_error(ConnectionError("refused"))
        self.assertTrue(err_conn.is_retryable, "Connection should be retryable")
        self.assertTrue(is_retryable_error(err_conn))

        # 5xx
        for code in (500, 502, 503):
            exc = Exception(f"Server {code}")
            exc.status_code = code
            err = self.provider._classify_error(exc)
            self.assertTrue(err.is_retryable, f"HTTP {code} should be retryable")
            self.assertTrue(is_retryable_error(err))


class TestLoggingPolicy(unittest.TestCase):
    """AC: Does not log raw audio or full transcript text."""

    def test_no_audio_or_transcript_in_logs(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        transcript_text = "Este es un texto secreto de prueba completo"
        mock_response.model_dump.return_value = _fake_groq_response(
            text=transcript_text,
            duration=10.0,
        )
        mock_client.audio.transcriptions.create.return_value = mock_response

        provider = _build_provider_with_mock_client(mock_client)
        chunk = _make_chunk()

        # Capture log output
        log_output = []
        handler = logging.Handler()
        handler.emit = lambda record: log_output.append(record.getMessage())

        groq_logger = logging.getLogger("apps.videos.services.ai.groq_transcription")
        groq_logger.addHandler(handler)
        groq_logger.setLevel(logging.DEBUG)

        try:
            with patch("builtins.open", mock_open(read_data=b"audio_bytes")):
                provider.transcribe([chunk])

            combined_logs = " ".join(log_output)

            # Full transcript should NOT appear in logs
            self.assertNotIn(transcript_text, combined_logs)

            # Raw audio bytes should NOT appear in logs
            self.assertNotIn("audio_bytes", combined_logs)

            # But metadata SHOULD appear (chunk index, word count, latency)
            self.assertIn("chunk 0", combined_logs.lower())

        finally:
            groq_logger.removeHandler(handler)


class TestMultiChunkTranscription(unittest.TestCase):
    """Integration test: multi-chunk pipeline with offset + dedup."""

    def test_two_chunk_merge_with_dedup(self):
        # Chunk 0: words at 0-2s (relative)
        chunk0_response = _fake_groq_response(
            text="Hola mundo",
            words=[
                {"word": "Hola", "start": 0.0, "end": 0.5},
                {"word": "mundo", "start": 0.6, "end": 1.1},
            ],
            duration=600.0,
        )

        # Chunk 1: words at 0-3s (relative), first word overlaps
        chunk1_response = _fake_groq_response(
            text="mundo nuevo dia",
            words=[
                {"word": "mundo", "start": 0.0, "end": 0.5},  # Overlap duplicate
                {"word": "nuevo", "start": 1.5, "end": 2.0},
                {"word": "dia", "start": 2.1, "end": 2.5},
            ],
            duration=602.0,
        )

        mock_client = MagicMock()
        mock_resp_0 = MagicMock()
        mock_resp_0.model_dump.return_value = chunk0_response
        mock_resp_1 = MagicMock()
        mock_resp_1.model_dump.return_value = chunk1_response
        mock_client.audio.transcriptions.create.side_effect = [mock_resp_0, mock_resp_1]

        provider = _build_provider_with_mock_client(mock_client)

        chunks = [
            _make_chunk(index=0, start=0.0, end=600.0),
            _make_chunk(index=1, start=598.0, end=1200.0),
        ]

        with patch("builtins.open", mock_open(read_data=b"audio")):
            exec_result = provider.transcribe(chunks)

        result = exec_result.data

        # Chunk 1 word "mundo" at relative 0.0 -> absolute 598.0
        # Cutoff = 600.0 - 1.0 = 599.0
        # 598.0 < 599.0 -> dropped
        # "nuevo" at relative 1.5 -> absolute 599.5 >= 599.0 -> kept
        # "dia" at relative 2.1 -> absolute 600.1 >= 599.0 -> kept

        # Total words: 2 (chunk0) + 2 (chunk1 after dedup) = 4
        self.assertEqual(len(result.words), 4)
        word_texts = [w.text for w in result.words]
        self.assertEqual(word_texts, ["Hola", "mundo", "nuevo", "dia"])

        # Verify offsets on chunk 1 words
        # "nuevo" should be at 598.0 + 1.5 = 599.5
        self.assertAlmostEqual(result.words[2].start, 599.5)
        # "dia" should be at 598.0 + 2.1 = 600.1
        self.assertAlmostEqual(result.words[3].start, 600.1)


if __name__ == "__main__":
    unittest.main()
