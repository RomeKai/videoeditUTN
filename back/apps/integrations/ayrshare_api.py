import requests
import logging
from django.conf import settings
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

class AyrshareAPIError(Exception):
    """Exception raised when the Ayrshare API returns an error."""
    pass

class AyrshareClient:
    """
    Official API Client for Ayrshare.
    Handles secure social media distribution to TikTok, Instagram, and YouTube.
    """
    
    BASE_URL = "https://app.ayrshare.com/api"

    @classmethod
    def get_headers(cls):
        """Returns required headers with API Key."""
        api_key = getattr(settings, 'AYRSHARE_API_KEY', None)
        if not api_key:
            logger.error("❌ AYRSHARE_API_KEY not found in settings.")
            raise AyrshareAPIError("Ayrshare API Key is missing.")
            
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    @classmethod
    def send_post(cls, profile_key: str, s3_media_url: str, caption: str, platforms: List[str]) -> Dict[str, Any]:
        """
        Sends a POST request to Ayrshare to publish content.
        """
        endpoint = f"{cls.BASE_URL}/post"
        
        payload = {
            "post": caption,
            "platforms": platforms,
            "mediaUrls": [s3_media_url],
            "profileKeys": [profile_key]
        }
        
        try:
            logger.info(f"📤 [AYRSHARE] Sending post to platforms: {platforms}...")
            response = requests.post(endpoint, json=payload, headers=cls.get_headers())
            
            # Handle HTTP Errors
            if response.status_code >= 400:
                error_data = response.json()
                logger.error(f"❌ [AYRSHARE] API Error ({response.status_code}): {error_data}")
                raise AyrshareAPIError(f"Ayrshare error: {error_data.get('message', 'Unknown error')}")

            result = response.json()
            logger.info(f"✅ [AYRSHARE] Success! External ID: {result.get('id')}")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [AYRSHARE] Connection Error: {e}")
            raise AyrshareAPIError(f"Connection failed: {e}")
