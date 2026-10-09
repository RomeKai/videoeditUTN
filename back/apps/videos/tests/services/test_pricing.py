"""
Unit tests for the versioned pricing table (AICORE-8, plan 3.2).

Covers Decimal math, cached-input discounts, unknown-model handling
(None + log, never 0), ASR minimum billable seconds and ProviderUsage
backward compatibility.
"""

import logging
import re
import unittest
from decimal import Decimal

from apps.videos.services.ai import pricing
from apps.videos.services.ai.contracts import ProviderUsage


class TestProviderUsageBackwardCompat(unittest.TestCase):
    def test_new_fields_have_defaults(self):
        usage = ProviderUsage(provider="gemini", model="gemini/x")
        self.assertEqual(usage.role, "primary")
        self.assertEqual(usage.cache_read_tokens, 0)
        self.assertIsNone(usage.audio_seconds)
        self.assertIsNone(usage.pricing_version)
        self.assertIsNone(usage.estimated_cost_usd)

    def test_new_fields_accept_values(self):
        usage = ProviderUsage(
            provider="groq", model="m", role="fallback", cache_read_tokens=5,
            audio_seconds=12.5, pricing_version="2026-10-08",
        )
        self.assertEqual(usage.role, "fallback")
        self.assertEqual(usage.cache_read_tokens, 5)

    def test_invalid_role_and_negative_cache_rejected(self):
        with self.assertRaises(ValueError):
            ProviderUsage(provider="a", model="b", role="tertiary")
        with self.assertRaises(ValueError):
            ProviderUsage(provider="a", model="b", cache_read_tokens=-1)


class TestPricingVersion(unittest.TestCase):
    def test_version_is_iso_date(self):
        self.assertRegex(pricing.PRICING_VERSION, r"^\d{4}-\d{2}-\d{2}$")


class TestLlmCost(unittest.TestCase):
    def _usage(self, **kw):
        return ProviderUsage(provider="openai", model="openai/gpt-4o-mini", **kw)

    def test_decimal_math_without_cache(self):
        # gpt-4o-mini: 0.15 in / 0.60 out per Mtok
        cost = pricing.estimate_llm_cost(
            "openai/gpt-4o-mini",
            self._usage(prompt_tokens=1_000_000, completion_tokens=500_000),
        )
        self.assertIsInstance(cost, Decimal)
        self.assertEqual(cost, Decimal("0.15") + Decimal("0.30"))

    def test_cached_tokens_billed_at_cached_rate(self):
        # 1M prompt of which 400k cached: 600k*0.15 + 400k*0.075 per Mtok
        cost = pricing.estimate_llm_cost(
            "gpt-4o-mini",
            self._usage(prompt_tokens=1_000_000, cache_read_tokens=400_000),
        )
        self.assertEqual(cost, Decimal("0.09") + Decimal("0.03"))

    def test_cached_tokens_clamped_to_prompt(self):
        cost = pricing.estimate_llm_cost(
            "gpt-4o-mini", self._usage(prompt_tokens=100, cache_read_tokens=999)
        )
        expected = Decimal(100) * Decimal("0.075") / Decimal(1_000_000)
        self.assertEqual(cost, expected)

    def test_prefix_normalisation(self):
        usage = self._usage(prompt_tokens=1000, completion_tokens=1000)
        a = pricing.estimate_llm_cost("openai/gpt-4o-mini", usage)
        b = pricing.estimate_llm_cost("gpt-4o-mini", usage)
        c = pricing.estimate_llm_cost("GPT-4o-mini", usage)
        self.assertIsNotNone(a)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_gemini_priced_model(self):
        cost = pricing.estimate_llm_cost(
            "gemini/gemini-2.5-flash-lite",
            ProviderUsage(provider="gemini", model="m", prompt_tokens=1_000_000, completion_tokens=1_000_000),
        )
        self.assertEqual(cost, Decimal("0.10") + Decimal("0.40"))

    def test_zero_usage_known_model_is_zero_decimal(self):
        cost = pricing.estimate_llm_cost("gpt-4o-mini", self._usage())
        self.assertEqual(cost, Decimal("0"))

    def test_latest_aliases_are_unpriced(self):
        usage = self._usage(prompt_tokens=10, completion_tokens=10)
        for model in ("gemini/gemini-flash-latest", "gemini/gemini-flash-lite-latest"):
            with self.assertLogs(pricing.logger, level="WARNING"):
                self.assertIsNone(pricing.estimate_llm_cost(model, usage))

    def test_asr_model_is_not_an_llm_price(self):
        with self.assertLogs(pricing.logger, level="WARNING"):
            self.assertIsNone(
                pricing.estimate_llm_cost("whisper-large-v3-turbo", self._usage(prompt_tokens=5))
            )


class TestUnknownModel(unittest.TestCase):
    def test_unknown_llm_returns_none_and_logs_model_only(self):
        usage = ProviderUsage(provider="x", model="x", prompt_tokens=10, completion_tokens=10)
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            result = pricing.estimate_llm_cost("gemini/mystery-model", usage)
        self.assertIsNone(result)
        self.assertEqual(len(cm.records), 1)
        message = cm.records[0].getMessage()
        self.assertIn("pricing.unknown_model", message)
        self.assertIn("mystery-model", message)

    def test_unknown_asr_returns_none_not_zero(self):
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            result = pricing.estimate_asr_cost("whisper-future", 3600.0)
        self.assertIsNone(result)
        self.assertIn("pricing.unknown_model", cm.records[0].getMessage())

    def test_log_record_has_no_user_text(self):
        secret = "SECRET_TRANSCRIPT_X"
        usage = ProviderUsage(provider="x", model="x", prompt_tokens=10)
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            pricing.estimate_llm_cost("unknown-model", usage)
        for record in cm.records:
            rendered = record.getMessage() + repr(record.args) + repr(record.__dict__.get("msg"))
            self.assertNotIn(secret, rendered)
            self.assertIsNone(re.search(r"prompt|transcript", record.getMessage(), re.I))


class TestAsrCost(unittest.TestCase):
    def test_turbo_hourly_rate(self):
        self.assertEqual(pricing.estimate_asr_cost("whisper-large-v3-turbo", 3600.0), Decimal("0.04"))

    def test_large_v3_hourly_rate(self):
        self.assertEqual(pricing.estimate_asr_cost("groq/whisper-large-v3", 3600.0), Decimal("0.111"))

    def test_turbo_cheaper_than_large_v3(self):
        turbo = pricing.estimate_asr_cost("whisper-large-v3-turbo", 1800.0)
        large = pricing.estimate_asr_cost("whisper-large-v3", 1800.0)
        self.assertLess(turbo, large)

    def test_minimum_billable_seconds_applied(self):
        short = pricing.estimate_asr_cost("whisper-large-v3-turbo", 2.0)
        ten = pricing.estimate_asr_cost("whisper-large-v3-turbo", 10.0)
        self.assertEqual(short, ten)
        self.assertGreater(short, Decimal("0"))

    def test_above_minimum_is_linear(self):
        self.assertEqual(
            pricing.estimate_asr_cost("whisper-large-v3-turbo", 1800.0),
            Decimal("0.02"),
        )

    def test_zero_seconds_known_model_costs_zero(self):
        self.assertEqual(pricing.estimate_asr_cost("whisper-large-v3-turbo", 0.0), Decimal("0"))

    def test_negative_seconds_rejected(self):
        with self.assertRaises(ValueError):
            pricing.estimate_asr_cost("whisper-large-v3-turbo", -1.0)


if __name__ == "__main__":
    unittest.main()
