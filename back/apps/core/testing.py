"""Helpers for tests (no test runs from here)."""

from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache


def clear_local_caches(*aliases):
    """
    ``cache.clear()`` for tests, only on a LocMem cache. On django-redis it is
    a FLUSHDB of the whole Redis database, whatever the key prefix. pytest
    takes DJANGO_SETTINGS_MODULE from the environment over pytest.ini, and the
    docker-compose containers set it to dev: a test run there would wipe the
    shared cache (and the broker, if they share the database).
    """
    for alias in aliases or ("default",):
        cache = caches[alias]
        if not isinstance(cache, LocMemCache):
            raise AssertionError(
                f"Refusing to clear the {alias!r} cache: it is {type(cache).__name__}, not LocMemCache. "
                "Run the tests with DJANGO_SETTINGS_MODULE=backend.settings.ci."
            )
        cache.clear()
