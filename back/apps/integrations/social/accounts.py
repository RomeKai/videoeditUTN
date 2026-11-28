"""
The networks of a workspace (gap 10 of documents/producto/mapa-de-pantallas.md):
its profile at the provider, created the first time it connects a network,
and the accounts linked to it. Used by /api/v1/social-accounts/
(apps/videos/views_social_accounts.py), the posts API's account check, the
onboarding step, the workspace delete, the daily cleanup and
`manage.py social_smoke`.

Each profile is a paid slot at the provider, shared by the whole platform
(Ayrshare's plans cap them). So connecting asks for a plan that includes it
(SubscriptionPlan.allow_social_accounts), a verified email, and room under
SOCIAL_MAX_PROFILES; and a profile nobody linked is deleted after
SOCIAL_UNLINKED_PROFILE_DAYS.

Locks: the SocialProfile row, and an advisory lock around creating a profile
(the global cap). Nothing here locks the workspace's row (the workspace delete
locks its projects, then the workspace, then this row).
"""
import logging
import zlib
from dataclasses import asdict, fields
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import PLATFORMS, provider_for, provider_for_new_profiles, ready_profile
from .base import Account, AccountStatus
from .errors import PROFILE_NOT_FOUND, PROFILE_TITLE_TAKEN, PermanentError, RetryableError, SocialProviderError

logger = logging.getLogger(__name__)

# How long the provider's list of accounts is reused. Short: Ayrshare keeps its own
# for up to 60 s, and the linking window of a network refreshes it when it closes.
# Connecting and disconnecting forget it. It bounds the calls of a screen that asks
# again and again (300 requests per 5 minutes per profile at Ayrshare).
ACCOUNTS_CACHE_SECONDS = 15
# A failed read is not tried again for this long: a slow or failing provider must not
# take a web worker per request (each read can wait ACCOUNTS_TIMEOUT_SECONDS).
ACCOUNTS_FAILURE_SECONDS = 15
# One read of the provider per workspace at a time; the others get the last one.
ACCOUNTS_READING_SECONDS = 30
# The last full answer, for those who did not read (usernames and dates included).
ACCOUNTS_LAST_SECONDS = 24 * 3600
# A network OneCreator just unlinked: a read that still says "connected" is the provider's old answer.
DISCONNECT_GRACE = timedelta(seconds=60)
# A profile whose connect link was asked for this recently may be being linked right now (the
# provider keeps its list of accounts up to 60 s): the cleanup of never-linked profiles skips it.
CONNECT_IN_PROGRESS = timedelta(hours=1)

# Namespace of the advisory lock around creating a profile: the global cap counts one creation at a time.
PROFILES_LOCK_NAMESPACE = zlib.crc32(b'integrations.social.profiles') - 2 ** 31


class WorkspaceGone(Exception):
    """The workspace was deleted while its profile was being created."""


class ConnectRefused(Exception):
    """Connecting is not allowed here: the view answers ``status`` with ``{error, code}``."""

    def __init__(self, status, code, message):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


PLAN_DENIED = "Tu plan no incluye conectar redes. Pasate a un plan pago para conectar las cuentas."
EMAIL_NOT_VERIFIED = "Verificá tu email para conectar redes."
PROFILES_LIMIT = "Por ahora no podemos conectar más espacios a las redes. Escribinos y lo resolvemos."
TITLE_TAKEN = "No pudimos preparar la conexión de redes de este espacio. Escribinos y lo resolvemos."


def _key(workspace_id, kind='accounts'):
    return f"social:{kind}:{workspace_id}"


def _cache_get(key):
    try:
        return cache.get(key)
    except Exception:
        return None


def _cache_set(key, value, seconds):
    try:
        cache.set(key, value, seconds)
    except Exception as exc:
        logger.warning("social.cache_not_written key=%s error=%s", key.split(':')[1], type(exc).__name__)


def forget_accounts(workspace_id):
    for kind in ('accounts', 'accounts_failed'):
        try:
            cache.delete(_key(workspace_id, kind))
        except Exception as exc:  # the cache may be down: it expires on its own
            logger.warning("social.accounts_not_forgotten workspace_id=%s error=%s", workspace_id, type(exc).__name__)


# --- Connecting ---

def check_plan(workspace):
    """The plan includes connecting networks (`allow_social_accounts`; no plan means no plan limits, as everywhere)."""
    plan = workspace.subscription_plan
    if plan is not None and not plan.allow_social_accounts:
        raise ConnectRefused(403, 'social_accounts', PLAN_DENIED)


def check_can_connect(workspace, user):
    """
    The plan includes it (check_plan) and the user's email is verified. The
    global cap is checked when a profile is created (ensure_profile).
    """
    check_plan(workspace)
    if not getattr(user, 'is_email_verified', False):
        raise ConnectRefused(403, 'email_not_verified', EMAIL_NOT_VERIFIED)


def profiles_cap_reached():
    """Whether OneCreator already has SOCIAL_MAX_PROFILES profiles at the provider (the rows with a key)."""
    from apps.integrations.models import SocialProfile

    return SocialProfile.objects.exclude(provider_ref='').count() >= settings.SOCIAL_MAX_PROFILES


def _lock_profile_creation():
    """Transaction-scoped advisory lock, on PostgreSQL only (SQLite writes one transaction at a time)."""
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [PROFILES_LOCK_NAMESPACE, 0])


def ensure_profile(workspace):
    """
    The workspace's SocialProfile, created at the provider the first time.
    Two first connects at once create one profile: the row is claimed first
    (one per workspace), and the provider is called under its row lock, so the
    second request waits there and then finds the profile made. A failed
    creation leaves the row without its ref, and the next connect tries again.
    Creating one waits for the others (an advisory lock) and counts the
    profiles first: never more than SOCIAL_MAX_PROFILES. Each call marks the
    row (`connect_requested_at`): a link is on its way. A row deleted while
    this waited for its lock (the cleanup of a never-linked profile) is
    claimed again, once.
    """
    from apps.integrations.models import SocialProfile

    provider = provider_for_new_profiles()  # an unknown SOCIAL_PROVIDER fails before any row
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                SocialProfile.objects.get_or_create(workspace_id=workspace.pk, defaults={'provider': provider.name})
        except IntegrityError as exc:
            raise WorkspaceGone() from exc
        with transaction.atomic():
            profile = SocialProfile.objects.select_for_update().filter(workspace_id=workspace.pk).first()
            if profile is None:
                if attempt == 1:
                    # Deleted while this waited for its lock (the cleanup of a never-linked profile): claim it again.
                    continue
                raise WorkspaceGone()
            # A link is on its way: the cleanup of never-linked profiles leaves this one alone for a while.
            profile.connect_requested_at = timezone.now()
            profile.save(update_fields=['connect_requested_at', 'updated_at'])
            if profile.provider_ref:
                return profile
            _create_at_provider(workspace, profile)
        break
    logger.info(
        "social.profile_created workspace_id=%s provider=%s provider_id=%s",
        workspace.pk, profile.provider, profile.provider_id,
    )
    _mirror_legacy_key(workspace.pk, profile)
    return profile


def _create_at_provider(workspace, profile):
    """
    Creates the profile at the provider and keeps its key on ``profile``. Called
    under the row's lock, in ensure_profile's transaction: waits for the other
    creations (an advisory lock) and counts the profiles first.
    """
    _lock_profile_creation()
    if profiles_cap_reached():
        logger.error("social.profiles_limit workspace_id=%s limit=%s", workspace.pk, settings.SOCIAL_MAX_PROFILES)
        raise ConnectRefused(409, 'social_profiles_limit', PROFILES_LIMIT)
    try:
        created = provider_for(profile).create_profile(workspace)
    except PermanentError as exc:
        if exc.code == PROFILE_TITLE_TAKEN:
            # A creation whose answer was lost made it: its key is only in the provider's dashboard.
            logger.error("social.profile_title_taken workspace_id=%s detail=%s", workspace.pk, exc.detail)
            raise ConnectRefused(409, PROFILE_TITLE_TAKEN, TITLE_TAKEN) from exc
        raise
    profile.provider_ref, profile.provider_id = created.ref, created.provider_id
    # Nothing linked yet: the API knows it without asking.
    profile.networks, profile.networks_read_at = {}, timezone.now()
    profile.save(update_fields=['provider_ref', 'provider_id', 'networks', 'networks_read_at', 'updated_at'])


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
    """
    The provider says it has no profile with this key (Ayrshare's 144): the row
    loses its ref, and the next connect creates another. Only from a connect
    (Owner or Admin), never from a read: the key is given only once.
    """
    from apps.integrations.models import SocialProfile

    cleared = SocialProfile.objects.filter(pk=profile.pk, provider_ref=profile.provider_ref).update(
        provider_ref='', provider_id='', networks=None, networks_read_at=None, updated_at=timezone.now(),
    )
    logger.error("social.profile_lost workspace_id=%s cleared=%s", profile.workspace_id, cleared)


def connect_url(workspace, platform, return_url):
    """The URL of the provider's page to link ``platform``. Creates the profile the first time."""
    from apps.integrations.models import SocialProfile

    profile = ensure_profile(workspace)
    try:
        url = provider_for(profile).connect_url(profile, platform, return_url)
    except PermanentError as exc:
        if exc.code != PROFILE_NOT_FOUND:
            raise
        # Deleted at the provider (by hand, or by an earlier cleanup): a new one, once.
        _forget_lost_profile(profile)
        profile = ensure_profile(workspace)
        url = provider_for(profile).connect_url(profile, platform, return_url)
    with transaction.atomic():
        # The user links it again: a "connected" read from now on is believed.
        locked = SocialProfile.objects.select_for_update().filter(pk=profile.pk).first()
        if locked is not None and (locked.disconnected_at or {}).get(platform):
            locked.disconnected_at = {key: value for key, value in locked.disconnected_at.items() if key != platform}
            locked.save(update_fields=['disconnected_at', 'updated_at'])
    forget_accounts(workspace.pk)
    logger.info("social.connect_url workspace_id=%s platform=%s", workspace.pk, platform)
    return url


# --- Reading the accounts ---

# The fields every release's Account has: a process of the release before Account.tiktok reads the
# same cache during a deploy, and Account(**item) fails on a key it does not know.
_FIELDS_BEFORE_TIKTOK = ('platform', 'status', 'username', 'connected_at')


def _stored(account):
    """An Account as the cache keeps it: its extra fields only when set (Ayrshare never sets them)."""
    item = asdict(account)
    return {key: value for key, value in item.items() if key in _FIELDS_BEFORE_TIKTOK or value is not None}


def _account(item):
    """An Account from the cache, whatever release wrote it."""
    known = {field.name for field in fields(Account)}
    return Account(**{key: value for key, value in item.items() if key in known})


def _all(status):
    return [Account(platform, status) for platform in PLATFORMS]


def _last_known(profile):
    """The last full answer, or the statuses kept on the profile (without names), or None."""
    last = _cache_get(_key(profile.workspace_id, 'accounts_last'))
    if isinstance(last, list) and len(last) == len(PLATFORMS):
        return [_account(item) for item in last]
    if profile.networks is None:
        return None
    return [Account(platform, profile.networks.get(platform, AccountStatus.DISCONNECTED)) for platform in PLATFORMS]


def accounts(workspace, fresh=False):
    """
    One Account per network (PLATFORMS). Without a profile, all disconnected,
    without calling the provider. Otherwise the provider, at most every
    ACCOUNTS_CACHE_SECONDS (``fresh`` skips that copy), one read per workspace
    at a time (the others get the last answer), and a failure is not tried
    again for ACCOUNTS_FAILURE_SECONDS (the last answer meanwhile, or the
    error). Every read is kept on the profile (SocialProfile.networks).
    """
    profile = ready_profile(workspace.pk)
    if profile is None:
        return _all(AccountStatus.DISCONNECTED)
    if not fresh:
        cached = _cache_get(_key(workspace.pk))
        if isinstance(cached, list) and len(cached) == len(PLATFORMS):
            return [_account(item) for item in cached]
        failed = _cache_get(_key(workspace.pk, 'accounts_failed'))
        if failed:
            last = _last_known(profile)
            if last is not None:
                return last
            raise RetryableError(failed, 'a recent read failed')
    reading = _key(workspace.pk, 'accounts_reading')
    try:
        # False only when another read holds the key. The fail-open cache of production answers None,
        # without raising, while Redis is down: without the cache there is nobody to share the read with.
        mine = cache.add(reading, 1, ACCOUNTS_READING_SECONDS) is not False
    except Exception:
        mine = True
    if not mine:
        last = _last_known(profile)
        if last is not None:
            return last
        raise RetryableError(detail='another read is on its way')
    try:
        found = provider_for(profile).accounts(profile)
    except PermanentError as exc:
        if exc.code != PROFILE_NOT_FOUND:
            _cache_set(_key(workspace.pk, 'accounts_failed'), exc.code, ACCOUNTS_FAILURE_SECONDS)
            raise
        # The key is kept (a wrong setting must not wipe every workspace's key while people browse):
        # nothing publishes until an Owner or an Admin connects again, which makes a new profile.
        logger.error("social.profile_not_found workspace_id=%s detail=%s", workspace.pk, exc.detail)
        found = _all(AccountStatus.DISCONNECTED)
    except SocialProviderError as exc:
        _cache_set(_key(workspace.pk, 'accounts_failed'), exc.code, ACCOUNTS_FAILURE_SECONDS)
        raise
    finally:
        try:
            cache.delete(reading)
        except Exception:
            pass
    found = _keep(profile, found)
    stored = [_stored(account) for account in found]
    _cache_set(_key(workspace.pk), stored, ACCOUNTS_CACHE_SECONDS)
    _cache_set(_key(workspace.pk, 'accounts_last'), stored, ACCOUNTS_LAST_SECONDS)
    return found


def _recently_unlinked(disconnected_at, platform, now):
    when = parse_datetime((disconnected_at or {}).get(platform) or '')
    return when is not None and when > now - DISCONNECT_GRACE


def _keep(profile, found):
    """
    Keeps the read on the profile, under its row lock, so a disconnect that
    lands meanwhile is never undone: a network unlinked less than
    DISCONNECT_GRACE ago stays disconnected whatever the provider still says.
    Returns the accounts as kept.
    """
    from apps.integrations.models import SocialProfile

    now = timezone.now()
    with transaction.atomic():
        locked = SocialProfile.objects.select_for_update().filter(pk=profile.pk, provider_ref=profile.provider_ref).first()
        if locked is None:
            return found
        kept = [
            Account(account.platform, AccountStatus.DISCONNECTED)
            if account.status != AccountStatus.DISCONNECTED and _recently_unlinked(locked.disconnected_at, account.platform, now)
            else account
            for account in found
        ]
        locked.networks = {account.platform: account.status for account in kept}
        locked.networks_read_at = now
        fields = ['networks', 'networks_read_at', 'updated_at']
        if locked.linked_at is None and any(account.status != AccountStatus.DISCONNECTED for account in kept):
            locked.linked_at = now
            fields.append('linked_at')
        locked.save(update_fields=fields)
    return kept


def disconnect(workspace, platform):
    """Unlinks ``platform`` at the provider. Without a profile (or one the provider lost) there is nothing to unlink."""
    from apps.integrations.models import SocialProfile

    profile = ready_profile(workspace.pk)
    if profile is not None:
        try:
            provider_for(profile).disconnect(profile, platform)
        except PermanentError as exc:
            if exc.code != PROFILE_NOT_FOUND:
                raise
        with transaction.atomic():
            locked = SocialProfile.objects.select_for_update().filter(pk=profile.pk).first()
            if locked is not None:
                if locked.networks is not None:
                    locked.networks = {**locked.networks, platform: AccountStatus.DISCONNECTED}
                locked.disconnected_at = {**(locked.disconnected_at or {}), platform: timezone.now().isoformat()}
                locked.save(update_fields=['networks', 'disconnected_at', 'updated_at'])
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


# --- Cleaning up (paid slots) ---

def _shared(profile):
    """Another row with a workspace uses the same provider profile: deleting it would unlink theirs."""
    from apps.integrations.models import SocialProfile

    return SocialProfile.objects.filter(
        provider=profile.provider, provider_ref=profile.provider_ref, workspace__isnull=False,
    ).exclude(pk=profile.pk).exists()


def _delete_at_provider(profile, why):
    """Deletes the profile at the provider and then its row; False if the provider failed (the row stays)."""
    if profile.provider_ref and not _shared(profile):
        try:
            provider_for(profile).delete_profile(profile)
        except SocialProviderError as exc:
            logger.warning("social.%s_kept profile_id=%s code=%s detail=%s", why, profile.pk, exc.code, exc.detail)
            return False
    elif profile.provider_ref:
        logger.error("social.%s_shared profile_id=%s: only the row is deleted", why, profile.pk)
    else:
        # No ref: if a creation's answer was lost, its profile stays at the provider (runbook: by its title).
        logger.warning("social.%s_without_ref profile_id=%s", why, profile.pk)
    profile.delete()
    logger.info("social.%s_deleted profile_id=%s provider_id=%s", why, profile.pk, profile.provider_id)
    return True


def delete_orphan_profiles():
    """
    The profiles the workspace delete marked (`orphaned_at`, and no workspace
    left): each one is deleted at the provider (a paid slot) and then its row.
    One that fails stays for the next run. Returns (deleted, kept).
    """
    from apps.integrations.models import SocialProfile

    deleted = kept = 0
    for profile_id in list(
        SocialProfile.objects.filter(workspace__isnull=True, orphaned_at__isnull=False).values_list('pk', flat=True)
    ):
        with transaction.atomic():
            profile = (
                SocialProfile.objects.select_for_update(skip_locked=True)
                .filter(pk=profile_id, workspace__isnull=True, orphaned_at__isnull=False).first()
            )
            if profile is None:
                continue
            if _delete_at_provider(profile, 'orphan'):
                deleted += 1
            else:
                kept += 1
    return deleted, kept


def delete_unlinked_profiles():
    """
    Profiles of live workspaces that no network was ever linked to, older than
    SOCIAL_UNLINKED_PROFILE_DAYS (an abandoned "Conectar"), and with no link
    asked for in the last CONNECT_IN_PROGRESS (someone may be linking it right
    now): the provider is asked first, and one with any account linked is kept
    (and marked linked).
    The workspace makes a new one the next time it connects. Returns (deleted, kept).
    """
    from apps.integrations.models import SocialProfile

    now = timezone.now()
    since = now - timedelta(days=settings.SOCIAL_UNLINKED_PROFILE_DAYS)
    # Someone asked for a link lately: they may be linking it right now (the provider's list lags).
    connecting = Q(connect_requested_at__gte=now - CONNECT_IN_PROGRESS)
    deleted = kept = 0
    candidates = SocialProfile.objects.filter(
        workspace__isnull=False, linked_at__isnull=True, created_at__lt=since,
    ).exclude(provider_ref='').exclude(connecting).values_list('pk', flat=True)
    for profile_id in list(candidates):
        with transaction.atomic():
            profile = (
                SocialProfile.objects.select_for_update(skip_locked=True)
                .filter(pk=profile_id, linked_at__isnull=True).exclude(provider_ref='').exclude(connecting).first()
            )
            if profile is None:
                continue
            try:
                found = provider_for(profile).accounts(profile)
            except SocialProviderError as exc:
                logger.warning("social.unlinked_kept profile_id=%s code=%s", profile.pk, exc.code)
                kept += 1
                continue
            if any(account.status != AccountStatus.DISCONNECTED for account in found):
                profile.linked_at = timezone.now()
                profile.save(update_fields=['linked_at', 'updated_at'])
                kept += 1
                continue
            if _delete_at_provider(profile, 'unlinked'):
                deleted += 1
            else:
                kept += 1
    return deleted, kept
