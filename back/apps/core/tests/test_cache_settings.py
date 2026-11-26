"""
CACHES de dev y prod: con REDIS_URL, django-redis en ese Redis (una sola cache
para todos los procesos web y worker); sin REDIS_URL, LocMem por proceso, y
prod lo avisa en el log. Antes solo ci.py definía CACHES y dev y prod caían
en la LocMem de Django: la lista de planes y los locks de cache.add
(tasks/social.py) eran uno por proceso.

Los tests contra un Redis real necesitan CAPACITY_TEST_REDIS_URL, como los de
capacidad (CI levanta un Valkey).
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import skipUnless

import pytest
from django.test import SimpleTestCase, override_settings

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TEST_REDIS_URL = os.environ.get("CAPACITY_TEST_REDIS_URL")


def load_caches(settings_module, **environment):
    """CACHES of a settings module, imported in its own process with only this environment."""
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"REDIS_URL", "CACHE_KEY_PREFIX", "DATABASE_URL"}
    }
    # ci skips reading back/.env, so a developer's .env cannot add REDIS_URL.
    env.update({"DJANGO_SETTINGS_MODULE": "backend.settings.ci", "PYTHONPATH": str(BACKEND_ROOT), **environment})
    script = f"import json\nfrom {settings_module} import CACHES\nprint(json.dumps(CACHES))"
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])["default"], result.stderr


@pytest.mark.parametrize("module", ["backend.settings.dev", "backend.settings.prod"])
def test_with_redis_url_the_cache_is_redis(module):
    cache, stderr = load_caches(module, REDIS_URL="redis://redis:6379/0")

    assert cache == {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/0",
        "KEY_PREFIX": "onecreator",
        "TIMEOUT": 300,
        "OPTIONS": {"SOCKET_CONNECT_TIMEOUT": 2, "SOCKET_TIMEOUT": 2},
    }
    assert "cache.not_shared" not in stderr


def test_the_key_prefix_comes_from_the_environment():
    cache, _ = load_caches("backend.settings.prod", REDIS_URL="redis://redis:6379/0", CACHE_KEY_PREFIX="staging")

    assert cache["KEY_PREFIX"] == "staging"


def test_without_redis_url_dev_falls_back_to_locmem_quietly():
    cache, stderr = load_caches("backend.settings.dev")

    assert cache["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert "cache.not_shared" not in stderr


def test_without_redis_url_prod_falls_back_to_locmem_and_warns():
    cache, stderr = load_caches("backend.settings.prod")

    assert cache["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert "cache.not_shared REDIS_URL is not set" in stderr


@skipUnless(TEST_REDIS_URL, "Set CAPACITY_TEST_REDIS_URL to run the shared cache tests against Valkey")
class SharedCacheTests(SimpleTestCase):
    """The settings' own CACHES on a real Redis, with a key prefix of its own."""

    def setUp(self):
        prefix = f"test-cache-{uuid.uuid4().hex}"
        self.caches = {"default": load_caches("backend.settings.prod", REDIS_URL=TEST_REDIS_URL, CACHE_KEY_PREFIX=prefix)[0]}

    def add_from_other_processes(self, key, count):
        """``cache.add`` of the same key from ``count`` processes started together: what each one got."""
        script = (
            "import json, sys\n"
            "from django.conf import settings\n"
            "settings.configure(CACHES=json.loads(sys.argv[1]))\n"
            "from django.core.cache import cache\n"
            "print(cache.add(sys.argv[2], 'locked', 60))\n"
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

    def test_cache_add_takes_a_key_once_across_processes(self):
        results = self.add_from_other_processes(f"lock-{uuid.uuid4().hex}", 6)

        self.assertEqual(sorted(results), ["False"] * 5 + ["True"])

    def test_a_publish_lock_taken_by_another_process_stops_the_second_delivery(self):
        from django.core.cache import cache

        from apps.videos.tasks.social import upload_to_social_network

        post_id = uuid.uuid4()
        self.assertEqual(self.add_from_other_processes(f"lock_post_publish_{post_id}", 1), ["True"])

        with override_settings(CACHES=self.caches):
            try:
                self.assertEqual(upload_to_social_network(post_id), "Locked")
            finally:
                cache.delete(f"lock_post_publish_{post_id}")
