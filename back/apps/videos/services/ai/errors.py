"""
AI Core Error Hierarchy.

Explicitly separates retryable (transient) failures from non-retryable
(deterministic/permanent) failures to guide Celery task retries and fallback chains.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.videos.services.ai.contracts import ProviderUsage


class AIError(Exception):
    """
    Base exception for all AI Core service operations.

    ``usage_attempts`` carries the provider attempts made before the failure
    (billed ones included) so the caller can still account for what was paid.
    It is purely additive: it never affects ``is_retryable``.
    """
    is_retryable: bool = False

    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        raw_error: Optional[Exception] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.raw_error = raw_error
        self.metadata = metadata or {}
        self.usage_attempts: List["ProviderUsage"] = []

    def __str__(self) -> str:
        provider_str = self.provider or "UnknownProvider"
        model_str = f":{self.model}" if self.model else ""
        code_str = f" [HTTP {self.status_code}]" if self.status_code else ""
        return f"[{provider_str}{model_str}]{code_str} {self.message}"


# ==============================================================================
# RETRYABLE ERRORS (Transient failures: rate limits, timeouts, server 5xx)
# ==============================================================================

class RetryableAIError(AIError):
    """
    Base class for errors that can be safely retried.
    Transient issues that may resolve after a short backoff period.
    """
    is_retryable: bool = True


class AIRateLimitError(RetryableAIError):
    """
    Raised when provider returns HTTP 429 / Rate Limit Exceeded.
    """
    def __init__(
        self,
        message: str = "Rate limit exceeded by AI provider",
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(message, status_code=429, **kwargs)
        self.retry_after = retry_after


class AITimeoutError(RetryableAIError):
    """
    Raised when an API call or connection times out.
    """
    def __init__(self, message: str = "AI request timed out", **kwargs):
        super().__init__(message, status_code=kwargs.pop("status_code", 504), **kwargs)


class AIServerError(RetryableAIError):
    """
    Raised when an upstream provider returns 5xx internal server error (e.g. 500, 502, 503).
    """
    def __init__(self, message: str = "AI provider encountered an internal server error", status_code: int = 500, **kwargs):
        super().__init__(message, status_code=status_code, **kwargs)


class AIConnectionError(RetryableAIError):
    """
    Raised when network connectivity fails (DNS failure, connection reset, SSL handshake).
    """
    def __init__(self, message: str = "Failed to connect to AI provider", **kwargs):
        super().__init__(message, **kwargs)


# ==============================================================================
# NON-RETRYABLE ERRORS (Deterministic failures: auth, invalid input, policy)
# ==============================================================================

class NonRetryableAIError(AIError):
    """
    Base class for errors that must NOT be retried automatically.
    Retrying these will deterministically yield the same failure.
    """
    is_retryable: bool = False


class AIAuthenticationError(NonRetryableAIError):
    """
    Raised when API keys are invalid, missing, or lack required permissions (HTTP 401/403).
    """
    def __init__(self, message: str = "Authentication failed with AI provider", status_code: int = 401, **kwargs):
        super().__init__(message, status_code=status_code, **kwargs)


class AIInvalidRequestError(NonRetryableAIError):
    """
    Raised when request payload, prompt, or parameters are malformed (HTTP 400).
    """
    def __init__(self, message: str = "Invalid request payload to AI provider", status_code: int = 400, **kwargs):
        super().__init__(message, status_code=status_code, **kwargs)


class AIContentFilterError(NonRetryableAIError):
    """
    Raised when content violates provider safety, moderation, or trust policies.
    """
    def __init__(
        self,
        message: str = "Content blocked by AI provider safety filters",
        safety_ratings: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.safety_ratings = safety_ratings or {}


class AIQuotaExceededError(NonRetryableAIError):
    """
    Raised when account credits or hard quota are exhausted (HTTP 402 / out of credits).
    """
    def __init__(self, message: str = "AI provider credit or quota exhausted", status_code: int = 402, **kwargs):
        super().__init__(message, status_code=status_code, **kwargs)


class AIContractValidationError(NonRetryableAIError):
    """
    Raised when provider response cannot be validated against the expected Pydantic contract.
    """
    def __init__(
        self,
        message: str = "AI provider response failed schema validation",
        validation_errors: Optional[Any] = None,
        raw_response: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.validation_errors = validation_errors
        self.raw_response = raw_response


class AIEmptySelectionError(AIContractValidationError):
    """
    Raised when clip selection answered but no usable clip survived validation
    (empty list, every clip out of range, malformed or overlapping). Asking the
    same model again yields the same answer, so it is non-retryable; the
    selection provider still tries its fallback model once.
    """
    def __init__(self, message: str = "AI selection produced no usable clips", **kwargs):
        super().__init__(message, **kwargs)


class AINoSpeechDetectedError(AIContractValidationError):
    """
    Raised when transcription answered but found no speech at all (music,
    silence). Deterministic for the same audio, so it is non-retryable; selection
    must never run on an empty transcript.
    """
    def __init__(self, message: str = "No speech detected in the audio", **kwargs):
        super().__init__(message, **kwargs)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Client errors (4xx) that a later identical request can still succeed on.
_RETRYABLE_CLIENT_STATUSES = frozenset({408, 409, 425, 429})
_CONTENT_POLICY_MARKERS = ("contentpolicy", "contentfilter", "safety")


def non_retryable_error_class(exc: BaseException) -> Optional[type]:
    """
    The NonRetryableAIError subclass a provider exception maps to, or None when
    the caller's own rules (auth, rate limit, timeout, 5xx, connection) apply.

    A 4xx answer is the provider rejecting THIS request (malformed payload,
    unknown model, file too large, context window exceeded): retrying the same
    request only bills more attempts. 401/403 stay with the caller's auth
    mapping; 408/409/425/429 are transient and stay retryable.
    """
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _CONTENT_POLICY_MARKERS):
        return AIContentFilterError

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        return None
    if status_code == 402:
        return AIQuotaExceededError
    if (
        400 <= status_code < 500
        and status_code not in (401, 403)
        and status_code not in _RETRYABLE_CLIENT_STATUSES
    ):
        return AIInvalidRequestError
    return None


def is_retryable_error(exc: Exception) -> bool:
    """
    Determines whether a given exception represents a transient failure
    eligible for retry policies (e.g. Celery autoretry_for).
    """
    if isinstance(exc, AIError):
        return exc.is_retryable

    # Common transient built-in or network exceptions
    if isinstance(exc, (TimeoutError, ConnectionError, ConnectionResetError)):
        return True

    return False


def is_task_time_limit(exc: BaseException) -> bool:
    """
    True for Celery's soft time limit: the TASK's own deadline, not a provider
    failure. Providers must re-raise it untouched (never classify it as a
    retryable AIError, never answer it with a fallback call) so the pipeline
    fails the project instead of retrying with a fresh deadline.
    """
    try:
        from celery.exceptions import SoftTimeLimitExceeded
    except ImportError:  # pragma: no cover - celery is a hard dependency
        return False
    return isinstance(exc, SoftTimeLimitExceeded)


def is_billing_uncertain(exc: BaseException) -> bool:
    """
    True when a failed call may still have been billed upstream: a client-side
    timeout, a dropped connection or the task's time limit interrupting the
    call say nothing about whether the provider finished the work. Other
    failures (auth, 429, 5xx, validation) carry no charge.
    """
    return isinstance(exc, (AITimeoutError, AIConnectionError)) or is_task_time_limit(exc)
