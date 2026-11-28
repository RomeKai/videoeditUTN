"""In-memory stand-ins for Redis so tests never need a live broker."""

import threading
import uuid

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

    def acquire(self, blocking=True, token=None, **kwargs):
        with self._store.guard:
            if self.name in self._store.held:
                return False
            # The value Redis keeps under the lock's name (redis-py makes up one when none is given).
            self._token = token if token is not None else uuid.uuid1().hex
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

    def locked(self):
        with self._store.guard:
            return self.name in self._store.held


class FakeRedis:
    """Minimal ``Redis.lock`` implementation shared across lock instances."""

    def __init__(self):
        self.guard = threading.Lock()
        self.held = {}
        self.lock_calls = []
        self.locks = []
        self.values = {}

    # Plain keys (expiry is not simulated). A held lock's name reads its token, as in Redis.
    def get(self, name):
        if name in self.held:
            token = self.held[name]
            return token.encode() if isinstance(token, str) else b"held"
        return self.values.get(name)

    def set(self, name, value, ex=None):
        self.values[name] = value.encode() if isinstance(value, str) else value
        return True

    def delete(self, *names):
        return sum(1 for name in names if self.values.pop(name, None) is not None)

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

    def locked(self):
        raise redis.exceptions.ConnectionError("redis is down")
