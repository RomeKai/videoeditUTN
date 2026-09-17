"""
AI Core Package.

Unified contracts, error hierarchies, and shared abstractions for
transcription, clip selection, and LLM provider interactions.
"""

from .audio_preprocessor import AudioChunk, AudioPreprocessor
from .groq_transcription import GroqTranscriptionProvider
from .contracts import (
    AIExecutionResult,
    ClipSelectionResult,
    ProviderUsage,
    TranscriptionResult,
    TranscriptionSegment,
    ViralClip,
    WordTimestamp,
)
from .errors import (
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

__all__ = [
    # Audio Preprocessor
    "AudioPreprocessor",
    "AudioChunk",
    # Groq Transcription Provider
    "GroqTranscriptionProvider",
    # Contracts
    "WordTimestamp",
    "TranscriptionSegment",
    "TranscriptionResult",
    "ViralClip",
    "ClipSelectionResult",
    "ProviderUsage",
    "AIExecutionResult",
    # Errors
    "AIError",
    "RetryableAIError",
    "NonRetryableAIError",
    "AIRateLimitError",
    "AITimeoutError",
    "AIServerError",
    "AIConnectionError",
    "AIAuthenticationError",
    "AIInvalidRequestError",
    "AIContentFilterError",
    "AIQuotaExceededError",
    "AIContractValidationError",
    "is_retryable_error",
]
