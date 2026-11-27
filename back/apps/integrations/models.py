from django.db import models


class SocialProfile(models.Model):
    """
    The workspace's profile at the social publishing provider (ADR-005): the
    networks a workspace connects live there, and every post goes out through
    it (apps.integrations.social).

    `provider` is the provider that holds the profile, and the one its posts
    go out through: SOCIAL_PROVIDER only chooses the provider of new profiles,
    so changing it never moves a workspace that already has one.

    `provider_ref` is what the provider needs to act on the profile. For
    Ayrshare it is the Profile-Key, a credential: it never goes to the web
    app, to the logs or to error_log. Empty while the profile is being created.
    `provider_id` is the provider's own id of the profile when it gives one
    (Ayrshare's refId): not a secret, safe to log.

    Replaces Workspace.ayrshare_profile_key (deprecated): migration 0002 copied
    the keys that existed.
    """

    class Provider(models.TextChoices):
        AYRSHARE = 'ayrshare', 'Ayrshare'

    workspace = models.OneToOneField(
        'users.Workspace', on_delete=models.CASCADE, related_name='social_profile',
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    provider_ref = models.CharField(max_length=255, blank=True, default='')
    provider_id = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil de redes'
        verbose_name_plural = 'perfiles de redes'

    def __str__(self):
        return f"{self.get_provider_display()} ({self.workspace_id})"

    @property
    def is_ready(self):
        """The provider created it: posts can go out through it."""
        return bool(self.provider_ref)
