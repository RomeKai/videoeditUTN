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
    app, to the logs or to error_log. For Upload-Post it is the profile's
    username (not a secret: the calls carry the account's API key). Empty
    while the profile is being created.
    `provider_id` is the provider's own id of the profile when it gives one
    (Ayrshare's refId, Upload-Post's username): not a secret, safe to log.

    `networks` is what the provider said the last time it was read
    ({platform: connected | expired | disconnected}, apps.integrations.social.accounts):
    the API checks a network and the onboarding step with it, without calling
    the provider. None for a profile copied from ayrshare_profile_key that was
    never read (it is taken as able to publish).

    `disconnected_at` ({platform: ISO date}) is when OneCreator unlinked each
    network: a read that still says "connected" right after it (the provider
    keeps its answer up to 60 s) is not believed. `linked_at` is the first read
    that showed a linked network: a profile without it for
    SOCIAL_UNLINKED_PROFILE_DAYS is deleted (clean_social_profiles), unless a
    connect link was asked for lately (`connect_requested_at`).

    Deleting the workspace marks the row (`orphaned_at`) and leaves it without
    the workspace (`workspace` null): the profile at the provider, a paid slot,
    is deleted afterwards by delete_orphan_social_profiles, which then deletes
    the row. Only rows marked so are deleted that way. One provider profile is
    one row: (provider, provider_ref) is unique when the ref is set.

    Replaces Workspace.ayrshare_profile_key (deprecated): migration 0002 copied
    the keys that existed.
    """

    class Provider(models.TextChoices):
        AYRSHARE = 'ayrshare', 'Ayrshare'
        UPLOAD_POST = 'upload_post', 'Upload-Post'

    workspace = models.OneToOneField(
        'users.Workspace', on_delete=models.SET_NULL, null=True, blank=True, related_name='social_profile',
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    provider_ref = models.CharField(max_length=255, blank=True, default='')
    provider_id = models.CharField(max_length=255, blank=True, default='')
    networks = models.JSONField(null=True, blank=True)
    networks_read_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.JSONField(null=True, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    connect_requested_at = models.DateTimeField(null=True, blank=True)
    orphaned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil de redes'
        verbose_name_plural = 'perfiles de redes'
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'provider_ref'], condition=~models.Q(provider_ref=''),
                name='integrations_one_row_per_provider_profile',
            ),
        ]

    def __str__(self):
        return f"{self.get_provider_display()} ({self.workspace_id})"

    @property
    def is_ready(self):
        """The provider created it: posts can go out through it."""
        return bool(self.provider_ref)
