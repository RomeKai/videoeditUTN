"""AICORE-18 (#70): the AI cost history outlives the project it belongs to."""

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from apps.users.models import Workspace
from apps.videos.models import AIUsageRecord, VideoProject

User = get_user_model()


class UsageHistoryTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="hist", email="hist@test.com", password="pw")
        workspace = Workspace.objects.create(name="Hist", owner=user)
        self.project = VideoProject.objects.create(workspace=workspace, uploaded_by=user, title="P")
        self.record = AIUsageRecord.objects.create(
            project=self.project, stage="selection", pipeline_version="v2", attempt=1,
            role="primary", provider="gemini", model="gemini-3.8-flash", success=True,
            latency_seconds=0.5, total_tokens=100,
        )

    def test_deleting_a_project_keeps_its_usage_rows_and_their_project_id(self):
        project_id = self.project.pk

        self.project.delete()
        connection.check_constraints()  # no deferred FK left to fail at commit

        row = AIUsageRecord.objects.get(pk=self.record.pk)
        self.assertEqual(row.project_id, project_id)
        with self.assertRaises(VideoProject.DoesNotExist):
            row.project  # noqa: B018 - the dangling relation is expected

    def test_the_pilot_export_still_lists_the_rows_of_a_deleted_project(self):
        project_id = str(self.project.pk)
        self.project.delete()

        out = StringIO()
        call_command(
            "export_ai_pilot_metrics", "--since=2000-01-01", "--level=record", "--format=jsonl",
            stdout=out,
        )

        rows = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([row["project_id"] for row in rows], [project_id])

    def test_the_table_has_no_foreign_key_constraint_on_the_project(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, AIUsageRecord._meta.db_table
            )
        foreign_keys = [c for c in constraints.values() if c["foreign_key"]]
        self.assertEqual(foreign_keys, [])
