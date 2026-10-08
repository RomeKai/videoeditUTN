"""
Opt-in live smoke tests for AI Core V2 (real provider calls, tiny spend).

Skipped unless ``RUN_LIVE_AI=1``. Keys come from the container environment
(``env_file`` of the ``web`` service); they are never printed or asserted on.

Run:
    docker compose run --rm -e RUN_LIVE_AI=1 web python manage.py test apps.videos.tests.live

Scenarios:
  a. Groq Whisper through AudioPreprocessor + GroqTranscriptionProvider.
  b. Clip selection through LiteLLMSelectionProvider (primary model from settings).
  c. Cross-provider fallback: primary forced to a non-existent model so the
     provider falls back to the configured fallback model.

Limitation (a): the speech sample is synthesized with ffmpeg's ``flite``
filter (English TTS) because the repo ships no short speech fixture, while the
provider requests ``language="es"``. The test therefore validates the
request/response contract and timestamp structure, not transcription accuracy.
"""

import os
import subprocess
import tempfile
import time

from django.conf import settings
from django.test import SimpleTestCase
from unittest import skipUnless

from apps.videos.services.ai.audio_preprocessor import AudioPreprocessor
from apps.videos.services.ai.contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    TranscriptionResult,
)
from apps.videos.services.ai.errors import AIRateLimitError, AIServerError, AITimeoutError
from apps.videos.services.ai.groq_transcription import GroqTranscriptionProvider
from apps.videos.services.ai.litellm_selection import LiteLLMSelectionProvider
from apps.videos.services.ai.llm_credentials import resolve_api_key

_LIVE = os.environ.get("RUN_LIVE_AI") == "1"

_SPEECH_TEXT = (
    "Welcome to this short test. Today we are checking that the transcription "
    "pipeline returns word level timestamps for every spoken word."
)

# ~45 s of grounded transcript: enough for 15-60 s clips to be valid.
_SEGMENTS = [
    {"start": 0.0, "end": 8.0, "text": "I quit my job to build a startup and everyone laughed at me."},
    {"start": 8.0, "end": 16.0, "text": "Six months later we had our first thousand paying customers."},
    {"start": 16.0, "end": 24.0, "text": "The secret was talking to users every single day, no exceptions."},
    {"start": 24.0, "end": 32.0, "text": "Here is the one mistake that almost killed the company."},
    {"start": 32.0, "end": 40.0, "text": "We ignored cash flow, and payroll nearly bounced twice."},
]
_VIDEO_DURATION = 40.0
_TOLERANCE = 0.5


def _synthesize_speech(path: str, text: str) -> None:
    """Writes a WAV with English TTS (ffmpeg flite filter) to ``path``."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
            "-ar", "16000", "-ac", "1", path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _assert_clips_valid(test: SimpleTestCase, result: ClipSelectionResult) -> None:
    test.assertIsInstance(result, ClipSelectionResult)
    test.assertGreaterEqual(len(result.clips), 1)
    for clip in result.clips:
        test.assertGreaterEqual(clip.start, 0.0)
        test.assertGreater(clip.end, clip.start)
        test.assertLessEqual(clip.end, _VIDEO_DURATION + _TOLERANCE)
        test.assertTrue(clip.title.strip())
        test.assertGreaterEqual(clip.virality_score, 0.0)
        test.assertLessEqual(clip.virality_score, 100.0)


@skipUnless(_LIVE, "Live AI smoke tests are opt-in: set RUN_LIVE_AI=1")
class LiveGroqTranscriptionSmokeTest(SimpleTestCase):
    def test_transcribes_synthetic_speech_through_real_provider(self):
        if not getattr(settings, "GROQ_API_KEY", None):
            self.skipTest("GROQ_API_KEY is not configured")

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = os.path.join(tmp, "speech.wav")
            _synthesize_speech(wav_path, _SPEECH_TEXT)

            with AudioPreprocessor().process(wav_path) as chunks:
                self.assertEqual(len(chunks), 1)
                outcome = GroqTranscriptionProvider().transcribe(chunks)
                audio_duration = chunks[0].duration

        self.assertIsInstance(outcome, AIExecutionResult)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.usage.provider, "groq")

        result = outcome.data
        self.assertIsInstance(result, TranscriptionResult)
        self.assertTrue(result.full_text.strip())
        self.assertGreater(len(result.words), 0)
        self.assertGreater(len(result.segments), 0)

        previous_start = 0.0
        for word in result.words:
            self.assertTrue(word.text)
            self.assertGreaterEqual(word.start, previous_start - _TOLERANCE)
            self.assertGreaterEqual(word.end, word.start)
            self.assertLessEqual(word.end, audio_duration + _TOLERANCE)
            previous_start = word.start
        for segment in result.segments:
            self.assertGreaterEqual(segment.end, segment.start)
            self.assertLessEqual(segment.end, audio_duration + _TOLERANCE)


def _select_once_retrying_transient(provider: LiteLLMSelectionProvider):
    """Runs one selection; retries once after a pause on transient provider errors."""
    kwargs = dict(
        transcript="", video_duration=_VIDEO_DURATION, target_count=1, segments=_SEGMENTS
    )
    try:
        return provider.select_clips(**kwargs)
    except (AIServerError, AITimeoutError, AIRateLimitError):
        time.sleep(10)
        return provider.select_clips(**kwargs)


@skipUnless(_LIVE, "Live AI smoke tests are opt-in: set RUN_LIVE_AI=1")
class LiveClipSelectionSmokeTest(SimpleTestCase):
    def test_primary_model_returns_valid_clips(self):
        primary = settings.AI_DEFAULT_LLM_MODEL
        if not resolve_api_key(primary):
            self.skipTest(f"No API key configured for primary model {primary}")

        # fallback == primary disables the fallback, so only the primary can answer.
        provider = LiteLLMSelectionProvider(primary_model=primary, fallback_model=primary)
        outcome = _select_once_retrying_transient(provider)

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.usage.model, primary)
        self.assertGreater(outcome.usage.completion_tokens, 0)
        _assert_clips_valid(self, outcome.data)

    def test_falls_back_to_secondary_provider_when_primary_fails(self):
        # Fallback pinned to a provider other than the primary's: a deployed env may
        # configure a same-provider fallback, which cannot prove cross-provider fallback.
        fallback = "groq/openai/gpt-oss-20b"
        if not resolve_api_key(fallback):
            self.skipTest("No API key configured for the fallback provider")

        # The bogus primary is rejected by the provider (no tokens billed).
        provider = LiteLLMSelectionProvider(
            primary_model="gemini/this-model-does-not-exist",
            fallback_model=fallback,
        )
        outcome = _select_once_retrying_transient(provider)

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.usage.model, fallback)
        _assert_clips_valid(self, outcome.data)
