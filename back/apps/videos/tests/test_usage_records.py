from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.test import TestCase

from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoProject
from apps.videos.services.ai.contracts import ProviderUsage
from apps.videos.services.ai.errors import AIServerError, AITimeoutError
from apps.videos.services.ai.pricing import PRICING_VERSION
from apps.videos.services.ai.usage_recorder import (
    failure_usage,
    record_usages,
    usages_for_failure,
)

User = get_user_model()
SECRET = "SECRET_TRANSCRIPT_X"


def make_project(title="Usage"):
    owner = User.objects.create_user(
        username=f"u-{title}", email=f"{title}@test.com", password="pw"
    )
    workspace = Workspace.objects.create(name=f"WS {title}", owner=owner)
    return VideoProject.objects.create(workspace=workspace, uploaded_by=owner, title=title)


def usage_fields(**overrides):
    fields = dict(
        stage=AIUsageRecord.Stage.SELECTION,
        pipeline_version=AIUsageRecord.PipelineVersion.V2,
        attempt=0,
        role=AIUsageRecord.Role.PRIMARY,
        provider="gemini",
        model="gemini-3.8-flash",
        success=True,
        latency_seconds=1.5,
        cached=False,
    )
    fields.update(overrides)
    return fields


class AIUsageRecordModelTests(TestCase):
    def test_has_no_free_text_columns(self):
        # Privacy by construction: nothing that could hold a title, transcript,
        # prompt or provider message may ever be added to this table unnoticed.
        forbidden = (models.TextField, models.JSONField)
        for field in AIUsageRecord._meta.get_fields():
            self.assertNotIsInstance(field, forbidden, field.name)

    def test_char_columns_are_short_and_bounded(self):
        for field in AIUsageRecord._meta.get_fields():
            if isinstance(field, models.CharField):
                self.assertLessEqual(field.max_length, 64, field.name)

    def test_defaults_for_metered_columns_are_zero(self):
        record = AIUsageRecord.objects.create(project=make_project(), **usage_fields())

        record.refresh_from_db()
        self.assertEqual(
            (
                record.prompt_tokens,
                record.completion_tokens,
                record.total_tokens,
                record.cache_read_tokens,
            ),
            (0, 0, 0, 0),
        )
        self.assertIsNone(record.audio_seconds)
        self.assertIsNone(record.estimated_cost_usd)
        self.assertIsNone(record.pricing_version)
        self.assertIsNone(record.error_code)
        self.assertIsNone(record.resolved_model)
        self.assertIsNotNone(record.created_at)

    def test_sub_micro_dollar_costs_are_not_truncated(self):
        record = AIUsageRecord.objects.create(
            project=make_project(),
            **usage_fields(estimated_cost_usd=Decimal("0.000000375")),
        )

        record.refresh_from_db()
        self.assertEqual(record.estimated_cost_usd, Decimal("0.000000375"))

    def test_unique_per_project_stage_attempt_and_role(self):
        project = make_project()
        AIUsageRecord.objects.create(project=project, **usage_fields())

        with self.assertRaises(IntegrityError), transaction.atomic():
            AIUsageRecord.objects.create(project=project, **usage_fields())

    def test_different_role_attempt_stage_or_project_do_not_collide(self):
        project = make_project()
        AIUsageRecord.objects.create(project=project, **usage_fields())

        AIUsageRecord.objects.create(
            project=project, **usage_fields(role=AIUsageRecord.Role.FALLBACK)
        )
        AIUsageRecord.objects.create(project=project, **usage_fields(attempt=1))
        AIUsageRecord.objects.create(
            project=project, **usage_fields(stage=AIUsageRecord.Stage.TRANSCRIPTION)
        )
        AIUsageRecord.objects.create(project=make_project("other"), **usage_fields())

        self.assertEqual(AIUsageRecord.objects.count(), 5)

    def test_deleting_the_project_deletes_its_records(self):
        project = make_project()
        AIUsageRecord.objects.create(project=project, **usage_fields())

        project.delete()

        self.assertEqual(AIUsageRecord.objects.count(), 0)

    def test_created_at_and_project_are_indexed_for_since_queries(self):
        index_fields = [tuple(index.fields) for index in AIUsageRecord._meta.indexes]

        self.assertIn(("created_at", "project"), index_fields)
        self.assertTrue(AIUsageRecord._meta.get_field("created_at").db_index)
        self.assertTrue(AIUsageRecord._meta.get_field("project").db_index)


def selection_usage(**overrides):
    fields = dict(
        provider="gemini", model="gemini/gemini-3.8-flash", prompt_tokens=1000,
        completion_tokens=100, cache_read_tokens=200, duration_seconds=1.25,
        estimated_cost_usd=0.000000375, pricing_version=PRICING_VERSION,
        resolved_model="gemini-3.8-flash-001",
    )
    fields.update(overrides)
    return ProviderUsage(**fields)


class RecordUsagesTests(TestCase):
    def setUp(self):
        self.project = make_project("rec")

    def record(self, usages, attempt=0, stage="selection", version="v2"):
        return record_usages(self.project.id, stage, version, usages, attempt=attempt)

    def test_writes_every_column_from_the_usage(self):
        self.record([selection_usage()])

        row = AIUsageRecord.objects.get()
        self.assertEqual(
            (row.stage, row.pipeline_version, row.attempt, row.role, row.success),
            ("selection", "v2", 0, "primary", True),
        )
        self.assertEqual((row.provider, row.model), ("gemini", "gemini/gemini-3.8-flash"))
        self.assertEqual(row.resolved_model, "gemini-3.8-flash-001")
        self.assertEqual(
            (row.prompt_tokens, row.completion_tokens, row.total_tokens, row.cache_read_tokens),
            (1000, 100, 1100, 200),
        )
        self.assertEqual(row.latency_seconds, 1.25)
        self.assertEqual(row.estimated_cost_usd, Decimal("0.000000375"))
        self.assertEqual(row.pricing_version, PRICING_VERSION)
        self.assertIsNone(row.error_code)

    def test_a_redelivery_updates_the_row_instead_of_duplicating_it(self):
        self.record([selection_usage(prompt_tokens=10)])
        first = AIUsageRecord.objects.get()

        self.record([selection_usage(prompt_tokens=99)])

        row = AIUsageRecord.objects.get()
        self.assertEqual(row.pk, first.pk)
        self.assertEqual(row.prompt_tokens, 99)
        self.assertEqual(row.created_at, first.created_at)

    def test_primary_and_fallback_attempts_are_separate_rows(self):
        self.record([
            selection_usage(error_code="AIContractValidationError"),
            selection_usage(role="fallback", model="gemini/gemini-2.5-flash"),
        ])

        primary, fallback = AIUsageRecord.objects.order_by("role")[::-1]
        self.assertEqual((primary.role, primary.success, primary.error_code), ("primary", False, "AIContractValidationError"))
        self.assertEqual((fallback.role, fallback.success), ("fallback", True))
        self.assertEqual(primary.prompt_tokens, 1000)

    def test_attempt_defaults_to_the_projects_pipeline_attempts(self):
        VideoProject.objects.filter(pk=self.project.id).update(pipeline_attempts=3)

        record_usages(self.project.id, "selection", "v2", [selection_usage()])

        self.assertEqual(AIUsageRecord.objects.get().attempt, 3)

    def test_unknown_cost_stays_null_and_non_finite_cost_is_not_stored(self):
        self.record([selection_usage(estimated_cost_usd=None)], attempt=0)
        self.record([selection_usage(estimated_cost_usd=float("inf"))], attempt=1)

        self.assertEqual(
            list(AIUsageRecord.objects.order_by("attempt").values_list("estimated_cost_usd", flat=True)),
            [None, None],
        )

    def test_write_failure_is_swallowed_and_logged_with_the_class_name_only(self):
        with patch.object(
            AIUsageRecord.objects, "update_or_create", side_effect=RuntimeError(f"db says {SECRET}")
        ):
            with self.assertLogs("apps.videos.services.ai.usage_recorder", level="WARNING") as logs:
                written = self.record([selection_usage()])

        self.assertEqual(written, 0)
        output = " ".join(record.getMessage() for record in logs.records)
        self.assertIn("metrics.write_failed", output)
        self.assertIn("RuntimeError", output)
        self.assertNotIn(SECRET, output)
        self.assertNotIn("db says", output)
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    def test_failure_of_one_row_does_not_stop_the_others(self):
        real = AIUsageRecord.objects.update_or_create
        calls = []

        def flaky(**kwargs):
            calls.append(kwargs["role"])
            if len(calls) == 1:
                raise RuntimeError("boom")
            return real(**kwargs)

        with patch.object(AIUsageRecord.objects, "update_or_create", side_effect=flaky):
            written = self.record([selection_usage(), selection_usage(role="fallback")])

        self.assertEqual(written, 1)
        self.assertEqual(AIUsageRecord.objects.get().role, "fallback")

    def test_a_database_error_inside_the_write_does_not_poison_the_surrounding_transaction(self):
        with patch.object(
            AIUsageRecord.objects, "update_or_create", side_effect=IntegrityError("constraint")
        ):
            self.record([selection_usage()])

        # TestCase wraps the test in a transaction: it must still be usable.
        self.assertEqual(VideoProject.objects.filter(pk=self.project.id).count(), 1)

    def test_unknown_project_is_logged_not_raised(self):
        import uuid

        written = record_usages(uuid.uuid4(), "selection", "v2", [selection_usage()])

        self.assertEqual(written, 0)

    def test_overlong_strings_are_truncated_not_rejected(self):
        self.record([selection_usage(model="m" * 200, provider="p" * 200, error_code="E" * 200)])

        row = AIUsageRecord.objects.get()
        self.assertEqual((len(row.model), len(row.provider), len(row.error_code)), (64, 64, 64))


class FailureUsageTests(TestCase):
    def test_failure_usage_is_zero_cost_with_the_class_name_only(self):
        usage = failure_usage(AIServerError(f"boom {SECRET}"), "groq", "whisper", 2.5)

        self.assertEqual(usage.error_code, "AIServerError")
        self.assertEqual((usage.total_tokens, usage.estimated_cost_usd), (0, 0.0))
        self.assertEqual(usage.duration_seconds, 2.5)
        self.assertEqual(usage.pricing_version, PRICING_VERSION)
        self.assertNotIn(SECRET, usage.model_dump_json())

    def test_timeouts_leave_the_cost_unknown_not_zero(self):
        usage = failure_usage(AITimeoutError(), "groq", "whisper", 60.0)

        self.assertIsNone(usage.estimated_cost_usd)

    def test_carried_attempts_win_over_a_synthetic_failure_row(self):
        error = AIServerError("down")
        error.usage_attempts = [selection_usage(error_code="AIContractValidationError")]

        usages = usages_for_failure(error, "gemini", "m", 1.0)

        self.assertEqual(usages, error.usage_attempts)

    def test_carried_attempts_without_an_error_code_are_marked_failed(self):
        error = AIServerError("down")
        error.usage_attempts = [selection_usage()]

        (usage,) = usages_for_failure(error, "gemini", "m", 1.0)

        self.assertEqual(usage.error_code, "AIServerError")
        self.assertEqual(usage.prompt_tokens, 1000)

    def test_without_carried_attempts_one_failure_row_is_built(self):
        (usage,) = usages_for_failure(ValueError(SECRET), "gemini", "m", 0.4)

        self.assertEqual(usage.error_code, "ValueError")
        self.assertEqual(usage.duration_seconds, 0.4)
