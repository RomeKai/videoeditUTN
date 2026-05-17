import logging
import html
from django.conf import settings
from django.core.exceptions import ValidationError
from openai import OpenAI

logger = logging.getLogger(__name__)

class UnsafeContentError(ValidationError):
    """Exception raised when content fails safety or moderation checks."""
    pass

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
        
        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            logger.info("🛡️ [SECURITY] Checking content safety via OpenAI Moderation...")
            
            response = client.moderations.create(input=safe_payload)
            result = response.results[0]
            
            if result.flagged:
                logger.warning(f"🚨 [SECURITY] Content flagged as unsafe: {result.categories}")
                raise UnsafeContentError("El contenido proporcionado infringe las políticas de seguridad y no puede ser procesado.")
            
            return True

        except UnsafeContentError:
            raise
        except Exception as e:
            logger.error(f"⚠️ [SECURITY] Moderation API Error: {e}")
            # In case of API failure, we fail-safe (allow) or fail-secure (block).
            # For a SaaS, we'll allow but log the error unless it's a critical safety requirement.
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
