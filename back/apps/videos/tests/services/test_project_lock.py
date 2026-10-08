import time
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.videos.services.ai.errors import RetryableAIError, is_retryable_error
from apps.videos.services.ai.project_lock import (
    DEFAULT_LOCK_TTL_SECONDS,
    ProjectLockedError,
    ProjectLockLostError,
    project_lock,
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
