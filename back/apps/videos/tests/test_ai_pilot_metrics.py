import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import models
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from apps.users.models import Workspace
from apps.videos.management.commands import export_ai_pilot_metrics as command
from apps.videos.models import AIUsageRecord, VideoProject

User = get_user_model()
COMMAND = "export_ai_pilot_metrics"
UTC = dt_timezone.utc
EPOCH = "2000-01-01"


def make_project(label="p", metadata=None, **extra):
    owner = User.objects.create_user(
        username=f"u-{label}", email=f"{label}@test.com", password="pw"
    )
    workspace = Workspace.objects.create(name=f"WS {label}", owner=owner)
    return VideoProject.objects.create(
        workspace=workspace,
        uploaded_by=owner,
        title=extra.pop("title", f"Title {label}"),
        metadata=metadata if metadata is not None else {},
        **extra,
    )


def make_record(project, **overrides):
    fields = dict(
        project=project,
        stage=AIUsageRecord.Stage.SELECTION,
        pipeline_version=AIUsageRecord.PipelineVersion.V2,
        attempt=1,
        role=AIUsageRecord.Role.PRIMARY,
        provider="gemini",
        model="gemini-3.8-flash",
        success=True,
        latency_seconds=1.5,
        cached=False,
        estimated_cost_usd=Decimal("0.01"),
        pricing_version="2026-10-01",
    )
    fields.update(overrides)
    return AIUsageRecord.objects.create(**fields)


def set_created(obj, when):
    type(obj).objects.filter(pk=obj.pk).update(created_at=when)


def run(*args):
    out = io.StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


def run_csv(*args):
    return list(csv.DictReader(io.StringIO(run(*args))))


def project_rows(*extra):
    return run_csv(f"--since={EPOCH}", "--level=project", *extra)


class ArgumentValidationTests(TestCase):
    def test_since_is_required(self):
        with self.assertRaises(CommandError):
            call_command(COMMAND, stdout=io.StringIO())

    def test_invalid_since_raises_command_error(self):
        for bad in ("2026-13-01", "yesterday", "2026/10/01", "20261001", ""):
            with self.subTest(bad=bad), self.assertRaises(CommandError):
                call_command(COMMAND, f"--since={bad}", stdout=io.StringIO())

    def test_invalid_until_raises_command_error(self):
        with self.assertRaises(CommandError):
            call_command(COMMAND, f"--since={EPOCH}", "--until=nope", stdout=io.StringIO())

    def test_until_must_be_after_since(self):
        with self.assertRaises(CommandError):
            call_command(
                COMMAND, "--since=2026-10-02", "--until=2026-10-02", stdout=io.StringIO()
            )

    def test_unknown_level_or_format_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command(COMMAND, f"--since={EPOCH}", "--level=bogus", stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command(COMMAND, f"--since={EPOCH}", "--format=xml", stdout=io.StringIO())


class WindowBoundaryTests(TestCase):
    """The window is [since 00:00 UTC, until 00:00 UTC): --until is EXCLUSIVE."""

    def test_record_level_excludes_the_minute_before_since(self):
        project = make_project()
        before = make_record(project, attempt=1)
        at_start = make_record(project, attempt=2)
        set_created(before, datetime(2026, 9, 30, 23, 59, 0, tzinfo=UTC))
        set_created(at_start, datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC))

        rows = run_csv("--since=2026-10-01", "--level=record")

        self.assertEqual([r["attempt"] for r in rows], ["2"])

    def test_until_is_exclusive(self):
        project = make_project()
        inside = make_record(project, attempt=1)
        on_until = make_record(project, attempt=2)
        set_created(inside, datetime(2026, 10, 1, 23, 59, 59, tzinfo=UTC))
        set_created(on_until, datetime(2026, 10, 2, 0, 0, 0, tzinfo=UTC))

        rows = run_csv("--since=2026-10-01", "--until=2026-10-02", "--level=record")

        self.assertEqual([r["attempt"] for r in rows], ["1"])

    def test_project_level_window_uses_project_creation(self):
        old = make_project("old")
        fresh = make_project("fresh")
        set_created(old, datetime(2026, 9, 30, 23, 59, 0, tzinfo=UTC))
        set_created(fresh, datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC))

        rows = run_csv("--since=2026-10-01", "--level=project")

        self.assertEqual([r["project_id"] for r in rows], [str(fresh.pk)])

    def test_project_aggregates_records_outside_the_window(self):
        project = make_project()
        record = make_record(project)
        set_created(project, datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
        set_created(record, datetime(2026, 10, 5, 12, 0, tzinfo=UTC))

        rows = run_csv("--since=2026-10-01", "--until=2026-10-02", "--level=project")

        self.assertEqual(rows[0]["records_count"], "1")


class RecordLevelTests(TestCase):
    def test_exports_whitelisted_columns_only(self):
        make_record(make_project())
        out = run(f"--since={EPOCH}", "--level=record")
        header = out.splitlines()[0].split(",")
        self.assertEqual(tuple(header), command.EXPORT_COLUMNS)

    def test_row_values(self):
        project = make_project()
        make_record(
            project,
            stage=AIUsageRecord.Stage.TRANSCRIPTION,
            provider="groq",
            model="whisper-large-v3-turbo",
            resolved_model="whisper-large-v3-turbo",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cache_read_tokens=4,
            audio_seconds=12.5,
            success=False,
            error_code="AITimeoutError",
            estimated_cost_usd=None,
        )

        row = run_csv(f"--since={EPOCH}", "--level=record")[0]

        self.assertEqual(row["provider"], "groq")
        self.assertEqual(row["stage"], "transcription")
        self.assertEqual(row["role"], "primary")
        self.assertEqual(row["pipeline_version"], "v2")
        self.assertEqual(row["success"], "False")
        self.assertEqual(row["error_code"], "AITimeoutError")
        self.assertEqual(row["total_tokens"], "30")
        self.assertEqual(row["cache_read_tokens"], "4")
        self.assertEqual(row["audio_seconds"], "12.5")
        self.assertEqual(row["estimated_cost_usd"], "")
        self.assertEqual(row["project_id"], str(project.pk))
        self.assertTrue(row["created_at"].endswith("+00:00"))

    def test_empty_window_csv_is_header_only_and_exits_zero(self):
        out = run(f"--since={EPOCH}", "--level=record")
        self.assertEqual(out.strip(), ",".join(command.EXPORT_COLUMNS))

    def test_empty_window_jsonl_is_empty(self):
        self.assertEqual(run(f"--since={EPOCH}", "--level=record", "--format=jsonl"), "")

    def test_jsonl_is_typed_and_decimal_is_a_string(self):
        make_record(make_project(), estimated_cost_usd=Decimal("0.000001"))

        out = run(f"--since={EPOCH}", "--level=record", "--format=jsonl")
        lines = out.strip().splitlines()
        payload = json.loads(lines[0])

        self.assertEqual(len(lines), 1)
        self.assertIs(payload["success"], True)
        self.assertEqual(payload["attempt"], 1)
        self.assertIsInstance(payload["estimated_cost_usd"], str)
        self.assertEqual(Decimal(payload["estimated_cost_usd"]), Decimal("0.000001"))
        self.assertIsNone(payload["error_code"])

    def test_decimal_is_never_exported_in_scientific_notation(self):
        make_record(make_project(), estimated_cost_usd=Decimal("0.000000001"))

        csv_row = run_csv(f"--since={EPOCH}", "--level=record")[0]
        json_out = run(f"--since={EPOCH}", "--level=record", "--format=jsonl")

        for text in (csv_row["estimated_cost_usd"], json.loads(json_out)["estimated_cost_usd"]):
            self.assertNotIn("e", text.lower())
            self.assertEqual(Decimal(text), Decimal("0.000000001"))


class ProjectLevelTests(TestCase):
    def test_project_without_records_appears_with_zero_count(self):
        project = make_project(metadata={"duration": 900.0})

        rows = project_rows()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["project_id"], str(project.pk))
        self.assertEqual(row["records_count"], "0")
        self.assertEqual(row["failed_records"], "0")
        self.assertEqual(row["cost_complete"], "False")
        for column in (
            "pipeline_version", "total_tokens", "total_cost_usd", "cost_per_30min_usd",
            "transcription_latency_s", "selection_latency_s",
        ):
            self.assertEqual(row[column], "", column)
        self.assertEqual(row["fallback_used"], "False")
        self.assertEqual(row["cached_any"], "False")

    def test_does_not_break_when_mixed_with_projects_that_have_records(self):
        make_record(make_project("with"))
        make_project("without")

        counts = sorted(r["records_count"] for r in project_rows())

        self.assertEqual(counts, ["0", "1"])

    def test_cost_per_30min_uses_source_duration_not_audio_seconds(self):
        project = make_project(metadata={"duration": 900.0})
        make_record(
            project,
            stage=AIUsageRecord.Stage.TRANSCRIPTION,
            estimated_cost_usd=Decimal("0.02"),
            audio_seconds=5000.0,
        )

        row = project_rows()[0]

        self.assertEqual(Decimal(row["cost_per_30min_usd"]), Decimal("0.04"))
        self.assertEqual(Decimal(row["total_cost_usd"]), Decimal("0.02"))
        self.assertEqual(float(row["source_duration_s"]), 900.0)
        self.assertEqual(row["cost_complete"], "True")

    def test_cost_per_30min_is_empty_without_a_usable_duration(self):
        cases = {
            "absent": {},
            "zero": {"duration": 0},
            "null": {"duration": None},
            "text": {"duration": "abc"},
            "negative": {"duration": -5},
        }
        for label, metadata in cases.items():
            with self.subTest(label):
                VideoProject.objects.all().delete()
                make_record(make_project(f"d-{label}", metadata=metadata))
                row = project_rows()[0]
                self.assertEqual(row["cost_per_30min_usd"], "")
                self.assertEqual(row["total_cost_usd"], "0.010000000")

    def test_absurdly_long_numeric_strings_do_not_abort_the_export(self):
        # A 400-digit string would overflow the float cast in Postgres and abort
        # the whole export: it must just yield no duration.
        cases = {
            "huge_integer": {"duration": "9" * 400},
            "huge_decimals": {"duration": "1." + "0" * 400},
            "ten_integer_digits": {"duration": "1234567890"},
        }
        for label, metadata in cases.items():
            with self.subTest(label):
                VideoProject.objects.all().delete()
                make_record(make_project(f"big-{label}", metadata=metadata))
                row = project_rows()[0]
                self.assertEqual(row["cost_per_30min_usd"], "")
                self.assertEqual(row["source_duration_s"], "")

    def test_numeric_string_duration_is_accepted(self):
        make_record(make_project(metadata={"duration": "1800"}), estimated_cost_usd=Decimal("0.03"))
        self.assertEqual(Decimal(project_rows()[0]["cost_per_30min_usd"]), Decimal("0.03"))

    def test_null_cost_makes_cost_incomplete(self):
        project = make_project(metadata={"duration": 900})
        make_record(project, attempt=1, estimated_cost_usd=Decimal("0.01"))
        make_record(project, attempt=2, estimated_cost_usd=None)

        row = project_rows()[0]

        self.assertEqual(row["cost_complete"], "False")
        self.assertEqual(row["records_count"], "2")

    def test_all_costs_known_is_complete(self):
        project = make_project()
        make_record(project, attempt=1)
        make_record(project, attempt=2)
        self.assertEqual(project_rows()[0]["cost_complete"], "True")

    def test_fallback_role_sets_fallback_used(self):
        project = make_project()
        make_record(project, role=AIUsageRecord.Role.PRIMARY, success=False, error_code="AIServerError")
        make_record(project, role=AIUsageRecord.Role.FALLBACK)

        row = project_rows()[0]

        self.assertEqual(row["fallback_used"], "True")
        self.assertEqual(row["failed_records"], "1")

    def test_mixed_pipeline_versions(self):
        project = make_project()
        make_record(project, stage=AIUsageRecord.Stage.TRANSCRIPTION, pipeline_version="legacy")
        make_record(project, stage=AIUsageRecord.Stage.SELECTION, pipeline_version="v2")
        self.assertEqual(project_rows()[0]["pipeline_version"], "mixed")

    def test_single_pipeline_version_is_reported(self):
        project = make_project()
        make_record(project, attempt=1, pipeline_version="legacy")
        make_record(project, attempt=2, pipeline_version="legacy")
        self.assertEqual(project_rows()[0]["pipeline_version"], "legacy")

    def test_aggregates_tokens_latency_cache_and_state(self):
        project = make_project(
            pipeline_attempts=3,
            status=VideoProject.Status.AWAITING_APPROVAL,
            pipeline_stage=VideoProject.PipelineStage.CLIPS_SELECTED,
        )
        make_record(project, stage="transcription", total_tokens=0, latency_seconds=10.0)
        make_record(project, stage="selection", attempt=1, total_tokens=100, latency_seconds=2.0)
        make_record(project, stage="selection", attempt=2, total_tokens=50, latency_seconds=3.0, cached=True)

        row = project_rows()[0]

        self.assertEqual(row["total_tokens"], "150")
        self.assertEqual(float(row["transcription_latency_s"]), 10.0)
        self.assertEqual(float(row["selection_latency_s"]), 5.0)
        self.assertEqual(row["cached_any"], "True")
        self.assertEqual(row["attempts"], "3")
        self.assertEqual(row["final_stage"], "clips_selected")
        self.assertEqual(row["status"], "awaiting_approval")
        self.assertEqual(row["records_count"], "3")

    def test_empty_window_is_header_only(self):
        out = run("--since=2999-01-01", "--level=project")
        self.assertEqual(out.strip(), ",".join(command.PROJECT_COLUMNS))

    def test_default_level_is_project(self):
        make_project()
        out = run(f"--since={EPOCH}")
        self.assertEqual(out.splitlines()[0], ",".join(command.PROJECT_COLUMNS))


class PrivacyTests(TestCase):
    def test_export_columns_are_a_subset_of_concrete_record_fields(self):
        concrete = {f.attname for f in AIUsageRecord._meta.concrete_fields}
        self.assertTrue(set(command.EXPORT_COLUMNS) <= concrete)

    def test_record_model_has_no_free_text_columns(self):
        for field in AIUsageRecord._meta.get_fields():
            self.assertNotIsInstance(field, (models.TextField, models.JSONField), field.name)

    def test_forbidden_columns_are_never_exported(self):
        forbidden = {"title", "metadata", "transcript_data", "ai_rationale_log", "email", "user_id"}
        self.assertFalse(forbidden & set(command.EXPORT_COLUMNS))
        self.assertFalse(forbidden & set(command.PROJECT_COLUMNS))

    def test_no_secret_reaches_the_output_in_any_level_or_format(self):
        user = User.objects.create_user(
            username="leak-user", email="SECRET_EMAIL_X@test.com", password="pw"
        )
        workspace = Workspace.objects.create(name="WS leak", owner=user)
        project = VideoProject.objects.create(
            workspace=workspace,
            uploaded_by=user,
            title="SECRET_TITLE_X",
            metadata={"duration": 900.0, "full_text": "SECRET_TRANSCRIPT_X"},
            transcript_data={"text": "SECRET_TRANSCRIPT_X"},
            ai_rationale_log={"why": "SECRET_REASON_X"},
        )
        make_record(project, estimated_cost_usd=Decimal("0.02"))
        make_record(project, attempt=2, role="fallback", success=False, error_code="AIServerError")

        for level in ("record", "project"):
            for fmt in ("csv", "jsonl"):
                with self.subTest(level=level, format=fmt):
                    out = run(f"--since={EPOCH}", f"--level={level}", f"--format={fmt}")
                    self.assertTrue(out)
                    self.assertNotIn(b"SECRET_", out.encode("utf-8"))

    def test_no_secret_reaches_an_output_file(self):
        project = make_project(title="SECRET_TITLE_X", metadata={"full_text": "SECRET_TRANSCRIPT_X"})
        make_record(project)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            run(f"--since={EPOCH}", "--level=project", f"--output={path}")
            with open(path, "rb") as handle:
                self.assertNotIn(b"SECRET_", handle.read())


class CsvInjectionTests(TestCase):
    def test_sanitizer_prefixes_dangerous_leading_characters(self):
        for value in ("=SUM(A1)", "+1", "-1", "@cmd", "\tx", "\rx"):
            with self.subTest(value=value):
                self.assertEqual(command.sanitize_csv_text(value), "'" + value)

    def test_sanitizer_keeps_safe_values(self):
        for value in ("gemini", "", "a=b", "v2"):
            self.assertEqual(command.sanitize_csv_text(value), value)

    def test_dangerous_model_is_sanitized_in_csv_but_raw_in_jsonl(self):
        make_record(make_project(), model="=HYPERLINK(1)", error_code="@evil")

        csv_row = run_csv(f"--since={EPOCH}", "--level=record")[0]
        json_row = json.loads(run(f"--since={EPOCH}", "--level=record", "--format=jsonl"))

        self.assertEqual(csv_row["model"], "'=HYPERLINK(1)")
        self.assertEqual(csv_row["error_code"], "'@evil")
        self.assertEqual(json_row["model"], "=HYPERLINK(1)")


class OutputTests(TestCase):
    def test_output_writes_file_and_leaves_stdout_empty(self):
        make_record(make_project())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "metrics.csv")
            stdout = run(f"--since={EPOCH}", "--level=record", f"--output={path}")
            with open(path, encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(stdout, "")
        self.assertEqual(len(rows), 1)

    def test_unwritable_output_raises_command_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing-dir", "out.csv")
            with self.assertRaises(CommandError):
                run(f"--since={EPOCH}", f"--output={path}")


class MemoryBoundTests(TestCase):
    def test_record_level_streams_with_iterator(self):
        make_record(make_project())
        with patch.object(QuerySet, "iterator", autospec=True, side_effect=QuerySet.iterator) as spy:
            run(f"--since={EPOCH}", "--level=record")
        self.assertTrue(spy.called)
        self.assertEqual(spy.call_args.kwargs.get("chunk_size"), 2000)

    def test_project_level_streams_with_iterator(self):
        make_project()
        with patch.object(QuerySet, "iterator", autospec=True, side_effect=QuerySet.iterator) as spy:
            run(f"--since={EPOCH}", "--level=project")
        self.assertEqual(spy.call_args.kwargs.get("chunk_size"), 2000)

    def test_query_count_does_not_grow_with_rows(self):
        project = make_project()
        for attempt in range(1, 6):
            make_record(project, attempt=attempt)
        with self.assertNumQueries(1):
            run(f"--since={EPOCH}", "--level=record")
        with self.assertNumQueries(1):
            run(f"--since={EPOCH}", "--level=project")

    def test_ten_thousand_records_export(self):
        project = make_project(metadata={"duration": 1800})
        AIUsageRecord.objects.bulk_create(
            AIUsageRecord(
                project=project,
                stage="selection",
                pipeline_version="v2",
                attempt=attempt,
                role="primary",
                provider="gemini",
                model="gemini-3.8-flash",
                success=True,
                latency_seconds=0.5,
                estimated_cost_usd=Decimal("0.000001"),
            )
            for attempt in range(1, 10_001)
        )

        record_rows = run_csv(f"--since={EPOCH}", "--level=record")
        project_row = project_rows()[0]

        self.assertEqual(len(record_rows), 10_000)
        self.assertEqual(project_row["records_count"], "10000")
        self.assertEqual(Decimal(project_row["total_cost_usd"]), Decimal("0.01"))
