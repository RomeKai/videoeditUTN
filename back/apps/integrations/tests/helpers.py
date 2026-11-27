"""Helpers for tests that need a workspace with (or without) its profile at the publishing provider."""
from apps.integrations.models import SocialProfile
from apps.integrations.social import PublishResult

# Where tests replace the network: the provider's publish (no HTTP).
PUBLISH = "apps.integrations.social.ayrshare.AyrshareProvider.publish"
PUBLISHED = PublishResult(external_id="ayr-1", post_url="https://www.instagram.com/reel/abc/")


def give_social_profile(workspace, ref="perfil"):
    """The workspace has its Ayrshare profile (what SocialProfile's data migration made of ayrshare_profile_key)."""
    SocialProfile.objects.update_or_create(
        workspace_id=workspace.pk, defaults={"provider": SocialProfile.Provider.AYRSHARE, "provider_ref": ref},
    )


def take_social_profile(workspace):
    SocialProfile.objects.filter(workspace_id=workspace.pk).delete()
