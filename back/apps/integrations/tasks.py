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
    Queued by the workspace delete after it commits, and daily from Celery Beat
    ('delete-orphan-social-profiles') for what a failed run left. Never blocks a
    delete: a profile that fails stays for the next run.
    """
    from apps.integrations.social.accounts import delete_orphan_profiles

    deleted, kept = delete_orphan_profiles()
    logger.info("social.orphans deleted=%d kept=%d", deleted, kept)
    return {'deleted': deleted, 'kept': kept}
