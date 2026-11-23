"""
AICORE-12 (#64): the whole text is moderated in blocks and the verdict is per
category (ADR-011 D-M1, D-M5, D-M6, D-M7).
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from openai.types.moderation import Categories

from apps.core.security import (
    AI_Security_Shield,
    ModerationUnavailableError,
    UnsafeContentError,
    split_into_blocks,
)
from apps.videos.tests.test_moderation_fail_closed import OPENAI_CLIENT, status_error

import openai

SECRET = "texto-privado-del-usuario"


def verdict(**categories):
    """Verdict whose flagged value follows the categories, like the real API."""
    flagged = any(categories.values())
    return SimpleNamespace(
        results=[SimpleNamespace(flagged=flagged, categories=SimpleNamespace(**categories))]
    )


CLEAN = verdict(hate=False)


def sdk_verdict(**flags):
    """Verdict built with the real SDK model (aliases such as "self-harm/intent")."""
    values = {field.alias or name: False for name, field in Categories.model_fields.items()}
    values.update(flags)
    categories = Categories.model_validate(values)
    return SimpleNamespace(
        results=[SimpleNamespace(flagged=any(flags.values()), categories=categories)]
    )


def client_with(*responses):
    client = MagicMock()
    client.moderations.create.side_effect = list(responses)
    return client


def long_text(blocks, block_chars=10000):
    word = "palabra "
    return (word * (block_chars // len(word))) * blocks


class SplitIntoBlocksTests(SimpleTestCase):
    def test_short_text_is_a_single_block(self):
        self.assertEqual(split_into_blocks("  hola mundo  ", 100), ["hola mundo"])

    def test_blocks_respect_the_limit_and_never_split_words(self):
        text = " ".join(f"w{i}" for i in range(5000))
        blocks = split_into_blocks(text, 1000)
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(len(b) <= 1000 for b in blocks))
        self.assertEqual(" ".join(blocks).split(), text.split())

    def test_a_token_longer_than_the_block_is_cut_hard(self):
        self.assertEqual(split_into_blocks("x" * 25, 10), ["x" * 10, "x" * 10, "x" * 5])

    def test_whitespace_only_text_has_no_blocks(self):
        self.assertEqual(split_into_blocks(" \n\t ", 10), [])

    def test_non_positive_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            split_into_blocks("hola", 0)


@override_settings(OPENAI_API_KEY="test-key")
class ModerationByBlocksTests(SimpleTestCase):
    def check(self, client, text):
        with patch(OPENAI_CLIENT, return_value=client):
            return AI_Security_Shield.check_content_safety(text)

    def test_short_text_makes_one_call_with_the_pinned_model(self):
        client = client_with(CLEAN)
        self.assertTrue(self.check(client, "hola"))
        client.moderations.create.assert_called_once_with(
            input="hola", model="omni-moderation-latest"
        )

    def test_content_flagged_after_the_first_10000_characters_is_detected(self):
        text = long_text(3)
        blocks = split_into_blocks(text, 10000)
        client = client_with(*([CLEAN] * (len(blocks) - 1)), verdict(hate=True))
        with self.assertRaises(UnsafeContentError):
            self.check(client, text)
        self.assertEqual(client.moderations.create.call_count, len(blocks))

    def test_stops_at_the_first_blocking_block(self):
        client = client_with(verdict(hate=True), CLEAN, CLEAN)
        with self.assertRaises(UnsafeContentError):
            self.check(client, long_text(3))
        self.assertEqual(client.moderations.create.call_count, 1)

    def test_an_error_in_a_later_block_never_lets_the_text_pass(self):
        error = status_error(openai.InternalServerError, 503)
        client = client_with(CLEAN, error)
        with self.assertRaises(ModerationUnavailableError):
            self.check(client, long_text(3))
        self.assertEqual(client.moderations.create.call_count, 2)

    @override_settings(MODERATION_BLOCK_CHARS=20)
    def test_block_size_is_configurable(self):
        client = client_with(*([CLEAN] * 10))
        self.check(client, "una frase bastante larga para partir en varios bloques")
        self.assertGreater(client.moderations.create.call_count, 1)


@override_settings(OPENAI_API_KEY="test-key")
class PerCategoryVerdictTests(SimpleTestCase):
    def check(self, response):
        with patch(OPENAI_CLIENT, return_value=client_with(response)):
            return AI_Security_Shield.check_content_safety("texto")

    def test_gaming_categories_are_measured_but_do_not_block(self):
        self.assertTrue(self.check(verdict(violence=True, harassment=True)))

    def test_a_blocking_category_blocks_even_next_to_non_blocking_ones(self):
        with self.assertRaises(UnsafeContentError):
            self.check(verdict(violence=True, hate=True))

    def test_a_category_unknown_to_the_allow_list_blocks(self):
        with self.assertRaises(UnsafeContentError):
            self.check(verdict(brand_new_category=True))

    def test_flagged_without_any_category_blocks(self):
        response = SimpleNamespace(
            results=[SimpleNamespace(flagged=True, categories=SimpleNamespace(hate=False))]
        )
        with self.assertRaises(UnsafeContentError):
            self.check(response)

    def test_non_bool_category_is_a_failure_to_moderate(self):
        response = SimpleNamespace(
            results=[SimpleNamespace(flagged=True, categories=SimpleNamespace(hate="yes"))]
        )
        with self.assertRaises(ModerationUnavailableError):
            self.check(response)

    def test_real_sdk_aliases_are_understood(self):
        # "self-harm" alone is allowed; "self-harm/intent" and "violence/graphic" block.
        self.assertTrue(self.check(sdk_verdict(**{"self-harm": True, "violence": True})))
        for blocking in ("self-harm/intent", "violence/graphic", "sexual/minors", "illicit/violent"):
            with self.subTest(category=blocking), self.assertRaises(UnsafeContentError):
                self.check(sdk_verdict(**{blocking: True}))

    @override_settings(MODERATION_NON_BLOCKING_CATEGORIES=[])
    def test_the_allow_list_is_configurable(self):
        with self.assertRaises(UnsafeContentError):
            self.check(verdict(violence=True))


@override_settings(OPENAI_API_KEY="test-key")
class ModerationMetricsLogTests(SimpleTestCase):
    def test_logs_carry_counts_and_category_names_but_no_text(self):
        client = client_with(verdict(violence=True), CLEAN)
        text = f"{SECRET} " * 400  # ~10.400 chars: 2 blocks
        with self.assertLogs("apps.core.security", level=logging.INFO) as logs:
            with patch(OPENAI_CLIENT, return_value=client):
                AI_Security_Shield.check_content_safety(text)
        output = "\n".join(logs.output)
        self.assertNotIn(SECRET, output)
        self.assertIn("moderation.passed blocks=2", output)
        self.assertIn("non_blocking_flagged=violence", output)

    def test_blocked_log_names_the_category_and_the_block(self):
        client = client_with(CLEAN, verdict(hate=True))
        with self.assertLogs("apps.core.security", level=logging.WARNING) as logs:
            with patch(OPENAI_CLIENT, return_value=client):
                with self.assertRaises(UnsafeContentError):
                    AI_Security_Shield.check_content_safety(f"{SECRET} " * 400)
        output = "\n".join(logs.output)
        self.assertNotIn(SECRET, output)
        self.assertIn("moderation.blocked block=2/2 categories=hate", output)
