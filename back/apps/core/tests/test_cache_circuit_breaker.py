"""
The circuit breaker of the fail-open cache (apps/core/cache.py): after an
error Redis is skipped for a cooldown, then one call tries it again. The
``locks`` cache has none: a lock that cannot be taken always raises.
"""
import logging
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.test import override_settings
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from apps.core import cache as core_cache

DOWN_URL = "redis://127.0.0.1:6399/1"
OPTIONS = {"SOCKET_CONNECT_TIMEOUT": 2, "SOCKET_TIMEOUT": 2}
CACHES = {
    "default": {
        "BACKEND": "apps.core.cache.FailOpenRedisCache",
        "LOCATION": DOWN_URL,
        "OPTIONS": {**OPTIONS, "IGNORE_EXCEPTIONS": True},
    },
    "locks": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": DOWN_URL, "OPTIONS": dict(OPTIONS)},
}


class FakeRedis:
    """Stands in for the socket: refuses every connection and counts them."""

    def __init__(self):
        self.attempts = 0

    def connect(self, *args, **kwargs):
        self.attempts += 1
        raise ConnectionRefusedError(111, "refused")


@pytest.fixture
def redis():
    fake = FakeRedis()
    with override_settings(CACHES=CACHES, CACHE_CIRCUIT_COOLDOWN_SECONDS=5), \
            patch("redis.connection.Connection._connect", side_effect=fake.connect):
        yield fake


@pytest.fixture
def clock():
    """
    The breaker's clock, moved by hand: no test here depends on how long it
    takes to run. Only the breaker's clock: time.monotonic stays real for
    everything else in the process.
    """
    now = [1000.0]
    with patch.object(core_cache, "_now", side_effect=lambda: now[0]):
        yield now


def _messages(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "apps.core.cache"]


def test_after_an_error_redis_is_skipped_during_the_cooldown(redis, clock, caplog):
    cache = caches["default"]
    with caplog.at_level(logging.INFO, logger="apps.core.cache"):
        results = [cache.get("k", "fallback"), cache.add("k", 1, 60), cache.incr("k"), cache.get_many(["k"])]
        cache.set("k", "v")
        cache.delete_many(["k"])

    # Every call answers what a miss answers, and only the first one waited for Redis.
    assert results == ["fallback", None, None, {}]
    assert redis.attempts == 1
    assert _messages(caplog) == ["cache.unavailable error=ConnectionError"]


def test_one_call_probes_redis_after_each_cooldown_without_logging_again(redis, clock, caplog):
    cache = caches["default"]
    with caplog.at_level(logging.INFO, logger="apps.core.cache"):
        cache.get("k")
        clock[0] += 4.9
        cache.get("k")
        assert redis.attempts == 1
        clock[0] += 0.2
        cache.get("k")
        cache.get("k")
        assert redis.attempts == 2
        clock[0] += 5.1
        cache.get("k")

    assert redis.attempts == 3
    assert _messages(caplog) == ["cache.unavailable error=ConnectionError"]


def test_a_probe_that_answers_closes_the_breaker_and_the_next_outage_is_logged(redis, clock, caplog):
    cache = caches["default"]
    with caplog.at_level(logging.INFO, logger="apps.core.cache"):
        cache.get("k")
        clock[0] += 6
        # Redis is back: the probe's call returns.
        with patch("django_redis.client.DefaultClient.get", return_value="v") as real_get:
            assert cache.get("k") == "v"
            assert cache.get("k") == "v"
            assert real_get.call_count == 2
        clock[0] += 1
        assert cache.get("k") is None

    assert redis.attempts == 2
    assert _messages(caplog) == [
        "cache.unavailable error=ConnectionError",
        "cache.recovered after_seconds=6",
        "cache.unavailable error=ConnectionError",
    ]


def test_closing_the_cache_is_not_a_probe(redis, clock):
    cache = caches["default"]
    cache.get("k")
    clock[0] += 6

    cache.close()
    cache.get("k")

    # close() did not use up the probe nor close the breaker: get() was the probe.
    assert redis.attempts == 2


def test_the_breaker_is_shared_by_the_threads_of_a_process(redis, clock):
    import threading

    caches["default"].get("k")
    thread = threading.Thread(target=lambda: caches["default"].get("k"))
    thread.start()
    thread.join()

    assert redis.attempts == 1


def test_the_locks_cache_has_no_breaker(redis, clock):
    caches["default"].get("k")

    for _ in range(3):
        with pytest.raises(RedisConnectionError):
            caches["locks"].add("lock", "locked", 60)

    # default: 1 attempt. locks: one per call, never skipped.
    assert redis.attempts == 4


def _refused_command(*args, **kwargs):
    """What django-redis raises when Redis answers a command with an error."""
    raise ConnectionInterrupted(connection=None) from ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")


def test_a_command_redis_refuses_does_not_open_the_breaker(redis, clock, caplog):
    cache = caches["default"]
    with caplog.at_level(logging.INFO, logger="apps.core.cache"), \
            patch("django_redis.client.DefaultClient.incr", side_effect=_refused_command), \
            patch("django_redis.client.DefaultClient.get", return_value="v") as real_get:
        assert cache.incr("lista") is None
        # The next call still goes to Redis.
        assert cache.get("k") == "v"

    assert real_get.call_count == 1
    assert redis.attempts == 0
    # The class of the error only: the message names the key.
    assert _messages(caplog) == ["cache.command_failed error=ResponseError"]


def test_a_probe_that_redis_answers_with_an_error_closes_the_breaker(redis, clock, caplog):
    cache = caches["default"]
    with caplog.at_level(logging.INFO, logger="apps.core.cache"):
        cache.get("k")
        clock[0] += 6
        with patch("django_redis.client.DefaultClient.incr", side_effect=_refused_command), \
                patch("django_redis.client.DefaultClient.get", return_value="v") as real_get:
            assert cache.incr("lista") is None
            assert cache.get("k") == "v"

    assert real_get.call_count == 1
    assert _messages(caplog) == [
        "cache.unavailable error=ConnectionError",
        "cache.recovered after_seconds=6",
        "cache.command_failed error=ResponseError",
    ]


def test_an_outage_that_goes_on_is_logged_again_as_an_error_every_few_minutes(redis, clock, caplog):
    cache = caches["default"]
    with caplog.at_level(logging.INFO, logger="apps.core.cache"):
        cache.get("k")
        # 13 minutes of failed probes, one every 6 seconds.
        for _ in range(130):
            clock[0] += 6
            cache.get("k")

    records = [record for record in caplog.records if record.name == "apps.core.cache"]
    assert [(record.levelname, record.getMessage()) for record in records] == [
        ("WARNING", "cache.unavailable error=ConnectionError"),
        ("ERROR", "cache.still_unavailable error=ConnectionError down_for_seconds=300"),
        ("ERROR", "cache.still_unavailable error=ConnectionError down_for_seconds=600"),
    ]
