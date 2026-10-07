"""
Distributed per-project lock for the ingestion pipeline (AICORE-7).

Uses redis-py directly: the project has no shared Django cache (``cache.add``
would be per-process), so the broker's Redis is the only store workers share.
The lock carries an ownership token and a TTL, and only its owner can release
it. If Redis is unreachable we fail fast with a retryable error: processing a
project without the lock is never an option.
"""

import logging
from contextlib import contextmanager

import redis
from django.conf import settings

from apps.videos.services.ai.errors import RetryableAIError

logger = logging.getLogger(__name__)

# Worst case for one stage (long transcription) must stay well under the TTL.
DEFAULT_LOCK_TTL_SECONDS = 3600


class ProjectLockedError(Exception):
    """Another worker currently holds the lock for this project."""


def get_redis_client():
    return redis.Redis.from_url(settings.REDIS_URL)


@contextmanager
def project_lock(project_id, client=None, ttl=None):
    client = client if client is not None else get_redis_client()
    ttl = ttl or getattr(settings, "PIPELINE_LOCK_TTL", DEFAULT_LOCK_TTL_SECONDS)
    lock = client.lock(f"lock:pipeline:project:{project_id}", timeout=ttl)

    try:
        acquired = lock.acquire(blocking=False)
    except redis.exceptions.RedisError as exc:
        raise RetryableAIError(
            f"Redis unavailable, cannot lock project {project_id}: {exc}",
            provider="redis",
            raw_error=exc,
        ) from exc

    if not acquired:
        raise ProjectLockedError(f"Project {project_id} is being processed by another worker")

    try:
        yield
    finally:
        try:
            lock.release()
        except redis.exceptions.LockError:
            # TTL expired mid-run (and the lock may belong to someone else now).
            logger.warning(
                "pipeline.lock_not_owned project_id=%s", project_id,
                extra={"project_id": str(project_id)},
            )
        except redis.exceptions.RedisError:
            # The TTL will free the lock; the work itself already finished.
            logger.warning(
                "pipeline.lock_release_failed project_id=%s", project_id,
                extra={"project_id": str(project_id)},
            )
