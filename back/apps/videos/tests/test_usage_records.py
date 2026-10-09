from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.test import TestCase

from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoProject

User = get_user_model()


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
