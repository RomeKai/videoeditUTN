"""
Social publishing providers (ADR-005): the only code that talks to a
publishing service. The publisher (apps/videos/tasks/social.py), the API's
checks and the endpoints that connect networks go through this package, in
our own terms (base.py) and with our own error codes (errors.py).

The provider of a workspace is the one recorded on its SocialProfile
(apps/integrations/models.py): provider_for(profile). SOCIAL_PROVIDER only
chooses the provider new profiles are created with (provider_for_new_profiles).

Today: Ayrshare (ayrshare.py). A second provider (Upload-Post) implements
SocialProvider and is added to _PROVIDERS.
"""
from django.conf import settings

from .base import (  # noqa: F401  (the package's API)
    ADMIN_TIMEOUT_SECONDS,
    INSTAGRAM_REELS,
    PLATFORMS,
    PUBLISH_LOCK_TTL_SECONDS,
    PUBLISH_TIMEOUT_SECONDS,
    TIKTOK,
    TIKTOK_FLAGS,
    TIKTOK_PRIVACY_LEVELS,
    YOUTUBE_SHORTS,
    Account,
    AccountStatus,
    CreatedProfile,
    PublishRequest,
    PublishResult,
    SocialProvider,
    TikTokOptions,
    idempotency_key,
)
from .errors import (  # noqa: F401
    PUBLISH_ERROR_CODES,
    OutcomeUnknownError,
    PermanentError,
    PROVIDER_NOT_CONFIGURED,
    RetryableError,
    SocialProviderError,
)


def _providers():
    from .ayrshare import AyrshareProvider

    return {AyrshareProvider.name: AyrshareProvider}


def get_provider(name):
    """The provider called ``name``; one this release does not know is a configuration error."""
    provider = _providers().get(name)
    if provider is None:
        raise PermanentError(PROVIDER_NOT_CONFIGURED, f'unknown social provider {name!r}')
    return provider()


def provider_for(profile):
    """The provider that holds ``profile`` (a SocialProfile): its posts go out through it."""
    return get_provider(profile.provider)


def provider_for_new_profiles():
    return get_provider(settings.SOCIAL_PROVIDER)


def ready_profile(workspace_id):
    """The workspace's SocialProfile once the provider created it, or None. No call to the provider."""
    from apps.integrations.models import SocialProfile

    return SocialProfile.objects.filter(workspace_id=workspace_id).exclude(provider_ref='').first()


def has_ready_profile(workspace_id):
    from apps.integrations.models import SocialProfile

    return SocialProfile.objects.filter(workspace_id=workspace_id).exclude(provider_ref='').exists()
