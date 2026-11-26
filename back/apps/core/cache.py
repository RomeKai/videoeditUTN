"""
The two Django caches (settings: CACHES, documents/ia/07 "Cache compartida").

- ``default`` fails open: a Redis that does not answer is a cache miss, so a
  read falls back to the database and a write is skipped. For data that can
  be rebuilt and for limits that may let a request through: the public plans
  list, request throttles, a probe cache. Never for a lock: ``add`` returns
  None there when Redis is down.
  After an error it skips Redis for CACHE_CIRCUIT_COOLDOWN_SECONDS (a circuit
  breaker per process), so a Redis that hangs costs one socket timeout per
  cooldown instead of one per call. Code that must not fail open keeps its
  own fallback for those misses (apps/users/throttles.py, LoginFailureGuard).
- ``locks`` raises: a lock that cannot be taken stops the caller instead of
  letting two of them in. ``caches['locks'].add(key, ...)`` is a single
  ``SET NX PX`` in Redis. A lock that needs an owner token or renewal goes to
  redis-py directly, like ``services/ai/project_lock.py``.
"""

import logging
import sys
import threading
import time

from django.conf import settings
from django_redis.cache import RedisCache
from django_redis.exceptions import ConnectionInterrupted

logger = logging.getLogger(__name__)


class CircuitOpen(Exception):
    """Redis was skipped: it failed less than a cooldown ago."""


class CircuitBreaker:
    """
    Closed: every call goes to Redis. The first error opens it and logs
    ``cache.unavailable`` once. Open: calls are skipped. When the cooldown
    ends, one call is let through as a probe while the others keep being
    skipped: its success closes the breaker (``cache.recovered``), its
    failure starts another cooldown without logging again.
    """

    def __init__(self, cooldown):
        self.cooldown = cooldown
        self._lock = threading.Lock()
        self._retry_at = None
        self._down_since = None

    def allow(self):
        """True (go to Redis), False (skip it) or 'probe' (go, and report the result)."""
        with self._lock:
            if self._retry_at is None:
                return True
            now = time.monotonic()
            if now < self._retry_at:
                return False
            # Only this caller probes: the others wait for another cooldown.
            self._retry_at = now + self.cooldown
            return 'probe'

    def failed(self, error):
        with self._lock:
            first = self._down_since is None
            now = time.monotonic()
            if first:
                self._down_since = now
            self._retry_at = now + self.cooldown
        if first:
            logger.warning("cache.unavailable error=%s", type(error).__name__)

    def succeeded(self):
        with self._lock:
            down_since, self._down_since, self._retry_at = self._down_since, None, None
        if down_since is not None:
            logger.info("cache.recovered after_seconds=%d", time.monotonic() - down_since)


# One breaker per Redis and per process: Django builds a cache object per thread.
_breakers = {}
_breakers_lock = threading.Lock()


def _breaker_for(server):
    with _breakers_lock:
        if server not in _breakers:
            _breakers[server] = CircuitBreaker(getattr(settings, "CACHE_CIRCUIT_COOLDOWN_SECONDS", 5))
        return _breakers[server]


def reset_circuit_breakers():
    """Forgets every outage. For tests: each one starts with Redis considered up."""
    with _breakers_lock:
        _breakers.clear()


class _Probe:
    """The client for the one call that tests Redis after a cooldown: a call that returns closes the breaker."""

    def __init__(self, client, breaker):
        self._client = client
        self._breaker = breaker

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def call(*args, **kwargs):
            result = method(*args, **kwargs)
            self._breaker.succeeded()
            return result

        return call


class _IgnoredErrorLog:
    """
    django-redis logs every ignored error with its whole traceback. This
    tells the breaker instead, which logs the class of the first error of an
    outage (``cache.unavailable``) and nothing for the calls it skips.
    """

    def __init__(self, breaker):
        self.breaker = breaker

    def exception(self, *args, **kwargs):
        error = sys.exc_info()[1]
        # ConnectionInterrupted wraps the redis-py error that caused it.
        cause = getattr(error, "__cause__", None) or error
        if not isinstance(cause, CircuitOpen):
            self.breaker.failed(cause)


class FailOpenRedisCache(RedisCache):
    """
    django-redis with IGNORE_EXCEPTIONS on and a circuit breaker: an error, or
    a call skipped while the breaker is open, returns what a miss returns
    (None, or the default of ``get``).
    """

    def __init__(self, server, params):
        super().__init__(server, params)
        self._ignore_exceptions = True
        self._log_ignored_exceptions = True
        self._breaker = _breaker_for(server)
        self.logger = _IgnoredErrorLog(self._breaker)

    @property
    def client(self):
        allowed = self._breaker.allow()
        if not allowed:
            # Handled like any other ignored error by every method of RedisCache.
            raise ConnectionInterrupted(connection=None) from CircuitOpen()
        client = super().client
        return _Probe(client, self._breaker) if allowed == 'probe' else client

    def close(self, **kwargs):
        # Closing the connections asks Redis nothing: it must not count as a probe.
        if self._client:
            self._client.close()
