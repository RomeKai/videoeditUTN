"""
AICORE-16 (#68): clip boundaries anchored to real words and to the target
duration (15-60 s), on a synthetic word-level transcript.

The synthetic transcript: sentences of 10 words (0.4 s each, 0.1 s between
words, the last one ending in "."), separated by 0.8 s pauses. Natural
boundaries are therefore the sentence ends, every 5.8 s.
"""

import json
import logging
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.videos.services.ai.clip_validation import ClipRules, sanitize_clips
from apps.videos.services.ai.contracts import ProviderUsage

RULES = ClipRules(min_seconds=15, max_seconds=60, snap_tolerance_seconds=1.0, pause_seconds=0.5)
SECRET_TITLE = "TITULO-SECRETO-DEL-CLIP"


def synthetic_words(sentences=31, word=0.4, gap=0.1, pause=0.8):
    words, t = [], 0.0
    for s in range(sentences):
        for w in range(10):
            text = f"palabra{s}_{w}" + ("." if w == 9 else "")
            words.append({"start": round(t, 3), "end": round(t + word, 3), "text": text})
            t += word + (pause if w == 9 else gap)
    return words


WORDS = synthetic_words()  # ~180 s
VIDEO = 180.0
SENTENCE_ENDS = [w["end"] for w in WORDS if w["text"].endswith(".")]
SENTENCE_STARTS = [w["start"] for w in WORDS if w["text"].endswith("_0")]
WORD_STARTS = {w["start"] for w in WORDS}
WORD_ENDS = {w["end"] for w in WORDS}


def clip(start, end, score=80, title="Clip"):
    return {"start": start, "end": end, "title": title, "virality_score": score, "reasoning": "r"}


def anchored(candidates, video=VIDEO, words=WORDS, target=None, rules=RULES):
    return sanitize_clips(candidates, video, target, words=words, rules=rules)


class WordSnappingTests(SimpleTestCase):
    def test_a_clip_starting_mid_word_starts_at_that_word(self):
        word = WORDS[12]  # inside the second sentence
        start = word["start"] + 0.2
        end = SENTENCE_ENDS[4] + 0.15  # ~0.15 s after a word end: within tolerance

        clips, report = anchored([clip(start, end)])

        self.assertEqual(clips[0].start, word["start"])
        self.assertEqual(clips[0].end, SENTENCE_ENDS[4])
        self.assertEqual(report.snapped, 1)
        self.assertTrue(report.anchored)

    def test_a_boundary_inside_a_long_word_never_cuts_it_even_beyond_tolerance(self):
        words = [
            {"start": 0.0, "end": 3.0, "text": "largo"},
            {"start": 3.1, "end": 20.0, "text": "eterno."},
            {"start": 21.0, "end": 40.0, "text": "final."},
        ]
        rules = ClipRules(15, 60, snap_tolerance_seconds=0.1, pause_seconds=0.5)

        clips, _ = anchored([clip(1.5, 30.0)], video=40.0, words=words, rules=rules)

        # 1.5 is 1.5 s from both edges of "largo": still moved to its start;
        # 30.0 is inside "final.": moved to its end.
        self.assertEqual((clips[0].start, clips[0].end), (0.0, 40.0))

    def test_a_start_in_a_long_silence_far_from_words_is_left_alone(self):
        words = [
            {"start": 0.0, "end": 1.0, "text": "hola."},
            {"start": 10.0, "end": 30.0, "text": "mundo."},
        ]
        clips, report = anchored([clip(5.0, 30.0)], video=30.0, words=words)

        self.assertEqual(clips[0].start, 5.0)  # silence: no word is cut
        self.assertEqual(report.snapped, 0)

    def test_every_kept_boundary_lands_on_a_word_edge(self):
        candidates = [clip(3.33, 25.07, 90), clip(40.21, 52.9, 70), clip(100.05, 290.0, 60)]

        clips, _ = anchored(candidates)

        for kept in clips:
            self.assertIn(kept.start, WORD_STARTS)
            self.assertIn(kept.end, WORD_ENDS)


class DurationTests(SimpleTestCase):
    def test_a_5_second_clip_is_extended_to_the_next_natural_boundary(self):
        start = SENTENCE_STARTS[2]

        clips, report = anchored([clip(start, start + 5.0)])

        kept = clips[0]
        self.assertEqual(kept.start, start)
        self.assertIn(kept.end, SENTENCE_ENDS)
        self.assertGreaterEqual(kept.end - kept.start, 15)
        self.assertEqual(kept.end, min(e for e in SENTENCE_ENDS if e >= start + 15))
        self.assertEqual(report.extended, 1)

    def test_a_3_minute_clip_is_trimmed_at_the_last_natural_boundary_within_60_s(self):
        start = SENTENCE_STARTS[0]

        clips, report = anchored([clip(start, start + 180.0)])

        kept = clips[0]
        self.assertIn(kept.end, SENTENCE_ENDS)
        self.assertLessEqual(kept.end - kept.start, 60)
        self.assertEqual(kept.end, max(e for e in SENTENCE_ENDS if e <= start + 60))
        self.assertEqual(report.trimmed, 1)

    def test_a_clip_already_within_15_60_keeps_its_length(self):
        start, end = SENTENCE_STARTS[1], SENTENCE_ENDS[5]

        clips, report = anchored([clip(start, end)])

        self.assertEqual((clips[0].start, clips[0].end), (start, end))
        self.assertEqual((report.extended, report.trimmed, report.snapped), (0, 0, 0))

    def test_a_short_clip_that_cannot_grow_without_overlapping_is_dropped_and_counted(self):
        # B (lower score) sits at the very start, right before A: growing forward
        # would overlap A and there is nothing before it to grow backward into.
        a = clip(SENTENCE_STARTS[1], SENTENCE_ENDS[5], score=90, title="A")
        b = clip(SENTENCE_STARTS[0], SENTENCE_ENDS[0], score=50, title="B")

        clips, report = anchored([b, a])

        self.assertEqual([c.title for c in clips], ["A"])
        self.assertEqual(report.duration_unmet, 1)
        self.assertEqual(report.kept, 1)

    def test_a_short_clip_at_the_end_of_the_video_grows_backward(self):
        last_start = SENTENCE_STARTS[-1]

        clips, report = anchored([clip(last_start, SENTENCE_ENDS[-1])])

        kept = clips[0]
        self.assertEqual(kept.end, SENTENCE_ENDS[-1])
        self.assertIn(kept.start, SENTENCE_STARTS)
        self.assertGreaterEqual(kept.end - kept.start, 15)
        self.assertEqual(kept.start, max(s for s in SENTENCE_STARTS if s <= kept.end - 15))
        self.assertEqual(report.extended, 1)

    def test_a_long_clip_without_a_natural_boundary_inside_the_window_is_dropped(self):
        words = [{"start": float(i), "end": i + 0.9, "text": f"w{i}"} for i in range(200)]
        words[-1]["text"] += "."  # the only natural boundary is the very end

        clips, report = anchored([clip(0.0, 150.0)], video=200.0, words=words)

        self.assertEqual(clips, [])
        self.assertEqual(report.duration_unmet, 1)

    def test_a_video_shorter_than_the_minimum_gives_one_whole_clip(self):
        words = synthetic_words(sentences=2)  # ~10.8 s
        duration = words[-1]["end"]

        clips, _ = anchored([clip(0.0, 4.0)], video=duration + 0.2, words=words)

        self.assertEqual((clips[0].start, clips[0].end), (0.0, duration))

    def test_limits_follow_the_rules(self):
        rules = ClipRules(min_seconds=20, max_seconds=30, snap_tolerance_seconds=1.0, pause_seconds=0.5)
        start = SENTENCE_STARTS[0]

        clips, _ = anchored([clip(start, start + 5), clip(SENTENCE_STARTS[10], 179)], rules=rules)

        for kept in clips:
            self.assertGreaterEqual(kept.end - kept.start, 20)
            self.assertLessEqual(kept.end - kept.start, 30)


class UnanchoredModeTests(SimpleTestCase):
    def test_segment_level_items_only_apply_the_video_limits(self):
        segments = [
            {"start": 0.0, "end": 4.0, "text": "una frase con varias palabras"},
            {"start": 4.0, "end": 9.0, "text": "otra frase completa"},
        ]

        clips, report = anchored([clip(1.3, 6.2)], video=9.0, words=segments)

        self.assertEqual((clips[0].start, clips[0].end), (1.3, 6.2))  # as before AICORE-16
        self.assertFalse(report.anchored)

    def test_without_items_nothing_changes(self):
        clips, report = sanitize_clips([clip(1.3, 6.2)], 9.0)

        self.assertEqual((clips[0].start, clips[0].end), (1.3, 6.2))
        self.assertFalse(report.anchored)


class ReportAndRulesTests(SimpleTestCase):
    def test_the_log_carries_counts_only(self):
        start = SENTENCE_STARTS[2]
        with self.assertLogs("apps.videos.services.ai.clip_validation", logging.INFO) as logs:
            anchored([clip(start + 0.1, start + 5, title=SECRET_TITLE)])

        line = "\n".join(logs.output)
        self.assertIn("snapped=1", line)
        self.assertIn("extended=1", line)
        self.assertNotIn(SECRET_TITLE, line)
        self.assertNotIn("palabra", line)

    def test_invalid_rules_are_rejected(self):
        for kwargs in ({"min_seconds": 0}, {"min_seconds": 30, "max_seconds": 20},
                       {"pause_seconds": -1}, {"snap_tolerance_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ClipRules(**kwargs)

    @override_settings(AI_CLIP_MIN_SECONDS=20, AI_CLIP_MAX_SECONDS=45,
                       AI_CLIP_SNAP_TOLERANCE_SECONDS=0.3, AI_CLIP_PAUSE_SECONDS=0.7)
    def test_rules_come_from_settings(self):
        self.assertEqual(ClipRules.from_settings(), ClipRules(20, 45, 0.3, 0.7))


class SelectorWiringTests(SimpleTestCase):
    """Both selection paths pass the transcript items to the validator."""

    def answer(self, *clips):
        return json.dumps({"clips": list(clips)})

    def test_v2_provider_anchors_clips(self):
        from apps.videos.services.ai.litellm_selection import LiteLLMSelectionProvider
        from apps.videos.tests.services.test_litellm_selection import _build_provider

        provider = _build_provider()
        start = SENTENCE_STARTS[2]
        usage = ProviderUsage(provider="gemini", model="gemini/x")
        with patch.object(
            LiteLLMSelectionProvider, "_call_llm", return_value=(self.answer(clip(start, start + 5)), usage)
        ):
            result = provider.select_clips("t", video_duration=VIDEO, target_count=3, segments=WORDS)

        kept = result.data.clips[0]
        self.assertIn(kept.end, SENTENCE_ENDS)
        self.assertGreaterEqual(kept.end - kept.start, 15)

    def test_legacy_selector_applies_the_same_rules(self):
        from apps.videos.services.selection_engine import SelectionEngine

        start = SENTENCE_STARTS[0]
        clips = SelectionEngine._parse_legacy_answer(
            self.answer(clip(start, start + 180)), VIDEO, {"segments": WORDS}, 3
        )

        self.assertLessEqual(clips[0]["end"] - clips[0]["start"], 60)
        self.assertIn(clips[0]["end"], SENTENCE_ENDS)

    @override_settings(AI_CLIP_MIN_SECONDS=20, AI_CLIP_MAX_SECONDS=45)
    def test_prompts_state_the_configured_duration(self):
        from apps.videos.tests.services.test_litellm_selection import _build_provider

        messages = _build_provider()._build_messages(
            transcript="t", video_duration=VIDEO, target_count=3, editing_style="dynamic"
        )
        self.assertIn("20-45 seconds", messages[0]["content"])
