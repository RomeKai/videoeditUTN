"""
The networks of a workspace (gap 10 of documents/producto/mapa-de-pantallas.md):
its profile at the provider, created the first time it connects a network,
and the accounts linked to it. Used by /api/v1/social-accounts/
(apps/videos/views_social_accounts.py), the posts API's account check, the
onboarding step, the workspace delete and `manage.py social_smoke`.

Locks: only the SocialProfile row. Nothing here locks the workspace's row
(the workspace delete locks its projects, then the workspace, then this row).
"""
import logging
from dataclasses import asdict

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import PLATFORMS, provider_for, provider_for_new_profiles, ready_profile
from .base import Account, AccountStatus
from .errors import ACCOUNT_NOT_CONNECTED, PermanentError

logger = logging.getLogger(__name__)

# How long the provider's list of accounts is reused. Short: Ayrshare keeps its own
# for up to 60 s, and the linking window of a network refreshes it when it closes.
# Connecting and disconnecting forget it. It bounds the calls of a screen that asks
# again and again (300 requests per 5 minutes per profile at Ayrshare).
ACCOUNTS_CACHE_SECONDS = 15


class WorkspaceGone(Exception):
    """The workspace was deleted while its profile was being created."""


def _cache_key(workspace_id):
    return f"social:accounts:{workspace_id}"


def forget_accounts(workspace_id):
    try:
        cache.delete(_cache_key(workspace_id))
    except Exception as exc:  # the cache may be down: it expires on its own
        logger.warning("social.accounts_not_forgotten workspace_id=%s error=%s", workspace_id, type(exc).__name__)


def ensure_profile(workspace):
    """
    The workspace's SocialProfile, created at the provider the first time.
    Two first connects at once create one profile: the row is claimed first
    (one per workspace), and the provider is called under its row lock, so the
    second request waits there and then finds the profile made. A failed
    creation leaves the row without its ref, and the next connect tries again.
    """
    from apps.integrations.models import SocialProfile

    provider = provider_for_new_profiles()  # an unknown SOCIAL_PROVIDER fails before any row
    try:
        with transaction.atomic():
            SocialProfile.objects.get_or_create(workspace_id=workspace.pk, defaults={'provider': provider.name})
    except IntegrityError as exc:
        raise WorkspaceGone() from exc
    with transaction.atomic():
        profile = SocialProfile.objects.select_for_update().filter(workspace_id=workspace.pk).first()
        if profile is None:
            raise WorkspaceGone()
        if profile.provider_ref:
            return profile
        created = provider_for(profile).create_profile(workspace)
        profile.provider_ref, profile.provider_id = created.ref, created.provider_id
        # Nothing linked yet: the API knows it without asking.
        profile.networks, profile.networks_read_at = {}, timezone.now()
        profile.save(update_fields=['provider_ref', 'provider_id', 'networks', 'networks_read_at', 'updated_at'])
    logger.info(
        "social.profile_created workspace_id=%s provider=%s provider_id=%s",
        workspace.pk, profile.provider, profile.provider_id,
    )
    _mirror_legacy_key(workspace.pk, profile)
    return profile


def _mirror_legacy_key(workspace_id, profile):
    """
    A worker of the release before SocialProfile reads Workspace.ayrshare_profile_key
    until it restarts: the key goes there too while that column exists. Its own
    short statement, after the profile's transaction: no lock is held with it.
    """
    from apps.users.models import Workspace

    if profile.provider != 'ayrshare':
        return
    try:
        Workspace.objects.filter(pk=workspace_id).update(ayrshare_profile_key=profile.provider_ref)
    except Exception as exc:
        logger.warning("social.legacy_key_not_kept workspace_id=%s error=%s", workspace_id, type(exc).__name__)


def _forget_lost_profile(profile):
    """The provider no longer has the profile: the row loses its ref, and the next connect creates another."""
    from apps.integrations.models import SocialProfile

    cleared = SocialProfile.objects.filter(pk=profile.pk, provider_ref=profile.provider_ref).update(
        provider_ref='', provider_id='', networks=None, networks_read_at=None, updated_at=timezone.now(),
    )
    logger.warning("social.profile_lost workspace_id=%s cleared=%s", profile.workspace_id, cleared)


def connect_url(workspace, platform, return_url):
    """The URL of the provider's page to link ``platform``. Creates the profile the first time."""
    profile = ensure_profile(workspace)
    try:
        url = provider_for(profile).connect_url(profile, platform, return_url)
    except PermanentError as exc:
        if exc.code != ACCOUNT_NOT_CONNECTED:
            raise
        # Deleted at the provider (by hand, or by an earlier cleanup): a new one, once.
        _forget_lost_profile(profile)
        profile = ensure_profile(workspace)
        url = provider_for(profile).connect_url(profile, platform, return_url)
    forget_accounts(workspace.pk)
    logger.info("social.connect_url workspace_id=%s platform=%s", workspace.pk, platform)
    return url


def accounts(workspace, fresh=False):
    """
    One Account per network (PLATFORMS). Without a profile, all disconnected,
    without calling the provider. ``fresh`` skips the short cache. Every read
    of the provider is kept on the profile (SocialProfile.networks).
    """
    profile = ready_profile(workspace.pk)
    if profile is None:
        return [Account(platform, AccountStatus.DISCONNECTED) for platform in PLATFORMS]
    key = _cache_key(workspace.pk)
    if not fresh:
        try:
            cached = cache.get(key)
        except Exception:
            cached = None
        if isinstance(cached, list) and len(cached) == len(PLATFORMS):
            return [Account(**item) for item in cached]
    try:
        found = provider_for(profile).accounts(profile)
    except PermanentError as exc:
        if exc.code != ACCOUNT_NOT_CONNECTED:
            raise
        _forget_lost_profile(profile)
        return [Account(platform, AccountStatus.DISCONNECTED) for platform in PLATFORMS]
    _keep(profile, found)
    try:
        cache.set(key, [asdict(account) for account in found], ACCOUNTS_CACHE_SECONDS)
    except Exception as exc:
        logger.warning("social.accounts_not_cached workspace_id=%s error=%s", workspace.pk, type(exc).__name__)
    return found


def _keep(profile, found):
    from apps.integrations.models import SocialProfile

    networks = {account.platform: account.status for account in found}
    SocialProfile.objects.filter(pk=profile.pk, provider_ref=profile.provider_ref).update(
        networks=networks, networks_read_at=timezone.now(),
    )


def disconnect(workspace, platform):
    """Unlinks ``platform`` at the provider. Without a profile (or one the provider lost) there is nothing to unlink."""
    from apps.integrations.models import SocialProfile

    profile = ready_profile(workspace.pk)
    if profile is not None:
        try:
            provider_for(profile).disconnect(profile, platform)
        except PermanentError as exc:
            if exc.code != ACCOUNT_NOT_CONNECTED:
                raise
        with transaction.atomic():
            locked = SocialProfile.objects.select_for_update().filter(pk=profile.pk).first()
            if locked is not None and locked.networks is not None:
                locked.networks = {**locked.networks, platform: AccountStatus.DISCONNECTED}
                locked.save(update_fields=['networks', 'updated_at'])
    forget_accounts(workspace.pk)
    logger.info("social.disconnected workspace_id=%s platform=%s", workspace.pk, platform)


def network_status(workspace_id, platform):
    """
    What the API knows of a network without calling the provider: connected,
    expired or disconnected, from the last read (SocialProfile.networks).
    A profile never read (copied from ayrshare_profile_key) counts as connected.
    """
    from apps.integrations.models import SocialProfile

    row = SocialProfile.objects.filter(workspace_id=workspace_id).exclude(provider_ref='').values('networks').first()
    if row is None:
        return AccountStatus.DISCONNECTED
    if row['networks'] is None:
        return AccountStatus.CONNECTED
    return row['networks'].get(platform, AccountStatus.DISCONNECTED)


def has_connected_network(workspace_id):
    """The onboarding step "Conectar una red": at least one network connected, by the last read."""
    from apps.integrations.models import SocialProfile

    row = SocialProfile.objects.filter(workspace_id=workspace_id).exclude(provider_ref='').values('networks').first()
    if row is None:
        return False
    if row['networks'] is None:
        return True
    return any(status == AccountStatus.CONNECTED for status in row['networks'].values())


def delete_orphan_profiles():
    """
    The profiles whose workspace was deleted: each one is deleted at the
    provider (a paid slot) and then its row. One that fails stays for the next
    run. Returns (deleted, kept).
    """
    from apps.integrations.models import SocialProfile

    from .errors import SocialProviderError

    deleted = kept = 0
    for profile_id in list(SocialProfile.objects.filter(workspace__isnull=True).values_list('pk', flat=True)):
        with transaction.atomic():
            profile = (
                SocialProfile.objects.select_for_update(skip_locked=True)
                .filter(pk=profile_id, workspace__isnull=True).first()
            )
            if profile is None:
                continue
            if profile.provider_ref:
                try:
                    provider_for(profile).delete_profile(profile)
                except SocialProviderError as exc:
                    logger.warning(
                        "social.orphan_kept profile_id=%s code=%s detail=%s", profile.pk, exc.code, exc.detail,
                    )
                    kept += 1
                    continue
            profile.delete()
            deleted += 1
            logger.info("social.orphan_deleted profile_id=%s provider_id=%s", profile_id, profile.provider_id)
    return deleted, kept


def profile_title_hint():
    """For the runbook and the smoke command: how the provider names this environment's profiles."""
    return f"{settings.SOCIAL_PROFILE_TITLE_PREFIX}-<id del espacio>"
