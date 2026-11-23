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
# 2b. PROMPT-TO-EDIT CONTRACTS (PTE-01a, #72)
# ==============================================================================

# Same values as the VideoProject choices (a test keeps them in sync). Note the
# model stores the square ratio as '1:Post'.
EditingStyleValue = Literal["dynamic", "minimalist", "vlog", "hormozi", "custom"]
AspectRatioValue = Literal["9:16", "1:Post", "16:9"]
LayoutValue = Literal["fill", "fit", "blurred", "split", "pip", "versus", "active"]
PositionValue = Literal["left", "center", "right"]
SubtitleSizeValue = Literal["small", "medium", "large"]
SubtitlePositionValue = Literal["top", "center", "bottom"]
# The resolution tiers PricingEngine and SubscriptionPlan.max_resolution use.
RenderResolutionValue = Literal["720p", "1080p", "4K"]

# Requests the workspace plan may forbid and that change the price. They are
# never applied as parsed: ConfigValidator (PTE-01b) checks them first.
PLAN_GATED_FIELDS = ("render_resolution", "use_seo", "use_ai_thumbnail")

PROJECT_CONFIG_MAX_CLIPS = 20


class ProjectConfig(BaseModel):
    """
    Project settings a Prompt-to-Edit prompt can ask for. Only existing
    VideoProject fields and metadata keys, with the model's own choices; no
    price, plan, tier or approval field. ``None`` means "not asked": the
    project default stays. The PLAN_GATED_FIELDS are requests the workspace
    plan may forbid; ConfigValidator decides on them (PTE-01b).
    """

    model_config = ConfigDict(extra="ignore")

    editing_style: Optional[EditingStyleValue] = None
    aspect_ratio: Optional[AspectRatioValue] = None
    render_layout: Optional[LayoutValue] = None
    gameplay_position: Optional[PositionValue] = None
    camera_selection_position: Optional[PositionValue] = None
    speaker_tracking: Optional[bool] = None
    use_facetracking: Optional[bool] = None
    remove_silences: Optional[bool] = None
    add_subtitles: Optional[bool] = None
    subtitle_color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    subtitle_size: Optional[SubtitleSizeValue] = None
    subtitle_position: Optional[SubtitlePositionValue] = None
    subtitle_with_emojis: Optional[bool] = None
    subtitle_words_per_segment: Optional[int] = Field(default=None, ge=1, le=10)
    # Stored in VideoProject.metadata, like the create view does.
    max_clips: Optional[int] = Field(default=None, ge=1, le=PROJECT_CONFIG_MAX_CLIPS)
    selection_criteria: Optional[str] = Field(default=None, max_length=300)
    # Plan-gated requests (PLAN_GATED_FIELDS), metadata keys the pricing reads.
    render_resolution: Optional[RenderResolutionValue] = None
    use_seo: Optional[bool] = None
    use_ai_thumbnail: Optional[bool] = None

    @field_validator("subtitle_color", mode="before")
    @classmethod
    def _upper_color(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("selection_criteria", mode="before")
    @classmethod
    def _clean_criteria(cls, value: Any) -> Any:
        from apps.videos.services.ai.selection_criteria import normalize_selection_criteria

        return normalize_selection_criteria(value) if value is not None else None

    def requested(self) -> Dict[str, Any]:
        """Only the settings the prompt asked for."""
        return self.model_dump(exclude_none=True)


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
    reasoning_tokens: int = Field(
        default=0,
        ge=0,
        description="Reasoning (thinking) tokens billed as output on top of completion_tokens",
    )
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

    error_code: Optional[str] = Field(
        default=None,
        description="CLASS NAME of the AIError that ended this attempt (None = it answered); "
        "never an error message",
    )

    @model_validator(mode="after")
    def auto_compute_total_tokens(self) -> "ProviderUsage":
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.completion_tokens + self.reasoning_tokens
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
    # Every provider attempt made, in order (failed primary first, then the one
    # that answered). ``usage`` stays the final answering usage. Empty when the
    # producer made a single attempt and did not track attempts.
    attempts: List[ProviderUsage] = Field(default_factory=list)
