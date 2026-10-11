"""
Unit tests for the versioned pricing table (AICORE-8, plan 3.2).

Covers Decimal math, cached-input discounts, price periods, composite
(provider, model) keys, unknown-model handling (None + log, never 0), the
once-per-process log dedupe, ASR minimum billable seconds and ProviderUsage
backward compatibility.
"""

import re
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from apps.videos.services.ai import pricing
from apps.videos.services.ai.contracts import ProviderUsage


class _PricingTestCase(unittest.TestCase):
    """Resets the once-per-process log dedupe so assertLogs is deterministic."""

    def setUp(self):
        pricing.reset_log_dedupe()
        self.addCleanup(pricing.reset_log_dedupe)


class TestProviderUsageBackwardCompat(unittest.TestCase):
    def test_new_fields_have_defaults(self):
        usage = ProviderUsage(provider="gemini", model="gemini/x")
        self.assertEqual(usage.role, "primary")
        self.assertEqual(usage.cache_read_tokens, 0)
        self.assertIsNone(usage.audio_seconds)
        self.assertIsNone(usage.pricing_version)
        self.assertIsNone(usage.estimated_cost_usd)
        self.assertIsNone(usage.resolved_model)

    def test_new_fields_accept_values(self):
        usage = ProviderUsage(
            provider="groq", model="m", role="fallback", cache_read_tokens=5,
            audio_seconds=12.5, pricing_version="2026-10-09",
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

    def test_version_is_a_real_date_not_in_the_future(self):
        parsed = date.fromisoformat(pricing.PRICING_VERSION)
        self.assertLessEqual(parsed, pricing._clock())

    def test_asr_rejects_non_finite_seconds(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(seconds=bad):
                with self.assertRaises(ValueError):
                    pricing.estimate_asr_cost("whisper-large-v3-turbo", bad)


class TestLlmCost(_PricingTestCase):
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

    def test_bare_name_defaults_to_openai_and_is_case_insensitive(self):
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

    def test_deliberately_unpriced_models_stay_unpriced(self):
        usage = self._usage(prompt_tokens=10, completion_tokens=10)
        unpriced = (
            "gemini/gemini-flash-latest",  # moving aliases
            "gemini/gemini-flash-lite-latest",
            "groq/llama-3.3-70b-versatile",  # "Contact Sales" on Groq
            "groq/llama-3.1-8b-instant",
            "groq/qwen/qwen3-32b",  # preview
        )
        for model in unpriced:
            with self.subTest(model=model):
                with self.assertLogs(pricing.logger, level="WARNING"):
                    self.assertIsNone(pricing.estimate_llm_cost(model, usage))

    def test_asr_model_is_not_an_llm_price(self):
        with self.assertLogs(pricing.logger, level="WARNING"):
            self.assertIsNone(
                pricing.estimate_llm_cost("whisper-large-v3-turbo", self._usage(prompt_tokens=5))
            )


class TestPricePeriods(_PricingTestCase):
    """gemini-3.8-flash: introductory price until 2026-12-31 inclusive."""

    def _cost(self, model, on, **kw):
        usage = ProviderUsage(provider="gemini", model="m", **kw)
        return pricing.estimate_llm_cost(model, usage, on=on)

    def test_last_day_of_intro_period_uses_intro_price(self):
        cost = self._cost(
            "gemini/gemini-3.8-flash", date(2026, 12, 31),
            prompt_tokens=1_000_000, completion_tokens=1_000_000,
        )
        self.assertEqual(cost, Decimal("0.75") + Decimal("3.75"))

    def test_first_day_after_intro_uses_new_price(self):
        cost = self._cost(
            "gemini/gemini-3.8-flash", date(2027, 1, 1),
            prompt_tokens=1_000_000, completion_tokens=1_000_000,
        )
        self.assertEqual(cost, Decimal("1.50") + Decimal("7.50"))

    def test_open_ended_period_applies_far_in_the_future(self):
        cost = self._cost("gemini/gemini-3.8-flash", date(2035, 6, 1), prompt_tokens=1_000_000)
        self.assertEqual(cost, Decimal("1.50"))

    def test_date_before_every_period_returns_none_and_logs(self):
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            cost = self._cost("gemini/gemini-3.8-flash", date(2020, 1, 1), prompt_tokens=10)
        self.assertIsNone(cost)
        message = cm.records[0].getMessage()
        self.assertIn("pricing.no_price_for_date", message)
        self.assertIn("gemini-3.8-flash", message)
        self.assertIn("2020-01-01", message)

    def test_default_date_comes_from_injectable_clock(self):
        usage = ProviderUsage(provider="gemini", model="m", prompt_tokens=1_000_000)
        with mock.patch.object(pricing, "_clock", lambda: date(2026, 11, 1)):
            intro = pricing.estimate_llm_cost("gemini/gemini-3.7-flash", usage)
        with mock.patch.object(pricing, "_clock", lambda: date(2027, 2, 1)):
            later = pricing.estimate_llm_cost("gemini/gemini-3.7-flash", usage)
        self.assertEqual(intro, Decimal("0.75"))
        self.assertEqual(later, Decimal("1.50"))

    def test_all_three_intro_models_share_the_schedule(self):
        for name in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"):
            with self.subTest(model=name):
                self.assertEqual(
                    self._cost(f"gemini/{name}", date(2026, 12, 31), completion_tokens=1_000_000),
                    Decimal("3.75"),
                )
                self.assertEqual(
                    self._cost(f"gemini/{name}", date(2027, 1, 1), completion_tokens=1_000_000),
                    Decimal("7.50"),
                )

    def test_periods_of_each_model_do_not_overlap(self):
        tables = list(pricing.LLM_PRICES.items()) + list(pricing.ASR_PRICES.items())
        for key, periods in tables:
            with self.subTest(model=key):
                ordered = sorted(periods, key=lambda p: p.valid_from or date.min)
                for prev, nxt in zip(ordered, ordered[1:]):
                    self.assertIsNotNone(prev.valid_until)
                    self.assertLess(prev.valid_until, nxt.valid_from)


class TestCompositeKeys(_PricingTestCase):
    def _cost(self, model, **kw):
        usage = ProviderUsage(provider="x", model="x", **kw)
        return pricing.estimate_llm_cost(model, usage, on=date(2026, 10, 9))

    def test_groq_gpt_oss_and_openai_models_do_not_collide(self):
        self.assertEqual(self._cost("groq/openai/gpt-oss-120b", prompt_tokens=1_000_000), Decimal("0.15"))
        self.assertEqual(self._cost("openai/gpt-4o-mini", prompt_tokens=1_000_000), Decimal("0.15"))
        self.assertEqual(self._cost("groq/openai/gpt-oss-120b", completion_tokens=1_000_000), Decimal("0.60"))
        self.assertEqual(self._cost("openai/gpt-4o-mini", completion_tokens=1_000_000), Decimal("0.60"))

    def test_openai_prefix_does_not_resolve_a_groq_only_model(self):
        with self.assertLogs(pricing.logger, level="WARNING"):
            self.assertIsNone(self._cost("openai/gpt-oss-120b", prompt_tokens=10))

    def test_gpt_oss_20b_priced(self):
        self.assertEqual(
            self._cost("groq/openai/gpt-oss-20b", prompt_tokens=1_000_000, completion_tokens=1_000_000),
            Decimal("0.075") + Decimal("0.30"),
        )

    def test_bare_gemini_name_is_not_priced_as_openai(self):
        with self.assertLogs(pricing.logger, level="WARNING"):
            self.assertIsNone(self._cost("gemini-2.5-flash", prompt_tokens=10))

    def test_new_gemini_models(self):
        cases = {
            "gemini/gemini-3.5-flash": ("1.50", "9.00"),
            "gemini/gemini-3.5-flash-lite": ("0.30", "2.50"),
            "gemini/gemini-3.1-flash-lite": ("0.25", "1.50"),
        }
        for model, (rate_in, rate_out) in cases.items():
            with self.subTest(model=model):
                self.assertEqual(
                    self._cost(model, prompt_tokens=1_000_000, completion_tokens=1_000_000),
                    Decimal(rate_in) + Decimal(rate_out),
                )

    def test_cached_rate_unknown_falls_back_to_input_rate(self):
        # gpt-oss-120b has no published cached price: cached billed as regular input.
        with_cache = self._cost(
            "groq/openai/gpt-oss-120b", prompt_tokens=1_000_000, cache_read_tokens=400_000
        )
        without = self._cost("groq/openai/gpt-oss-120b", prompt_tokens=1_000_000)
        self.assertEqual(with_cache, without)

    def test_cached_rate_known_is_applied_on_new_model(self):
        cost = self._cost(
            "gemini/gemini-3.5-flash", prompt_tokens=1_000_000, cache_read_tokens=500_000
        )
        self.assertEqual(cost, Decimal("0.75") + Decimal("0.075"))

    def test_decimal_exactness_small_counts(self):
        cost = self._cost("gemini/gemini-3.1-flash-lite", prompt_tokens=1234, completion_tokens=567)
        expected = (Decimal(1234) * Decimal("0.25") + Decimal(567) * Decimal("1.50")) / Decimal(1_000_000)
        self.assertEqual(cost, expected)


class TestLogDedupe(_PricingTestCase):
    def test_unknown_model_logged_once_per_process(self):
        usage = ProviderUsage(provider="x", model="x", prompt_tokens=1)
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            for _ in range(5):
                self.assertIsNone(pricing.estimate_llm_cost("gemini/mystery", usage))
        self.assertEqual(len(cm.records), 1)

    def test_no_price_for_date_logged_once_per_model_even_across_dates(self):
        usage = ProviderUsage(provider="x", model="x", prompt_tokens=1)
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            for day in (date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)):
                pricing.estimate_llm_cost("gemini/gemini-3.8-flash", usage, on=day)
        self.assertEqual(len(cm.records), 1)

    def test_distinct_reasons_and_models_are_logged_separately(self):
        usage = ProviderUsage(provider="x", model="x", prompt_tokens=1)
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            pricing.estimate_llm_cost("gemini/mystery-a", usage)
            pricing.estimate_llm_cost("gemini/mystery-b", usage)
            pricing.estimate_llm_cost("gemini/gemini-3.8-flash", usage, on=date(2020, 1, 1))
        self.assertEqual(len(cm.records), 3)

    def test_reset_allows_logging_again(self):
        usage = ProviderUsage(provider="x", model="x", prompt_tokens=1)
        with self.assertLogs(pricing.logger, level="WARNING"):
            pricing.estimate_llm_cost("gemini/mystery", usage)
        pricing.reset_log_dedupe()
        with self.assertLogs(pricing.logger, level="WARNING") as cm:
            pricing.estimate_llm_cost("gemini/mystery", usage)
        self.assertEqual(len(cm.records), 1)


class TestUnknownModel(_PricingTestCase):
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


class TestAsrCost(_PricingTestCase):
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

    def test_bare_asr_model_defaults_to_groq(self):
        self.assertEqual(
            pricing.estimate_asr_cost("whisper-large-v3-turbo", 3600.0),
            pricing.estimate_asr_cost("groq/whisper-large-v3-turbo", 3600.0),
        )

    def test_asr_accepts_explicit_date_and_legacy_price_is_open_ended(self):
        self.assertEqual(
            pricing.estimate_asr_cost("whisper-large-v3-turbo", 3600.0, on=date(2031, 1, 1)),
            Decimal("0.04"),
        )

    def test_asr_without_price_for_date_returns_none(self):
        bounded = {
            ("groq", "whisper-test"): (
                pricing.ASRPrice(Decimal("1"), Decimal(10), valid_until=date(2026, 1, 1)),
            )
        }
        with mock.patch.dict(pricing.ASR_PRICES, bounded):
            with self.assertLogs(pricing.logger, level="WARNING") as cm:
                cost = pricing.estimate_asr_cost("whisper-test", 60.0, on=date(2026, 1, 2))
        self.assertIsNone(cost)
        self.assertIn("pricing.no_price_for_date", cm.records[0].getMessage())

    def test_negative_seconds_rejected(self):
        with self.assertRaises(ValueError):
            pricing.estimate_asr_cost("whisper-large-v3-turbo", -1.0)


if __name__ == "__main__":
    unittest.main()
