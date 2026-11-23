"""AICORE-15 (#67): normalization, WER and anchor timing of the pilot evaluation."""

from django.test import SimpleTestCase

from apps.videos.services.ai.evaluation import (
    AnchorReport,
    Token,
    anchor_errors,
    normalize_text,
    normalize_tokens,
    percentile,
    strip_accents,
    word_error_rate,
)


class NormalizationTests(SimpleTestCase):
    def test_lowercase_no_punctuation_collapsed_spaces(self):
        self.assertEqual(
            normalize_text("  Hola,   ¿CÓMO   están?  ¡Bien!", keep_accents=True),
            ["hola", "cómo", "están", "bien"],
        )

    def test_accents_kept_or_stripped_but_enie_is_a_letter(self):
        self.assertEqual(normalize_text("Año está acá", keep_accents=True), ["año", "está", "acá"])
        self.assertEqual(normalize_text("Año está acá", keep_accents=False), ["año", "esta", "aca"])
        self.assertEqual(strip_accents("pingüino ñandú"), "pinguino ñandu")

    def test_thousands_separator_is_removed(self):
        self.assertEqual(normalize_text("1.000 y 25.000.000", True), ["1000", "y", "25000000"])

    def test_hyphenated_word_splits_and_keeps_its_start(self):
        tokens = normalize_tokens([Token("co-founder", 3.0)], True)
        self.assertEqual(tokens, [Token("co", 3.0), Token("founder", 3.0)])

    def test_spanish_numbers_become_digits(self):
        cases = {
            "veinticinco": "25",
            "veintitrés": "23",
            "treinta y dos": "32",
            "ciento veinte": "120",
            "quinientos cinco": "505",
            "novecientas noventa y nueve": "999",
            "dos mil veinticinco": "2025",
            "mil novecientos noventa y nueve": "1999",
            "cien mil": "100000",
            "diez mil quinientos": "10500",
            "un millón": "1000000",
            "dos millones trescientos mil": "2300000",
        }
        for words, digits in cases.items():
            with self.subTest(words=words):
                self.assertEqual(normalize_text(words, True), [digits])

    def test_numbers_said_in_a_row_are_not_merged(self):
        self.assertEqual(normalize_text("uno dos tres", True), ["1", "2", "3"])
        self.assertEqual(normalize_text("cero cero siete", True), ["0", "0", "7"])

    def test_words_that_only_look_like_numbers_stay(self):
        self.assertEqual(normalize_text("un video y una idea", True), ["un", "video", "y", "una", "idea"])
        self.assertEqual(normalize_text("treinta y", True), ["30", "y"])

    def test_digits_and_words_normalize_the_same(self):
        self.assertEqual(normalize_text("Tengo 25 años", True), normalize_text("tengo veinticinco años", True))

    def test_a_number_keeps_the_start_of_its_first_word(self):
        tokens = normalize_tokens([Token("treinta", 1.0), Token("y", 1.4), Token("dos", 1.6)], True)
        self.assertEqual(tokens, [Token("32", 1.0)])


class WordErrorRateTests(SimpleTestCase):
    def test_identical_text_has_no_errors(self):
        result = word_error_rate(["a", "b", "c"], ["a", "b", "c"])
        self.assertEqual((result.errors, result.wer), (0, 0.0))

    def test_substitution_deletion_and_insertion_are_counted(self):
        result = word_error_rate("el gato come pescado".split(), "el perro come".split())
        self.assertEqual((result.substitutions, result.deletions, result.insertions), (1, 1, 0))
        self.assertEqual(result.wer, 0.5)

        result = word_error_rate("hola mundo".split(), "hola hola mundo".split())
        self.assertEqual((result.substitutions, result.deletions, result.insertions), (0, 0, 1))

    def test_empty_reference_has_no_wer(self):
        result = word_error_rate([], ["algo"])
        self.assertIsNone(result.wer)
        self.assertEqual(result.insertions, 1)

    def test_empty_hypothesis_is_all_deletions(self):
        self.assertEqual(word_error_rate(["a", "b"], []).wer, 1.0)

    def test_long_transcript_is_exact(self):
        reference = [f"w{i % 300}" for i in range(2000)]
        hypothesis = list(reference)
        hypothesis[10] = "x"
        del hypothesis[500]
        hypothesis.insert(1500, "y")
        result = word_error_rate(reference, hypothesis)
        self.assertEqual((result.substitutions, result.deletions, result.insertions), (1, 1, 1))


def timed(*pairs):
    return [Token(text, start) for text, start in pairs]


HYPOTHESIS = normalize_tokens(timed(
    ("Hola", 0.10), ("a", 0.40), ("todos", 0.55),
    ("Hoy", 10.25), ("vamos", 10.60), ("a", 10.90), ("hablar", 11.00),
    ("de", 11.30), ("veinte", 11.50), ("personas", 11.90),
    ("Hoy", 40.00), ("vamos", 40.30),
), True)


class AnchorTests(SimpleTestCase):
    def test_error_is_the_distance_to_the_matching_sentence_start(self):
        report = anchor_errors([(0.0, "Hola a todos"), (10.0, "Hoy vamos a hablar")], HYPOTHESIS, True)
        self.assertEqual(report.matched, 2)
        self.assertEqual([round(e) for e in report.errors_ms], [100, 250])

    def test_the_closest_occurrence_in_the_window_wins(self):
        report = anchor_errors([(39.5, "Hoy vamos")], HYPOTHESIS, True)
        self.assertEqual([round(e) for e in report.errors_ms], [500])

    def test_an_occurrence_outside_the_window_is_unmatched(self):
        report = anchor_errors([(25.0, "Hoy vamos")], HYPOTHESIS, True, window_s=5.0)
        self.assertEqual((report.matched, report.unmatched), (0, 1))

    def test_anchor_numbers_are_normalized_like_the_hypothesis(self):
        report = anchor_errors([(11.4, "20 personas")], HYPOTHESIS, True)
        self.assertEqual([round(e) for e in report.errors_ms], [100])

    def test_a_single_common_word_does_not_match_a_longer_anchor(self):
        report = anchor_errors([(10.0, "a nadie le gusta")], HYPOTHESIS, True)
        self.assertEqual(report.unmatched, 1)

    def test_percentiles_and_merge(self):
        self.assertEqual(percentile([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 90), 90)
        self.assertIsNone(percentile([], 50))
        merged = AnchorReport(2, [100.0]).merge(AnchorReport(1, [300.0]))
        self.assertEqual((merged.total, merged.matched, merged.median_ms), (3, 2, 200.0))
