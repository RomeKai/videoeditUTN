"""In-memory stand-ins for Redis so tests never need a live broker."""

import threading

import redis
from django.test import override_settings


class CapturedTaskFailures:
    """Test mixin: ``task.apply()`` captures failures in the EagerResult.

    ``backend.settings.ci`` sets ``CELERY_TASK_EAGER_PROPAGATES=True`` while the
    default settings do not. Tests that assert on ``result.failed()`` must not
    depend on whichever settings module runs them, so the flag is pinned to
    False for the class and restored afterwards.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Celery reads this through the Django settings namespace (CELERY_*),
        # so override_settings is the layer that actually takes effect.
        override = override_settings(CELERY_TASK_EAGER_PROPAGATES=False)
        override.enable()
        cls.addClassCleanup(override.disable)


class FakeLock:
    def __init__(self, store, name, timeout):
        self._store = store
        self.name = name
        self.timeout = timeout
        self._token = None
        self.extend_calls = []

    def acquire(self, blocking=True, **kwargs):
        with self._store.guard:
            if self.name in self._store.held:
                return False
            self._token = object()
            self._store.held[self.name] = self._token
            return True

    def extend(self, additional_time, replace_ttl=False):
        with self._store.guard:
            if self._store.held.get(self.name) is not self._token:
                raise redis.exceptions.LockNotOwnedError("not owner")
            self.extend_calls.append((additional_time, replace_ttl))
            return True

    def release(self):
        with self._store.guard:
            if self._store.held.get(self.name) is not self._token:
                raise redis.exceptions.LockNotOwnedError("not owner")
            del self._store.held[self.name]


class FakeRedis:
    """Minimal ``Redis.lock`` implementation shared across lock instances."""

    def __init__(self):
        self.guard = threading.Lock()
        self.held = {}
        self.lock_calls = []
        self.locks = []

    def lock(self, name, timeout=None, **kwargs):
        self.lock_calls.append((name, timeout))
        lock = FakeLock(self, name, timeout)
        self.locks.append(lock)
        return lock


class DownRedis:
    """Simulates an unreachable Redis server."""

    def lock(self, name, timeout=None, **kwargs):
        return self

    def acquire(self, *args, **kwargs):
        raise redis.exceptions.ConnectionError("redis is down")
