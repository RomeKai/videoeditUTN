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

import httpx

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
from apps.videos.services.ai.pricing import PRICING_VERSION
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

        mock_settings.AI_TRANSCRIPTION_TIMEOUT_SECONDS = 45.0

        with patch.object(_groq_stub, "Groq", MagicMock()) as MockGroq:
            provider = GroqTranscriptionProvider(api_key="gsk_test_key")
            MockGroq.assert_called_once_with(
                api_key="gsk_test_key", timeout=httpx.Timeout(45.0, connect=5.0), max_retries=0,
            )

    @patch("apps.videos.services.ai.groq_transcription.settings")
    def test_settings_key_used_when_no_explicit_key(self, mock_settings):
        mock_settings.GROQ_API_KEY = "gsk_from_settings"
        mock_settings.AI_DEFAULT_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"

        mock_settings.AI_TRANSCRIPTION_TIMEOUT_SECONDS = 45.0

        with patch.object(_groq_stub, "Groq", MagicMock()) as MockGroq:
            provider = GroqTranscriptionProvider()
            MockGroq.assert_called_once_with(
                api_key="gsk_from_settings", timeout=httpx.Timeout(45.0, connect=5.0), max_retries=0,
            )


class TestGroqClientTimeoutAndRetries(unittest.TestCase):
    """
    The real SDK client is built with the configured timeout and without its
    own retries (Celery owns them): a timed-out chunk is sent ONCE and comes
    back as a retryable AITimeoutError.
    """

    def _real_groq(self):
        """The installed groq package, even if this module's stub is registered."""
        import importlib

        for name in [n for n in sys.modules if n == "groq" or n.startswith("groq.")]:
            del sys.modules[name]
        try:
            return importlib.import_module("groq")
        except ImportError:
            self.skipTest("groq is not installed")

    def test_a_timed_out_chunk_is_sent_once_and_is_retryable(self):
        import tempfile

        import httpx
        from django.test import override_settings

        with patch.dict(sys.modules):
            groq = self._real_groq()
            requests = []

            def timing_out(request):
                requests.append(request)
                raise httpx.ReadTimeout("timed out", request=request)

            real_client = groq.Groq
            http_client = httpx.Client(transport=httpx.MockTransport(timing_out))
            with override_settings(AI_TRANSCRIPTION_TIMEOUT_SECONDS=33.0), patch.object(
                groq, "Groq", side_effect=lambda **kw: real_client(http_client=http_client, **kw),
            ):
                provider = GroqTranscriptionProvider(api_key="gsk_test_key")

            self.assertEqual(provider._client.max_retries, 0)
            # The long timeout is for the upload and the answer; connecting stays short.
            self.assertEqual(provider._client.timeout, httpx.Timeout(33.0, connect=5.0))
            with tempfile.NamedTemporaryFile(suffix=".flac") as audio:
                audio.write(b"fake audio")
                audio.flush()
                with self.assertRaises(AITimeoutError) as ctx:
                    provider.transcribe([_make_chunk(path=audio.name)])

        self.assertEqual(len(requests), 1)
        self.assertTrue(is_retryable_error(ctx.exception))

    def test_a_dropped_connection_is_retried_twice_then_left_to_celery(self):
        import tempfile

        import httpx

        with patch.dict(sys.modules):
            groq = self._real_groq()
            requests = []

            def dropping(request):
                requests.append(request)
                raise httpx.ConnectError("connection reset", request=request)

            real_client = groq.Groq
            http_client = httpx.Client(transport=httpx.MockTransport(dropping))
            with patch.object(
                groq, "Groq", side_effect=lambda **kw: real_client(http_client=http_client, **kw),
            ):
                provider = GroqTranscriptionProvider(api_key="gsk_test_key")

            with tempfile.NamedTemporaryFile(suffix=".flac") as audio:
                audio.write(b"fake audio")
                audio.flush()
                with patch(SLEEP) as sleep, self.assertRaises(AIConnectionError) as ctx:
                    provider.transcribe([_make_chunk(path=audio.name)])

        # Never the SDK's own retries: the provider's two, with a short backoff.
        self.assertEqual(len(requests), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0, 5.0])
        self.assertTrue(is_retryable_error(ctx.exception))

    def _provider_with_transport(self, groq, handler):
        import httpx

        real_client = groq.Groq
        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch.object(
            groq, "Groq", side_effect=lambda **kw: real_client(http_client=http_client, **kw),
        ):
            return GroqTranscriptionProvider(api_key="gsk_test_key")

    def _transcribe(self, provider, chunks=1):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".flac") as audio:
            audio.write(b"fake audio")
            audio.flush()
            parts = [
                _make_chunk(index=i, start=i * 600.0, end=(i + 1) * 600.0, path=audio.name)
                for i in range(chunks)
            ]
            with patch(SLEEP) as sleep:
                try:
                    return provider.transcribe(parts), None, sleep
                except Exception as exc:  # returned for the assertions
                    return None, exc, sleep

    def test_a_connect_timeout_is_retried_in_the_chunk_and_bills_nothing(self):
        import httpx

        from apps.videos.services.ai.errors import is_billing_uncertain

        with patch.dict(sys.modules):
            groq = self._real_groq()
            requests = []

            def connect_timeout_then_answer(request):
                requests.append(request)
                if len(requests) in (2, 3):  # chunk 1: connect timeout, then pool timeout
                    error = httpx.ConnectTimeout if len(requests) == 2 else httpx.PoolTimeout
                    raise error("could not connect", request=request)
                return httpx.Response(200, json=_fake_groq_response())

            provider = self._provider_with_transport(groq, connect_timeout_then_answer)
            result, error, sleep = self._transcribe(provider, chunks=2)

        self.assertIsNone(error)
        # Chunk 0 once; chunk 1 retried in the provider, not by Celery.
        self.assertEqual(len(requests), 4)
        self.assertEqual(sleep.call_count, 2)
        # Nothing reached Groq on the failed attempts: the cost stays known.
        self.assertIsNotNone(result.usage.estimated_cost_usd)
        self.assertEqual(result.usage.audio_seconds, 1200.0)

        from apps.videos.services.ai.errors import AIConnectionNotEstablishedError

        not_sent = AIConnectionNotEstablishedError("x", provider="groq")
        self.assertTrue(is_retryable_error(not_sent))
        self.assertFalse(is_billing_uncertain(not_sent))

    def test_a_read_timeout_still_goes_back_to_celery_with_an_unknown_cost(self):
        import httpx

        with patch.dict(sys.modules):
            groq = self._real_groq()
            requests = []

            def answer_then_read_timeout(request):
                requests.append(request)
                if len(requests) == 2:
                    raise httpx.ReadTimeout("slow", request=request)
                return httpx.Response(200, json=_fake_groq_response())

            provider = self._provider_with_transport(groq, answer_then_read_timeout)
            _, error, sleep = self._transcribe(provider, chunks=2)

        self.assertIsInstance(error, AITimeoutError)
        self.assertEqual(len(requests), 2)
        sleep.assert_not_called()
        (partial,) = error.usage_attempts
        self.assertIsNone(partial.estimated_cost_usd)

    def test_an_answer_that_cannot_be_decoded_is_not_retried_and_its_cost_is_unknown(self):
        import httpx

        from apps.videos.services.ai.errors import AIUnexpectedProviderError, is_billing_uncertain

        with patch.dict(sys.modules):
            groq = self._real_groq()
            requests = []

            def answer_then_garbage(request):
                requests.append(request)
                if len(requests) == 2:
                    return httpx.Response(
                        200, headers={"content-type": "application/json"}, content=b"{not json",
                    )
                return httpx.Response(200, json=_fake_groq_response())

            provider = self._provider_with_transport(groq, answer_then_garbage)
            _, error, sleep = self._transcribe(provider, chunks=2)

        self.assertIsInstance(error, AIUnexpectedProviderError)
        self.assertEqual(len(requests), 2)  # never re-sent inside the provider
        sleep.assert_not_called()
        self.assertTrue(is_retryable_error(error))  # Celery still retries the stage
        self.assertTrue(is_billing_uncertain(error))
        (partial,) = error.usage_attempts
        # Groq answered chunk 1 (and billed it): the total is unknown, not a floor.
        self.assertIsNone(partial.estimated_cost_usd)


SLEEP = "apps.videos.services.ai.groq_transcription.time.sleep"


def _status_exc(status_code, retry_after=None, name="APIStatusError"):
    exc = type(name, (Exception,), {})(f"HTTP {status_code}")
    exc.status_code = status_code
    exc.response = MagicMock()
    exc.response.headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    return exc


def _answer(text="hola"):
    response = MagicMock()
    response.model_dump.return_value = _fake_groq_response(
        text=text, words=[{"word": text, "start": 0.0, "end": 0.5}],
        segments=[{"start": 0.0, "end": 0.5, "text": text, "words": []}],
    )
    return response


class TestChunkRetries(unittest.TestCase):
    """
    One transient error on a chunk is retried inside the provider, so a Celery
    retry does not send (and pay for) the chunks already transcribed again.
    Only 429, 5xx and connection errors; never a timeout.
    """

    def transcribe(self, *side_effects, chunks=2):
        client = MagicMock()
        client.audio.transcriptions.create.side_effect = list(side_effects)
        provider = _build_provider_with_mock_client(client)
        parts = [_make_chunk(index=i, start=i * 600.0, end=(i + 1) * 600.0) for i in range(chunks)]
        with patch("builtins.open", mock_open(read_data=b"audio")), patch(SLEEP) as sleep:
            try:
                return provider.transcribe(parts), client, sleep, None
            except Exception as exc:  # returned for the assertions
                return None, client, sleep, exc

    def test_a_rate_limited_chunk_is_retried_without_resending_the_previous_ones(self):
        result, client, sleep, error = self.transcribe(
            _answer("uno"), _status_exc(429, retry_after=7), _answer("dos"),
        )

        self.assertIsNone(error)
        self.assertEqual(client.audio.transcriptions.create.call_count, 3)  # chunk 0 once
        sleep.assert_called_once_with(7.0)
        # A 429 bills nothing: the cost is still the two answered chunks.
        self.assertEqual(result.usage.audio_seconds, 1200.0)
        self.assertIsNotNone(result.usage.estimated_cost_usd)

    def test_retry_after_is_capped(self):
        _, _, sleep, error = self.transcribe(_status_exc(429, retry_after=600), _answer(), chunks=1)

        self.assertIsNone(error)
        sleep.assert_called_once_with(60.0)

    def test_a_server_error_waits_a_short_backoff(self):
        _, _, sleep, error = self.transcribe(_status_exc(503), _answer(), chunks=1)

        self.assertIsNone(error)
        sleep.assert_called_once_with(2.0)

    def test_a_timeout_is_never_retried_inside_the_provider(self):
        timeout = type("APITimeoutError", (Exception,), {})("Request timed out.")
        _, client, sleep, error = self.transcribe(_answer(), timeout)

        self.assertIsInstance(error, AITimeoutError)
        self.assertEqual(client.audio.transcriptions.create.call_count, 2)
        sleep.assert_not_called()

    def test_a_chunk_that_keeps_failing_gives_up_after_two_more_attempts(self):
        _, client, sleep, error = self.transcribe(
            _answer(), _status_exc(500), _status_exc(502), _status_exc(503),
        )

        self.assertIsInstance(error, AIServerError)
        self.assertEqual(client.audio.transcriptions.create.call_count, 4)
        self.assertEqual(sleep.call_count, 2)
        # Only chunk 0 answered: it is the billed usage the error carries.
        (usage,) = error.usage_attempts
        self.assertEqual(usage.audio_seconds, 600.0)
        self.assertIsNotNone(usage.estimated_cost_usd)

    def test_a_retried_connection_error_makes_the_cost_unknown(self):
        result, client, _, error = self.transcribe(ConnectionError("reset"), _answer(), chunks=1)

        self.assertIsNone(error)
        self.assertEqual(client.audio.transcriptions.create.call_count, 2)
        # The dropped request may have been billed: unknown, never a false floor.
        self.assertIsNone(result.usage.estimated_cost_usd)
        self.assertEqual(result.usage.audio_seconds, 600.0)

    def test_a_rejected_chunk_is_not_retried(self):
        _, client, sleep, error = self.transcribe(_status_exc(400), chunks=1)

        self.assertIsNotNone(error)
        self.assertFalse(is_retryable_error(error))
        self.assertEqual(client.audio.transcriptions.create.call_count, 1)
        sleep.assert_not_called()


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

        # Turbo rate (USD 0.04/h), not the large-v3 rate (finding H3).
        expected_cost = (120.0 / 3600.0) * 0.04
        self.assertAlmostEqual(exec_result.usage.estimated_cost_usd, expected_cost, places=5)
        self.assertEqual(exec_result.usage.audio_seconds, 120.0)
        self.assertEqual(exec_result.usage.pricing_version, PRICING_VERSION)
        self.assertEqual(exec_result.usage.role, "primary")

    def _transcribe_with_model(self, model, durations):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.model_dump.return_value = _fake_groq_response(duration=10.0)
        mock_client.audio.transcriptions.create.return_value = mock_response
        provider = _build_provider_with_mock_client(mock_client)
        provider._model = model
        chunks = []
        start = 0.0
        for i, d in enumerate(durations):
            chunks.append(_make_chunk(index=i, start=start, end=start + d))
            start += d
        with patch("builtins.open", mock_open(read_data=b"audio")):
            return provider.transcribe(chunks)

    def test_turbo_uses_own_rate_not_large_v3(self):
        turbo = self._transcribe_with_model("whisper-large-v3-turbo", [3600.0])
        large = self._transcribe_with_model("whisper-large-v3", [3600.0])
        self.assertAlmostEqual(turbo.usage.estimated_cost_usd, 0.04, places=6)
        self.assertAlmostEqual(large.usage.estimated_cost_usd, 0.111, places=6)

    def test_unknown_model_cost_is_none_not_zero(self):
        result = self._transcribe_with_model("whisper-mystery", [60.0])
        self.assertIsNone(result.usage.estimated_cost_usd)
        self.assertEqual(result.usage.audio_seconds, 60.0)

    def test_minimum_billable_length_applies_per_chunk(self):
        # Two 4 s chunks are each billed as 10 s -> 20 s billed in total.
        result = self._transcribe_with_model("whisper-large-v3-turbo", [4.0, 4.0])
        self.assertAlmostEqual(result.usage.estimated_cost_usd, 20.0 / 3600.0 * 0.04, places=6)
        self.assertEqual(result.usage.audio_seconds, 8.0)


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
