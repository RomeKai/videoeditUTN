from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.videos.services.ai.errors import RetryableAIError, is_retryable_error
from apps.videos.services.ai.project_lock import ProjectLockedError, project_lock
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
