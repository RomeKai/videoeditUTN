from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AIUsageRecordMigrationTests(TransactionTestCase):
    migrate_from = [("videos", "0002_pipeline_state")]
    migrate_to = [("videos", "0003_ai_usage_record")]

    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def test_applies_on_a_database_with_existing_projects_without_touching_them(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Workspace = old_apps.get_model("users", "Workspace")
        VideoProject = old_apps.get_model("videos", "VideoProject")
        user_model = old_apps.get_model(*get_user_model()._meta.label_lower.split("."))
        owner = user_model.objects.create(username="legacy-usage", email="legacy-usage@test.com")
        workspace = Workspace.objects.create(name="Legacy", owner_id=owner.pk)
        project = VideoProject.objects.create(
            workspace_id=workspace.pk, title="Legacy", metadata={"max_clips": 3}
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps

        migrated = new_apps.get_model("videos", "VideoProject").objects.get(pk=project.pk)
        usage_model = new_apps.get_model("videos", "AIUsageRecord")
        self.assertEqual(migrated.metadata, {"max_clips": 3})
        self.assertEqual(usage_model.objects.count(), 0)

        usage_model.objects.create(
            project_id=project.pk, stage="selection", pipeline_version="v2", attempt=0,
            role="primary", provider="gemini", model="m", success=True, latency_seconds=0.1,
        )
        self.assertEqual(usage_model.objects.filter(project_id=project.pk).count(), 1)

    def test_makemigrations_is_clean(self):
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
