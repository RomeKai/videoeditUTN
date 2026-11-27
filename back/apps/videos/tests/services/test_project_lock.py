import threading
import time
from unittest.mock import patch

import redis.lock

from django.test import SimpleTestCase, override_settings

from apps.videos.services.ai.errors import RetryableAIError, is_retryable_error
from apps.videos.services.ai.project_lock import (
    DEFAULT_LOCK_TTL_SECONDS,
    ProjectLockedError,
    ProjectLockLostError,
    project_lock,
    project_lock_held,
)
from apps.videos.tests.fakes import DownRedis, FakeRedis


class ProjectLockTests(SimpleTestCase):
    def test_second_holder_is_rejected_while_first_holds_the_lock(self):
        client = FakeRedis()

        with project_lock("p1", client=client):
            with self.assertRaises(ProjectLockedError):
                with project_lock("p1", client=client):
                    self.fail("second holder must not enter the critical section")

    def test_lock_is_released_on_exit_and_on_error(self):
        client = FakeRedis()

        with self.assertRaises(ValueError):
            with project_lock("p1", client=client):
                raise ValueError("boom")

        with project_lock("p1", client=client):
            pass

    def test_locks_are_per_project(self):
        client = FakeRedis()

        with project_lock("p1", client=client):
            with project_lock("p2", client=client):
                pass

    @override_settings(PIPELINE_LOCK_TTL=1234)
    def test_lock_uses_a_ttl_from_settings(self):
        client = FakeRedis()

        with project_lock("p1", client=client):
            pass

        self.assertEqual(client.lock_calls[0][1], 1234)

    def test_unreachable_redis_raises_retryable_error_and_never_enters(self):
        entered = []

        with self.assertRaises(RetryableAIError) as ctx:
            with project_lock("p1", client=DownRedis()):
                entered.append(True)

        self.assertEqual(entered, [])
        self.assertTrue(is_retryable_error(ctx.exception))

    def test_default_client_is_built_from_redis_url(self):
        with patch("apps.videos.services.ai.project_lock.redis.Redis.from_url") as from_url:
            from_url.return_value = FakeRedis()
            with override_settings(REDIS_URL="redis://example:6379/5"):
                with project_lock("p1"):
                    pass

        from_url.assert_called_once()
        self.assertEqual(from_url.call_args.args[0], "redis://example:6379/5")


class _RedisPyLock(redis.lock.Lock):
    """The lock of redis-py, with its scripts registered on this class: the real one caches them for every client."""

    lua_release = None
    lua_extend = None
    lua_reacquire = None


class _Script:
    """What ``register_script`` returns: called with (keys, args, client)."""

    def __init__(self, on_call=None):
        self._on_call = on_call

    def __call__(self, keys=(), args=(), client=None):
        if self._on_call:
            self._on_call()
        return 1


class _ScriptedRedis:
    """Just enough of a client for the lock of redis-py: it takes every key and counts the renewals."""

    def __init__(self):
        self.extended = []

    def lock(self, name, timeout=None, **kwargs):
        return _RedisPyLock(self, name, timeout=timeout, **kwargs)

    def register_script(self, script):
        if script == redis.lock.Lock.LUA_EXTEND_SCRIPT:
            return _Script(lambda: self.extended.append(threading.current_thread().name))
        return _Script()

    def set(self, name, value, nx=False, px=None):
        return True


class ProjectLockWithRedisPyTests(SimpleTestCase):
    """The lock of redis-py itself: the fake lock has no notion of which thread owns the token."""

    def setUp(self):
        # redis-py registers its scripts once per class, bound to the first client: a second run
        # in the same process would count the renewals of a client that is gone.
        _RedisPyLock.lua_release = _RedisPyLock.lua_extend = _RedisPyLock.lua_reacquire = None

    def test_heartbeat_thread_can_renew_the_lock_the_task_thread_acquired(self):
        client = _ScriptedRedis()
        crashes = []
        hook = threading.excepthook
        threading.excepthook = lambda args: crashes.append(args.exc_type.__name__)
        try:
            with project_lock("p1", client=client, ttl=0.3):
                time.sleep(0.5)
        finally:
            threading.excepthook = hook

        # By default redis-py keeps the token per thread: the heartbeat thread then finds none,
        # dies on its first renewal and the lock expires under a stage longer than the TTL.
        self.assertEqual(crashes, [])
        self.assertGreaterEqual(len(client.extended), 1)
        self.assertTrue(all(name.startswith("project-lock-heartbeat-") for name in client.extended))


class ProjectLockHeldTests(SimpleTestCase):
    """Read-only check used by the dry run of recover_stuck_renders."""

    def test_tells_whether_the_project_is_held_without_taking_the_lock(self):
        client = FakeRedis()

        self.assertFalse(project_lock_held("p1", client=client))
        with project_lock("p1", client=client, heartbeat=False):
            self.assertTrue(project_lock_held("p1", client=client))
            self.assertFalse(project_lock_held("p2", client=client))
        self.assertFalse(project_lock_held("p1", client=client))
        self.assertEqual(client.held, {})

    def test_unreachable_redis_raises_a_retryable_error(self):
        with self.assertRaises(RetryableAIError):
            project_lock_held("p1", client=DownRedis())


class ProjectLockRenewalTests(SimpleTestCase):
    def test_default_ttl_is_short_so_a_dead_workers_lock_expires_quickly(self):
        self.assertLessEqual(DEFAULT_LOCK_TTL_SECONDS, 600)

    def test_renew_extends_the_ttl_of_the_held_lock(self):
        client = FakeRedis()

        with project_lock("p1", client=client, ttl=100, heartbeat=False) as handle:
            handle.renew()

        self.assertEqual(client.locks[0].extend_calls, [(100, True)])

    def test_heartbeat_keeps_extending_the_lock_during_a_long_stage(self):
        client = FakeRedis()

        with project_lock("p1", client=client, ttl=0.3):
            time.sleep(0.5)

        self.assertGreaterEqual(len(client.locks[0].extend_calls), 1)

    def test_heartbeat_stops_after_the_lock_is_released(self):
        client = FakeRedis()

        with project_lock("p1", client=client, ttl=0.3):
            pass
        calls = len(client.locks[0].extend_calls)
        time.sleep(0.3)

        self.assertEqual(len(client.locks[0].extend_calls), calls)

    def test_renew_raises_when_the_lock_was_lost(self):
        client = FakeRedis()

        with self.assertRaises(ProjectLockLostError):
            with project_lock("p1", client=client, ttl=100, heartbeat=False) as handle:
                client.held.clear()  # TTL expired and the key is gone
                handle.renew()
