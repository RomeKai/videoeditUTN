"""AICORE-15 (#67): ``evaluate_ai_transcription`` command (pilot G1 metrics)."""

import csv
import io
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoProject

User = get_user_model()
SECRET = "palabrasecretadelusuario"


def words(*pairs):
    return [{"start": start, "end": start + 0.3, "text": text} for text, start in pairs]


class EvaluateCommandTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="ev", email="ev@test.com", password="pw")
        self.workspace = Workspace.objects.create(name="W", owner=user)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def project(self, transcript, version=None):
        project = VideoProject.objects.create(
            workspace=self.workspace, title=SECRET, transcript_data=transcript
        )
        if version:
            AIUsageRecord.objects.create(
                project=project, stage=AIUsageRecord.Stage.TRANSCRIPTION, pipeline_version=version,
                attempt=1, role=AIUsageRecord.Role.PRIMARY, provider="groq", model="whisper",
                success=True, latency_seconds=1.0,
            )
        return project

    def write(self, name, content):
        (self.dir / name).write_text(content, encoding="utf-8")

    def manifest(self, *entries):
        self.write("manifest.csv", "video_id,project_id\n" + "".join(f"{v},{p}\n" for v, p in entries))

    def run_command(self, accents="keep", **kwargs):
        out, err = io.StringIO(), io.StringIO()
        call_command(
            "evaluate_ai_transcription", references=str(self.dir), accents=accents,
            stdout=out, stderr=err, **kwargs,
        )
        rows = list(csv.DictReader(io.StringIO(out.getvalue())))
        return rows, err.getvalue(), out.getvalue()

    def test_per_project_and_per_engine_metrics(self):
        v2 = self.project(words(("Hola", 0.1), ("a", 0.4), ("todos", 0.6), ("veinte", 5.2), ("personas", 5.5)), "v2")
        legacy = self.project(words(("Hola", 0.5), ("todo", 0.9), ("20", 6.0), ("personas", 6.3)), "legacy")
        self.manifest(("v01", v2.pk), ("v01", legacy.pk))
        self.write("v01.txt", "Hola a todos. 20 personas.")
        self.write("v01.anchors.csv", "time_seconds,text\n0.0,Hola a todos\n5.0,veinte personas\n")

        rows, summary, _ = self.run_command()

        by_project = {row["project_id"]: row for row in rows if row["video_id"] != "ALL"}
        row = by_project[str(v2.pk)]
        self.assertEqual((row["status"], row["pipeline_version"], row["wer"]), ("ok", "v2", "0.0000"))
        self.assertEqual((row["anchors_matched"], row["timestamp_error_median_ms"]), ("2", "150.0"))

        row = by_project[str(legacy.pk)]
        # "a" deleted, "todos" -> "todo": 2 errors over 5 reference words.
        self.assertEqual((row["deletions"], row["substitutions"], row["wer"]), ("1", "1", "0.4000"))
        # "Hola a todos" no longer matches with 2+ words; "20 personas" does (1000 ms).
        self.assertEqual((row["anchors_matched"], row["anchors_unmatched"]), ("1", "1"))

        totals = {row["pipeline_version"]: row for row in rows if row["video_id"] == "ALL"}
        self.assertEqual(set(totals), {"legacy", "v2"})
        self.assertIn("[v2] WER 0.00% (umbral 15%: cumple)", summary)
        self.assertIn("[legacy] WER 40.00% (umbral 15%: NO cumple)", summary)

    def test_corpus_wer_sums_errors_over_reference_words(self):
        first = self.project(words(("a", 0), ("b", 1), ("c", 2), ("d", 3)), "v2")  # 0 errors / 4 words
        second = self.project(words(("x", 0), ("y", 1)), "v2")  # "x z" -> "x y": 1 error / 2 words
        self.manifest(("v01", first.pk), ("v02", second.pk))
        self.write("v01.txt", "a b c d")
        self.write("v02.txt", "x z")

        rows, _, _ = self.run_command()

        total = next(row for row in rows if row["video_id"] == "ALL")
        # (0 + 1) / (4 + 2), not the mean of 0% and 50%.
        self.assertEqual(total["wer"], "0.1667")

    def test_accents_decision_changes_the_result(self):
        project = self.project(words(("esta", 0.0), ("aca", 0.3)), "v2")
        self.manifest(("v01", project.pk))
        self.write("v01.txt", "está acá")

        keep, _, _ = self.run_command(accents="keep")
        strip, _, _ = self.run_command(accents="strip")

        self.assertEqual(keep[0]["wer"], "1.0000")
        self.assertEqual(strip[0]["wer"], "0.0000")

    def test_problem_rows_get_a_status_and_no_metrics(self):
        no_text = self.project([], "v2")
        missing_ref = self.project(words(("hola", 0.0)), "v2")
        self.manifest(
            ("v01", no_text.pk), ("v02", missing_ref.pk),
            ("v03", "00000000-0000-0000-0000-000000000000"),
        )
        self.write("v01.txt", "hola")
        self.write("v03.txt", "hola")

        rows, _, _ = self.run_command()

        statuses = {row["video_id"]: row["status"] for row in rows}
        self.assertEqual(statuses, {"v01": "no_transcript", "v02": "missing_reference", "v03": "project_not_found"})
        self.assertTrue(all(row["wer"] == "" for row in rows))

    def test_output_has_ids_and_numbers_only(self):
        project = self.project(words((SECRET, 0.0), ("dos", 0.4)), "v2")
        self.manifest(("v01", project.pk))
        self.write("v01.txt", f"{SECRET} uno")
        self.write("v01.anchors.csv", f"time_seconds,text\n0.0,{SECRET} dos\n")

        rows, summary, raw = self.run_command()

        self.assertNotIn(SECRET, raw)
        self.assertNotIn(SECRET, summary)
        self.assertEqual(list(rows[0]), [
            "video_id", "project_id", "pipeline_version", "status", "reference_words",
            "hypothesis_words", "substitutions", "deletions", "insertions", "wer",
            "anchors_total", "anchors_matched", "anchors_unmatched",
            "timestamp_error_median_ms", "timestamp_error_p90_ms",
        ])

    def test_output_file_and_summary_on_stdout(self):
        project = self.project(words(("hola", 0.0)), "v2")
        self.manifest(("v01", project.pk))
        self.write("v01.txt", "hola")
        output = self.dir / "out.csv"

        _, _, stdout = self.run_command(output=str(output))

        self.assertIn("[v2] WER 0.00%", stdout)
        self.assertEqual(list(csv.DictReader(output.open(encoding="utf-8")))[0]["wer"], "0.0000")

    def test_invalid_inputs_are_rejected_without_echoing_them(self):
        some_uuid = "00000000-0000-0000-0000-000000000000"
        cases = {
            f"video_id,project_id\n../etc,{some_uuid}\n": "invalid video_id",
            "video_id,project_id\nv01,not-a-uuid\n": "not a UUID",
            "proyecto,video\nv01,x\n": "header",
        }
        for content, message in cases.items():
            with self.subTest(message=message):
                self.write("manifest.csv", content)
                with self.assertRaisesMessage(CommandError, message):
                    self.run_command()

    def test_bad_anchor_time_is_rejected(self):
        project = self.project(words(("hola", 0.0)), "v2")
        self.manifest(("v01", project.pk))
        self.write("v01.txt", "hola")
        self.write("v01.anchors.csv", "time_seconds,text\nabc,hola\n")
        with self.assertRaisesMessage(CommandError, "time_seconds is not a number"):
            self.run_command()

    def test_accents_decision_is_mandatory(self):
        with self.assertRaises(CommandError):
            call_command("evaluate_ai_transcription", references=str(self.dir))
