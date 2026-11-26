"""
CACHES de dev y prod (settings/base.py, apps/core/cache.py):

- Con CACHE_REDIS_URL, django-redis en esa base, que no es la del broker:
  una cache para todos los procesos web y worker. Sin CACHE_REDIS_URL,
  LocMem por proceso, y prod lo avisa en el log. Antes solo ci.py definía
  CACHES y dev y prod caían en la LocMem de Django: la lista de planes y los
  locks de cache.add (tasks/social.py) eran uno por proceso.
- Dos alias: ``default`` falla abierto (sin Redis es un miss y loguea la
  clase del error) y ``locks`` lanza (sin Redis, un lock no se da por
  tomado).
- ``clear_local_caches`` (apps/core/testing.py) solo limpia una LocMem: en
  django-redis, clear() es un FLUSHDB de toda la base.

Los tests contra un Redis real necesitan CAPACITY_TEST_REDIS_URL, como los de
capacidad (CI levanta un Valkey).
"""

import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.testing import clear_local_caches

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TEST_REDIS_URL = os.environ.get("CAPACITY_TEST_REDIS_URL")
BROKER_URL = "redis://redis:6379/0"
CACHE_URL = "redis://redis:6379/1"
# Nothing listens there: every test that uses it patches the socket connect.
DOWN_URL = "redis://127.0.0.1:6399/1"


def redis_down():
    return patch("redis.connection.Connection._connect", side_effect=ConnectionRefusedError(111, "refused"))


def load_caches(settings_module, **environment):
    """CACHES of a settings module, imported in its own process with only this environment."""
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"REDIS_URL", "CACHE_REDIS_URL", "CACHE_KEY_PREFIX", "DATABASE_URL"}
    }
    # ci skips reading back/.env, so a developer's .env cannot add a URL.
    env.update({"DJANGO_SETTINGS_MODULE": "backend.settings.ci", "PYTHONPATH": str(BACKEND_ROOT), **environment})
    script = f"import json\nfrom {settings_module} import CACHES\nprint(json.dumps(CACHES))"
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1]), result.stderr


@pytest.mark.parametrize("module", ["backend.settings.dev", "backend.settings.prod"])
def test_with_a_cache_url_both_caches_are_redis_in_that_database(module):
    caches_config, stderr = load_caches(module, REDIS_URL=BROKER_URL, CACHE_REDIS_URL=CACHE_URL)

    options = {"SOCKET_CONNECT_TIMEOUT": 2, "SOCKET_TIMEOUT": 2}
    assert caches_config == {
        "default": {
            "BACKEND": "apps.core.cache.FailOpenRedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "onecreator",
            "TIMEOUT": 300,
            "OPTIONS": {**options, "IGNORE_EXCEPTIONS": True},
        },
        "locks": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "onecreator:locks",
            "TIMEOUT": 300,
            "OPTIONS": options,
        },
    }
    assert "cache.not_shared" not in stderr
    assert "cache.shares_broker_db" not in stderr


def test_the_broker_url_alone_does_not_make_a_redis_cache():
    """The broker's database is never the cache: without CACHE_REDIS_URL it stays LocMem."""
    caches_config, _ = load_caches("backend.settings.dev", REDIS_URL=BROKER_URL)

    assert {alias: c["BACKEND"] for alias, c in caches_config.items()} == {
        "default": "django.core.cache.backends.locmem.LocMemCache",
        "locks": "django.core.cache.backends.locmem.LocMemCache",
    }


def test_the_key_prefix_comes_from_the_environment():
    caches_config, _ = load_caches("backend.settings.prod", CACHE_REDIS_URL=CACHE_URL, CACHE_KEY_PREFIX="staging")

    assert caches_config["default"]["KEY_PREFIX"] == "staging"
    assert caches_config["locks"]["KEY_PREFIX"] == "staging:locks"


def test_a_cache_in_the_broker_database_is_warned_about():
    _, stderr = load_caches("backend.settings.dev", REDIS_URL=BROKER_URL, CACHE_REDIS_URL="redis://redis:6379")

    assert "cache.shares_broker_db" in stderr


def test_without_a_cache_url_dev_falls_back_to_locmem_quietly():
    caches_config, stderr = load_caches("backend.settings.dev")

    assert caches_config["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert caches_config["locks"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert caches_config["default"]["LOCATION"] != caches_config["locks"]["LOCATION"]
    assert "cache.not_shared" not in stderr


def test_without_a_cache_url_prod_falls_back_to_locmem_and_warns():
    caches_config, stderr = load_caches("backend.settings.prod", REDIS_URL=BROKER_URL)

    assert caches_config["default"]["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert "cache.not_shared CACHE_REDIS_URL is not set" in stderr


def redis_caches(url, prefix="onecreator"):
    """The settings' own CACHES, pointed at ``url``."""
    return load_caches("backend.settings.prod", CACHE_REDIS_URL=url, CACHE_KEY_PREFIX=prefix)[0]


class ClearLocalCachesTests(SimpleTestCase):
    def test_a_locmem_cache_is_cleared(self):
        caches["default"].set("k", "v")

        clear_local_caches()

        self.assertIsNone(caches["default"].get("k"))

    def test_a_redis_cache_is_never_flushed(self):
        with override_settings(CACHES=redis_caches(DOWN_URL)), \
                patch("django_redis.client.DefaultClient.clear") as flush:
            with self.assertRaisesMessage(AssertionError, "Refusing to clear the 'default' cache"):
                clear_local_caches()
            with self.assertRaisesMessage(AssertionError, "Refusing to clear the 'locks' cache"):
                clear_local_caches("locks")

        flush.assert_not_called()


class RedisDownTests(TestCase):
    """Redis does not answer (the socket connect is refused)."""

    def setUp(self):
        self.caches = redis_caches(DOWN_URL)

    def test_the_default_cache_fails_open_and_logs_only_the_class(self):
        with override_settings(CACHES=self.caches), redis_down(), \
                self.assertLogs("apps.core.cache", level=logging.WARNING) as logs:
            cache = caches["default"]
            self.assertIsNone(cache.get("k"))
            cache.set("k", "v")
            self.assertFalse(cache.delete("k"))

        self.assertEqual(logs.output[0], "WARNING:apps.core.cache:cache.unavailable error=ConnectionError")
        self.assertNotIn("6399", "".join(logs.output))

    def test_the_public_plans_still_answer(self):
        with override_settings(CACHES=self.caches), redis_down(), self.assertLogs("apps.core.cache", level=logging.WARNING):
            response = self.client.get("/api/v1/plans/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json())

    def test_saving_a_plan_still_works(self):
        """Saving a plan refreshes the cached list (payments/signals.py): a skipped delete, not an error."""
        from apps.payments.models import SubscriptionPlan

        with override_settings(CACHES=self.caches), redis_down(), \
                self.assertLogs("apps.core.cache", level=logging.WARNING):
            # The list is dropped once the save commits.
            with self.captureOnCommitCallbacks(execute=True):
                plan = SubscriptionPlan.objects.create(name="Sin Redis", max_members=3)
            with self.captureOnCommitCallbacks(execute=True):
                plan.delete()

    def test_the_locks_cache_raises(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        with override_settings(CACHES=self.caches), redis_down():
            with self.assertRaises(RedisConnectionError):
                caches["locks"].add("lock", "locked", 60)

    def test_a_post_is_not_published_without_its_lock(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        from apps.videos.tasks.social import upload_to_social_network

        with override_settings(CACHES=self.caches), redis_down(), \
                patch("apps.integrations.ayrshare_api.AyrshareClient.send_post") as send:
            with self.assertRaises(RedisConnectionError):
                upload_to_social_network(uuid.uuid4())

        send.assert_not_called()


@skipUnless(TEST_REDIS_URL, "Set CAPACITY_TEST_REDIS_URL to run the shared cache tests against Valkey")
class SharedCacheTests(SimpleTestCase):
    """The settings' own CACHES on a real Redis, with a key prefix of its own."""

    def setUp(self):
        self.caches = redis_caches(TEST_REDIS_URL, prefix=f"test-cache-{uuid.uuid4().hex}")

    def add_from_other_processes(self, key, count):
        """``caches['locks'].add`` of the same key from ``count`` processes started together: what each one got."""
        script = (
            "import json, sys\n"
            "from django.conf import settings\n"
            "settings.configure(CACHES=json.loads(sys.argv[1]))\n"
            "from django.core.cache import caches\n"
            "print(caches['locks'].add(sys.argv[2], 'locked', 60))\n"
        )
        env = {**os.environ, "PYTHONPATH": str(BACKEND_ROOT)}
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", script, json.dumps(self.caches), key],
                cwd=BACKEND_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(count)
        ]
        results = []
        for worker in workers:
            out, err = worker.communicate(timeout=60)
            self.assertEqual(worker.returncode, 0, err)
            results.append(out.strip())
        return results

    def test_a_lock_is_taken_once_across_processes(self):
        results = self.add_from_other_processes(f"lock-{uuid.uuid4().hex}", 6)

        self.assertEqual(sorted(results), ["False"] * 5 + ["True"])

    def test_a_publish_lock_taken_by_another_process_stops_the_second_delivery(self):
        from apps.videos.tasks.social import upload_to_social_network

        post_id = uuid.uuid4()
        self.assertEqual(self.add_from_other_processes(f"lock_post_publish_{post_id}", 1), ["True"])

        with override_settings(CACHES=self.caches):
            try:
                self.assertEqual(upload_to_social_network(post_id), "Locked")
            finally:
                caches["locks"].delete(f"lock_post_publish_{post_id}")
