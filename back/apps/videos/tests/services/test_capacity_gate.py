"""AICORE-11 (#60): capacity configuration and gate logic (no Valkey needed)."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.videos.services.ai.capacity import (
    Bucket,
    CapacityGate,
    CapacityUnavailable,
    buckets_for,
)

LIMITS = {
    "groq/whisper-large-v3-turbo": {
        "audio_seconds_per_hour": 5760, "audio_seconds_per_day": 23040, "requests_per_minute": 16,
    },
    "gemini/gemini-3.8-flash": {"tokens_per_minute": 160000, "requests_per_minute": 400},
}


class ScriptedStore:
    def __init__(self, *results):
        self.results = list(results)
        self.reserved = []
        self.adjusted = []

    def reserve(self, demands):
        self.reserved.append([(bucket.key, amount) for bucket, amount in demands])
        result = self.results.pop(0) if self.results else (True, 0.0)
        if isinstance(result, BaseException):
            raise result
        return result

    def adjust(self, bucket, delta):
        if isinstance(self.results and self.results[0], BaseException):
            raise self.results.pop(0)
        self.adjusted.append((bucket.key, round(delta, 3)))
        return 0.0


@override_settings(AI_CAPACITY_LIMITS=LIMITS)
class BucketConfigTests(SimpleTestCase):
    def test_a_resource_maps_to_every_window_configured_for_it(self):
        buckets = buckets_for("groq/whisper-large-v3-turbo", "audio_seconds")
        self.assertEqual([(b.window, b.limit) for b in buckets], [("hour", 5760.0), ("day", 23040.0)])
        self.assertAlmostEqual(buckets[0].refill_per_second, 1.6)
        self.assertEqual(
            buckets[0].key, "capacity:v1:groq/whisper-large-v3-turbo:audio_seconds_per_hour"
        )

    def test_an_unconfigured_resource_has_no_bucket(self):
        self.assertEqual(buckets_for("gemini/gemini-3.8-flash", "audio_seconds"), [])

    def test_a_model_without_limits_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            buckets_for("openai/gpt-4o-mini", "tokens")

    def test_invalid_limits_are_rejected(self):
        for value in (0, -5, "x", float("inf")):
            with self.subTest(value=value), override_settings(
                AI_CAPACITY_LIMITS={"m": {"tokens_per_minute": value}}
            ):
                with self.assertRaises(ImproperlyConfigured):
                    buckets_for("m", "tokens")


@override_settings(AI_CAPACITY_LIMITS=LIMITS)
class GateTests(SimpleTestCase):
    def test_reserve_sends_every_bucket_of_the_demand_at_once(self):
        store = ScriptedStore()
        CapacityGate(store).reserve(
            {"groq/whisper-large-v3-turbo": {"audio_seconds": 600, "requests": 1}}
        )
        self.assertEqual(len(store.reserved), 1)
        self.assertEqual(sorted(store.reserved[0]), sorted([
            ("capacity:v1:groq/whisper-large-v3-turbo:audio_seconds_per_hour", 600.0),
            ("capacity:v1:groq/whisper-large-v3-turbo:audio_seconds_per_day", 600.0),
            ("capacity:v1:groq/whisper-large-v3-turbo:requests_per_minute", 1.0),
        ]))

    def test_no_room_raises_with_the_wait_and_the_stage(self):
        gate = CapacityGate(ScriptedStore((False, 42.0)))
        with self.assertRaises(CapacityUnavailable) as ctx:
            gate.reserve({"gemini/gemini-3.8-flash": {"tokens": 1000}}, stage="clips_selected")
        self.assertEqual((ctx.exception.wait_seconds, ctx.exception.stage), (42.0, "clips_selected"))

    def test_settle_applies_the_difference_and_keeps_unknown_usage(self):
        store = ScriptedStore()
        gate = CapacityGate(store)
        reservation = gate.reserve({"gemini/gemini-3.8-flash": {"tokens": 1000, "requests": 1}})
        gate.settle(reservation, {"gemini/gemini-3.8-flash": {"tokens": 1300}})  # requests unknown
        self.assertEqual(
            store.adjusted, [("capacity:v1:gemini/gemini-3.8-flash:tokens_per_minute", 300.0)]
        )

    def test_settle_never_raises(self):
        store = ScriptedStore()
        gate = CapacityGate(store)
        reservation = gate.reserve({"gemini/gemini-3.8-flash": {"tokens": 1000}})
        store.results = [ConnectionError("valkey down")]
        gate.settle(reservation, {"gemini/gemini-3.8-flash": {"tokens": 0}})  # no exception

    def test_unknown_resource_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            CapacityGate(ScriptedStore()).reserve({"gemini/gemini-3.8-flash": {"pixels": 1}})

    def test_bucket_key_and_refill(self):
        bucket = Bucket("m", "tokens", "day", 86400.0)
        self.assertEqual(bucket.refill_per_second, 1.0)
