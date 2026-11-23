"""
AICORE-11 (#60): the Lua token buckets against a REAL Valkey/Redis.

Atomicity and the server clock cannot be tested against a fake, so these
tests need ``CAPACITY_TEST_REDIS_URL`` (CI provides a Valkey service; locally:
``redis-server --port 6390`` and ``CAPACITY_TEST_REDIS_URL=redis://127.0.0.1:6390/15``).
They are skipped without it. The database in the URL is flushed per test.
"""

import os
import threading
import uuid
from unittest import skipUnless

from django.test import SimpleTestCase, override_settings

from apps.videos.services.ai.capacity import (
    Bucket,
    CapacityGate,
    CapacityUnavailable,
    RedisCapacityStore,
)

REDIS_URL = os.environ.get("CAPACITY_TEST_REDIS_URL")


def client():
    import redis

    return redis.Redis.from_url(REDIS_URL)


@skipUnless(REDIS_URL, "Set CAPACITY_TEST_REDIS_URL to run the capacity tests against Valkey")
class RedisCapacityStoreTests(SimpleTestCase):
    def setUp(self):
        self.redis = client()
        self.redis.flushdb()
        self.store = RedisCapacityStore(self.redis)
        self.model = f"m-{uuid.uuid4().hex[:8]}"

    def bucket(self, resource="tokens", window="minute", limit=600.0):
        return Bucket(self.model, resource, window, limit)

    def level(self, bucket):
        return float(self.redis.hget(bucket.key, "tokens"))

    def test_an_absent_bucket_is_full_and_a_reservation_draws_from_it(self):
        bucket = self.bucket(limit=600.0)
        self.assertEqual(self.store.reserve([(bucket, 500)]), (True, 0.0))
        self.assertAlmostEqual(self.level(bucket), 100.0, delta=1.0)

    def test_without_room_nothing_is_drawn_and_the_wait_matches_the_deficit(self):
        bucket = self.bucket(limit=600.0)  # refills 10 per second
        self.store.reserve([(bucket, 500)])
        granted, wait = self.store.reserve([(bucket, 200)])
        self.assertFalse(granted)
        self.assertAlmostEqual(wait, 10.0, delta=0.5)  # 100 missing / 10 per second
        self.assertAlmostEqual(self.level(bucket), 100.0, delta=1.0)

    def test_several_buckets_are_drawn_all_or_none(self):
        tokens = self.bucket("tokens", limit=600.0)
        requests = self.bucket("requests", limit=6.0)
        self.store.reserve([(requests, 6)])
        granted, _ = self.store.reserve([(tokens, 10), (requests, 1)])
        self.assertFalse(granted)
        self.assertIsNone(self.redis.hget(tokens.key, "tokens"))  # never touched

    def test_an_oversized_demand_waits_for_a_full_bucket_then_delays_the_next(self):
        hour = self.bucket("audio_seconds", "hour", 3600.0)  # refills 1 per second
        self.assertEqual(self.store.reserve([(hour, 5000)]), (True, 0.0))
        self.assertAlmostEqual(self.level(hour), -1400.0, delta=1.0)
        granted, wait = self.store.reserve([(hour, 10)])
        self.assertFalse(granted)
        self.assertAlmostEqual(wait, 1410.0, delta=2.0)
        self.assertGreater(self.redis.ttl(hour.key), 0)  # negative only until it refills

    def test_settlement_refunds_up_to_the_limit_and_can_draw_more(self):
        bucket = self.bucket(limit=600.0)
        self.store.reserve([(bucket, 500)])
        self.assertAlmostEqual(self.store.adjust(bucket, -10_000), 600.0)  # capped at the limit
        self.assertAlmostEqual(self.store.adjust(bucket, 900), -300.0, delta=1.0)

    def test_concurrent_workers_never_exceed_the_limit(self):
        bucket = self.bucket("requests", "day", 10.0)  # refill ~0.0001 per second
        granted = []

        def worker():
            store = RedisCapacityStore(client())
            granted.append(store.reserve([(bucket, 1)])[0])

        threads = [threading.Thread(target=worker) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(granted), 10)

    def test_gate_with_the_real_store(self):
        limits = {self.model: {"audio_seconds_per_hour": 3600, "audio_seconds_per_day": 7200}}
        with override_settings(AI_CAPACITY_LIMITS=limits):
            gate = CapacityGate(self.store)
            reservation = gate.reserve({self.model: {"audio_seconds": 3000}}, stage="transcribed")
            with self.assertRaises(CapacityUnavailable) as ctx:
                gate.reserve({self.model: {"audio_seconds": 1000}}, stage="transcribed")
            self.assertEqual(ctx.exception.stage, "transcribed")
            gate.settle(reservation, {self.model: {"audio_seconds": 1500}})  # refund 1500
            gate.reserve({self.model: {"audio_seconds": 1000}}, stage="transcribed")
