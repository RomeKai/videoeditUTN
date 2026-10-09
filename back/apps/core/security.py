import logging
import html
from django.conf import settings
from django.core.exceptions import ValidationError
from openai import OpenAI

logger = logging.getLogger(__name__)

class UnsafeContentError(ValidationError):
    """Exception raised when content fails safety or moderation checks."""
    pass

MODERATION_UNAVAILABLE = "moderation_unavailable"
MODERATION_MISCONFIGURED = "moderation_misconfigured"
MODERATION_QUOTA = "moderation_quota"

# HTTP statuses that may succeed on a later attempt.
_RETRYABLE_STATUSES = frozenset({408, 409, 429})


class ModerationError(Exception):
    """No moderation verdict could be obtained. Content must NOT be treated as safe.

    ``code`` is a short static identifier safe to persist or log; the message
    never carries provider text or user content.
    """

    retryable: bool = False
    default_code: str = MODERATION_MISCONFIGURED

    def __init__(self, code: str | None = None):
        self.code = code or self.default_code
        super().__init__(self.code)


class ModerationUnavailableError(ModerationError):
    """Transient: timeout, connection, 5xx, rate limit or unexpected failure."""

    retryable = True
    default_code = MODERATION_UNAVAILABLE


class ModerationBlockedError(ModerationError):
    """Permanent: missing/invalid key, forbidden, bad request or exhausted quota."""

    retryable = False
    default_code = MODERATION_MISCONFIGURED


def _classify_moderation_failure(exc: Exception) -> ModerationError:
    """Maps a failure of the moderation call to a typed error. Logs class/status only."""
    from openai import APIStatusError

    status = exc.status_code if isinstance(exc, APIStatusError) else None
    logger.error(
        "⚠️ [SECURITY] Moderation API error: %s status=%s", type(exc).__name__, status
    )

    if status is None:
        return ModerationUnavailableError()
    if status == 429 and getattr(exc, "code", None) == "insufficient_quota":
        return ModerationBlockedError(code=MODERATION_QUOTA)
    if status >= 500 or status in _RETRYABLE_STATUSES:
        return ModerationUnavailableError()
    return ModerationBlockedError(code=MODERATION_MISCONFIGURED)


class AI_Security_Shield:
    """
    Middleware service to prevent Prompt Injection and ensure content safety.
    Acts as a firewall between user data and LLM services.
    """
    
    MAX_CHARACTERS = 10000

    @staticmethod
    def check_content_safety(text_payload: str) -> bool:
        """
        Calls OpenAI Moderation API to detect illegal or harmful content.
        Raises UnsafeContentError if flagged.
        """
        if not text_payload:
            return True

        # 1. Preventive Truncation
        safe_payload = text_payload[:AI_Security_Shield.MAX_CHARACTERS]

        # Fail closed: without a moderation verdict the content is NOT allowed.
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            logger.error("🛡️ [SECURITY] Moderation misconfigured: OPENAI_API_KEY is not set")
            raise ModerationBlockedError(code=MODERATION_MISCONFIGURED)

        try:
            # Retries are owned by Celery, not by the SDK.
            client = OpenAI(
                api_key=api_key,
                timeout=float(settings.MODERATION_TIMEOUT_SECONDS),
                max_retries=0,
            )
            logger.info("🛡️ [SECURITY] Checking content safety via OpenAI Moderation...")

            response = client.moderations.create(input=safe_payload)
            result = response.results[0]
            # Any shape error in the verdict is a failure to moderate, never "safe".
            flagged = result.flagged
            if not isinstance(flagged, bool):
                raise TypeError("moderation verdict 'flagged' is not a bool")
            categories = result.categories
        except Exception as exc:
            raise _classify_moderation_failure(exc) from None

        if flagged:
            logger.warning(f"🚨 [SECURITY] Content flagged as unsafe: {categories}")
            raise UnsafeContentError("El contenido proporcionado infringe las políticas de seguridad y no puede ser procesado.")

        return True

    @staticmethod
    def isolate_user_input(raw_text: str) -> str:
        """
        Sanitizes text to prevent XML/Tag-based Prompt Injection.
        Escapes < > and wraps in isolation tags.
        """
        if not raw_text:
            return "<user_input></user_input>"
            
        # 1. Escape XML characters
        # Replaces < with &lt; and > with &gt;
        sanitized = html.escape(raw_text)
        
        # 2. Wrap in protective tags
        return f"<user_input>\n{sanitized}\n</user_input>"
