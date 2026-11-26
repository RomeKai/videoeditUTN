"""
The two Django caches (settings: CACHES, documents/ia/07 "Cache compartida").

- ``default`` fails open: a Redis that does not answer is a cache miss, so a
  read falls back to the database and a write is skipped. For data that can
  be rebuilt and for limits that may let a request through: the public plans
  list, request throttles, a probe cache. Never for a lock: ``add`` returns
  None there when Redis is down.
- ``locks`` raises: a lock that cannot be taken stops the caller instead of
  letting two of them in. ``caches['locks'].add(key, ...)`` is a single
  ``SET NX PX`` in Redis. A lock that needs an owner token or renewal goes to
  redis-py directly, like ``services/ai/project_lock.py``.
"""

import logging
import sys

from django_redis.cache import RedisCache

logger = logging.getLogger(__name__)


class _IgnoredErrorLog:
    """django-redis logs an ignored error with its whole traceback: this logs its class only."""

    def exception(self, *args, **kwargs):
        error = sys.exc_info()[1]
        # ConnectionInterrupted wraps the redis-py error that caused it.
        cause = getattr(error, "__cause__", None) or error
        logger.warning("cache.unavailable error=%s", type(cause).__name__)


class FailOpenRedisCache(RedisCache):
    """django-redis with IGNORE_EXCEPTIONS on, logging each ignored error as ``cache.unavailable``."""

    def __init__(self, server, params):
        super().__init__(server, params)
        self._ignore_exceptions = True
        self._log_ignored_exceptions = True
        self.logger = _IgnoredErrorLog()
