import html
import logging
import time
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


# ADR-011 D-M6: the model is pinned so its category set cannot change silently.
DEFAULT_MODERATION_MODEL = "omni-moderation-latest"
# ADR-011 D-M5: flagged but frequent in gaming/streaming content ("lo maté",
# banter), so they are measured but never block. Any other flagged category
# blocks, including categories the API adds later.
DEFAULT_NON_BLOCKING_CATEGORIES = ("harassment", "violence", "self-harm", "illicit")


def _normalize_category(name) -> str:
    """API aliases ("self-harm/intent") and SDK field names ("self_harm_intent") compare equal."""
    return str(name).strip().lower().replace("/", "_").replace("-", "_")


def _flagged_categories(response) -> set:
    """
    Normalized names of the categories the verdict marks as true. Any shape
    error is a failure to moderate (never "safe"): it raises TypeError, which
    the caller classifies as a retryable ModerationError.
    """
    result = response.results[0]
    flagged = result.flagged
    if not isinstance(flagged, bool):
        raise TypeError("moderation verdict 'flagged' is not a bool")
    if not flagged:
        return set()

    categories = result.categories
    if hasattr(categories, "model_dump"):
        values = categories.model_dump(by_alias=True)
    elif categories is not None and hasattr(categories, "__dict__"):
        values = vars(categories)
    else:
        raise TypeError("moderation verdict has no categories")

    names = set()
    for name, value in values.items():
        if value is None:  # categories a model does not score
            continue
        if not isinstance(value, bool):
            raise TypeError("moderation category value is not a bool")
        if value:
            names.add(_normalize_category(name))
    # Flagged without any category: the verdict cannot be classified, so it
    # blocks (fail-closed) under a name that is never in the allow list.
    return names or {"unclassified"}


def split_into_blocks(text: str, max_chars: int) -> list:
    """
    Splits ``text`` into consecutive blocks of at most ``max_chars``, cut at
    the last whitespace inside each window so words are not split. A single
    token longer than the window is cut hard. Empty blocks are dropped.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    blocks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chars:
            blocks.append(remaining)
            break
        window = remaining[: max_chars + 1]
        cut = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
        if cut <= 0:
            cut = max_chars
        block = remaining[:cut].strip()
        if block:
            blocks.append(block)
        remaining = remaining[cut:].strip()
    return blocks


class AI_Security_Shield:
    """
    Middleware service to prevent Prompt Injection and ensure content safety.
    Acts as a firewall between user data and LLM services.
    """
    
    # Default block size (ADR-011 D-M1): the payload size this API already
    # moderated when only the first block was checked.
    MAX_CHARACTERS = 10000

    @staticmethod
    def check_content_safety(text_payload: str) -> bool:
        """
        Moderates the WHOLE text with the OpenAI Moderation API (ADR-011).

        - D-M1: the text is split into blocks of ``MODERATION_BLOCK_CHARS`` cut
          at whitespace; blocks are checked in order and the check stops at the
          first block that blocks.
        - D-M5: the verdict is per category. Categories listed in
          ``MODERATION_NON_BLOCKING_CATEGORIES`` (frequent in gaming content)
          never block; any other flagged category does, including categories
          the API adds later (fail-closed).
        - D-M4: no verdict for any block means the content does NOT pass
          (``ModerationError``, typed as retryable or not).
        - D-M7: logs carry block counts, latency and category names only.

        Raises UnsafeContentError when a block is blocked.
        """
        if not text_payload or not text_payload.strip():
            return True

        # Fail closed: without a moderation verdict the content is NOT allowed.
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            logger.error("🛡️ [SECURITY] Moderation misconfigured: OPENAI_API_KEY is not set")
            raise ModerationBlockedError(code=MODERATION_MISCONFIGURED)

        block_chars = int(
            getattr(settings, "MODERATION_BLOCK_CHARS", AI_Security_Shield.MAX_CHARACTERS)
        )
        blocks = split_into_blocks(text_payload, block_chars)
        model = getattr(settings, "MODERATION_MODEL", DEFAULT_MODERATION_MODEL)
        non_blocking = {
            _normalize_category(name)
            for name in getattr(
                settings, "MODERATION_NON_BLOCKING_CATEGORIES", DEFAULT_NON_BLOCKING_CATEGORIES
            )
        }

        started = time.monotonic()
        checked = 0
        flagged_seen = set()
        try:
            try:
                # Retries are owned by Celery, not by the SDK.
                client = OpenAI(
                    api_key=api_key,
                    timeout=float(settings.MODERATION_TIMEOUT_SECONDS),
                    max_retries=0,
                )
            except Exception as exc:
                raise _classify_moderation_failure(exc) from None
            for block in blocks:
                try:
                    response = client.moderations.create(input=block, model=model)
                    flagged = _flagged_categories(response)
                except Exception as exc:
                    raise _classify_moderation_failure(exc) from None
                checked += 1
                flagged_seen.update(flagged)
                blocking = sorted(flagged - non_blocking)
                if blocking:
                    logger.warning(
                        "🚨 [SECURITY] moderation.blocked block=%d/%d categories=%s latency_s=%.3f",
                        checked, len(blocks), ",".join(blocking), time.monotonic() - started,
                    )
                    raise UnsafeContentError(
                        "El contenido proporcionado infringe las políticas de seguridad "
                        "y no puede ser procesado."
                    )
        except ModerationError:
            logger.error(
                "🛡️ [SECURITY] moderation.failed blocks_checked=%d/%d latency_s=%.3f",
                checked, len(blocks), time.monotonic() - started,
            )
            raise

        logger.info(
            "🛡️ [SECURITY] moderation.passed blocks=%d latency_s=%.3f non_blocking_flagged=%s",
            len(blocks), time.monotonic() - started, ",".join(sorted(flagged_seen)) or "-",
        )
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
