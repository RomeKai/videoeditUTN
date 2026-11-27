"""Celery tasks of the social publishing providers."""
import logging

from backend.celery import app as celery_app

logger = logging.getLogger(__name__)


# The project's app, not shared_task: the workspace delete queues it from a request thread, where
# Celery's "current app" would be a default one with no broker configured.
@celery_app.task(name='apps.integrations.tasks.delete_orphan_social_profiles')
def delete_orphan_social_profiles():
    """
    Deletes at the provider the profiles of deleted workspaces (a paid slot
    each), then their rows (apps.integrations.social.accounts.delete_orphan_profiles).
    Queued by the workspace delete after it commits; clean_social_profiles runs it
    daily for what a failed run left. Never blocks a delete: a profile that
    fails stays for the next run.
    """
    from apps.integrations.social.accounts import delete_orphan_profiles

    deleted, kept = delete_orphan_profiles()
    logger.info("social.orphans deleted=%d kept=%d", deleted, kept)
    return {'deleted': deleted, 'kept': kept}


@celery_app.task(name='apps.integrations.tasks.clean_social_profiles')
def clean_social_profiles():
    """
    Daily from Celery Beat ('clean-social-profiles'): the orphans a failed
    delete left, and the profiles no network was ever linked to after
    SOCIAL_UNLINKED_PROFILE_DAYS (the provider is asked first). Each deleted
    profile frees a paid slot.
    """
    from apps.integrations.social.accounts import delete_orphan_profiles, delete_unlinked_profiles

    orphans, orphans_kept = delete_orphan_profiles()
    unlinked, unlinked_kept = delete_unlinked_profiles()
    logger.info(
        "social.cleaned orphans=%d orphans_kept=%d unlinked=%d unlinked_kept=%d",
        orphans, orphans_kept, unlinked, unlinked_kept,
    )
    return {'orphans': orphans, 'orphans_kept': orphans_kept, 'unlinked': unlinked, 'unlinked_kept': unlinked_kept}
