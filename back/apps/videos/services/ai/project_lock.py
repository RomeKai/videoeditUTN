"""
Distributed per-project lock for the ingestion pipeline (AICORE-7).

Uses redis-py directly: the project has no shared Django cache (``cache.add``
would be per-process), so the broker's Redis is the only store workers share.
The lock carries an ownership token and a TTL, and only its owner can release
it. If Redis is unreachable we fail fast with a retryable error: processing a
project without the lock is never an option.
"""

import logging
import threading
from contextlib import contextmanager

import redis
from django.conf import settings

from apps.videos.services.ai.errors import RetryableAIError

logger = logging.getLogger(__name__)

# Short on purpose: the lock is renewed while its owner is alive (heartbeat), so
# the TTL only bounds how long a *dead* worker's lock keeps its project blocked
# before the redelivered task can take over. It must stay well below Celery's
# broker visibility_timeout (see CELERY_BROKER_TRANSPORT_OPTIONS in settings).
DEFAULT_LOCK_TTL_SECONDS = 300
# The heartbeat renews this many times per TTL window.
_HEARTBEATS_PER_TTL = 3


class ProjectLockedError(Exception):
    """Another worker currently holds the lock for this project."""


class ProjectLockLostError(Exception):
    """The lock expired or was taken over while its owner was still working."""


def get_redis_client():
    return redis.Redis.from_url(settings.REDIS_URL)


class ProjectLockHandle:
    """Owner-side view of a held lock: renewal and loss detection."""

    def __init__(self, lock, ttl, project_id):
        self._lock = lock
        self._ttl = ttl
        self._project_id = project_id
        self.lost = threading.Event()

    def renew(self):
        """Resets the TTL. Raises ``ProjectLockLostError`` if the lock is no longer ours."""
        if self.lost.is_set():
            raise ProjectLockLostError(f"Lock for project {self._project_id} was lost")
        try:
            self._lock.extend(self._ttl, replace_ttl=True)
        except redis.exceptions.LockError as exc:
            self.lost.set()
            raise ProjectLockLostError(
                f"Lock for project {self._project_id} was lost: {exc}"
            ) from exc
        except redis.exceptions.RedisError:
            # Transient: the remaining TTL still covers a few missed renewals.
            logger.warning(
                "pipeline.lock_renew_failed project_id=%s", self._project_id,
                extra={"project_id": str(self._project_id)},
            )


def _heartbeat(handle, stop, interval):
    while not stop.wait(interval):
        try:
            handle.renew()
        except ProjectLockLostError:
            logger.warning(
                "pipeline.lock_lost project_id=%s", handle._project_id,
                extra={"project_id": str(handle._project_id)},
            )
            return


@contextmanager
def project_lock(project_id, client=None, ttl=None, heartbeat=True):
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

    handle = ProjectLockHandle(lock, ttl, project_id)
    stop = threading.Event()
    thread = None
    if heartbeat:
        thread = threading.Thread(
            target=_heartbeat,
            args=(handle, stop, max(ttl / _HEARTBEATS_PER_TTL, 0.05)),
            name=f"project-lock-heartbeat-{project_id}",
            daemon=True,
        )
        thread.start()

    try:
        yield handle
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=5)
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
