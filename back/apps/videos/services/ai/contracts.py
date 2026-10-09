"""
AI Core Shared Contracts (Pydantic V2).

Defines strongly-typed, runtime-validated schemas for:
1. Audio Transcriptions (word-level timestamps and segment groupings).
2. Viral Clips Selection (timestamps, scoring, and metadata).
3. Provider Usage (token consumption, latency metrics, and estimated cost).
"""

from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ==============================================================================
# 1. TRANSCRIPTION CONTRACTS
# ==============================================================================

class WordTimestamp(BaseModel):
    """
    Represents an individual word with high-precision timestamps.
    Used for word-by-word animated subtitles.
    """
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    text: str = Field(..., min_length=1, description="Word string")
    start: float = Field(..., ge=0.0, description="Start timestamp in seconds")
    end: float = Field(..., ge=0.0, description="End timestamp in seconds")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Recognition confidence")
    speaker: Optional[str] = Field(default=None, description="Optional speaker identifier")

    @model_validator(mode="after")
    def validate_time_range(self) -> "WordTimestamp":
        if self.end < self.start:
            raise ValueError(f"Word end timestamp ({self.end}) cannot be earlier than start ({self.start})")
        return self

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class TranscriptionSegment(BaseModel):
    """
    Represents a sentence or grouped subtitle chunk.
    """
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    start: float = Field(..., ge=0.0, description="Start timestamp in seconds")
    end: float = Field(..., ge=0.0, description="End timestamp in seconds")
    text: str = Field(..., min_length=1, description="Full text for the segment")
    words: List[WordTimestamp] = Field(default_factory=list, description="Word breakdown within this segment")
    speaker: Optional[str] = Field(default=None, description="Speaker identifier if available")
    is_emoji: bool = Field(default=False, description="Flag indicating if the segment is a high-impact emoji")

    @model_validator(mode="after")
    def validate_segment_times(self) -> "TranscriptionSegment":
        if self.end < self.start:
            raise ValueError(f"Segment end ({self.end}) must be >= start ({self.start})")
        return self

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class TranscriptionResult(BaseModel):
    """
    Unified contract returned by any ASR engine (Groq Whisper, local Whisper, etc.).
    """
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    full_text: str = Field(..., min_length=1, description="Complete transcript text")
    words: List[WordTimestamp] = Field(default_factory=list, description="All word timestamps chronologically")
    segments: List[TranscriptionSegment] = Field(default_factory=list, description="Segment/phrase chunks")
    language: Optional[str] = Field(default="es", description="Detected or requested language code")
    duration: Optional[float] = Field(default=None, ge=0.0, description="Total audio duration in seconds")

    @property
    def word_count(self) -> int:
        return len(self.words) if self.words else len(self.full_text.split())

    def filter_by_timerange(self, start: float, end: float) -> List[WordTimestamp]:
        """
        Returns words that fall within [start, end].
        """
        return [w for w in self.words if w.start >= start and w.end <= end]


# ==============================================================================
# 2. VIRAL CLIPS SELECTION CONTRACTS
# ==============================================================================

class ViralClip(BaseModel):
    """
    Represents a single selected viral clip with rationale and virality scoring.
    """
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    start: float = Field(..., ge=0.0, description="Clip start timestamp in seconds")
    end: float = Field(..., ge=0.0, description="Clip end timestamp in seconds")
    title: str = Field(..., min_length=1, max_length=250, description="Catchy title for the short clip")
    virality_score: float = Field(..., ge=0.0, le=100.0, description="Virality score between 0 and 100")
    reasoning: str = Field(..., min_length=1, description="Why this clip is likely to perform well")
    hook_text: Optional[str] = Field(default=None, description="Introductory hook or banner caption")
    hashtags: List[str] = Field(default_factory=list, description="Recommended social media tags")

    @model_validator(mode="after")
    def validate_clip_duration(self) -> "ViralClip":
        if self.end <= self.start:
            raise ValueError(f"Clip end ({self.end}) must be strictly greater than start ({self.start})")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class ClipSelectionResult(BaseModel):
    """
    Response returned by SelectionEngine after LLM analysis.
    """
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    clips: List[ViralClip] = Field(default_factory=list, description="List of chosen viral clips")
    project_title: Optional[str] = Field(default=None, description="Associated project title")
    editing_style: Optional[str] = Field(default="dynamic", description="Style used for selection")
    target_count: Optional[int] = Field(default=None, ge=1, description="Requested number of clips")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context or raw metadata")


# ==============================================================================
# 3. PROVIDER USAGE & METRICS CONTRACTS
# ==============================================================================

class ProviderUsage(BaseModel):
    """
    Standardized usage and billing metrics for any AI call (LLM or ASR).
    """
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    provider: str = Field(..., min_length=1, description="AI provider identifier (groq, gemini, openai)")
    model: str = Field(..., min_length=1, description="Model identifier invoked")
    prompt_tokens: int = Field(default=0, ge=0, description="Tokens consumed in the prompt")
    completion_tokens: int = Field(default=0, ge=0, description="Tokens generated in the completion")
    total_tokens: int = Field(default=0, ge=0, description="Total tokens consumed")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Wall-clock duration of the API call")
    estimated_cost_usd: Optional[float] = Field(default=None, ge=0.0, description="Estimated cost in USD")
    cached: bool = Field(default=False, description="Whether response was served from cache")
    role: Literal["primary", "fallback"] = Field(
        default="primary", description="Whether the primary or the fallback model produced this call"
    )
    cache_read_tokens: int = Field(default=0, ge=0, description="Prompt tokens served from the provider cache")
    audio_seconds: Optional[float] = Field(
        default=None, ge=0.0, description="Audio duration submitted (transcription only)"
    )
    pricing_version: Optional[str] = Field(
        default=None, description="Version of the pricing table used for estimated_cost_usd"
    )
    resolved_model: Optional[str] = Field(
        default=None,
        description="Model name the provider reports having served (never user text); "
        "`model` stays the requested identifier",
    )

    @model_validator(mode="after")
    def auto_compute_total_tokens(self) -> "ProviderUsage":
        if self.total_tokens == 0 and (self.prompt_tokens > 0 or self.completion_tokens > 0):
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


T = TypeVar("T")

class AIExecutionResult(BaseModel, Generic[T]):
    """
    Generic envelope packaging domain data together with provider usage metrics.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    data: T
    usage: ProviderUsage
    success: bool = True
