from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PipelineStateMigrationTests(TransactionTestCase):
    migrate_from = [("videos", "0001_initial")]
    migrate_to = [("videos", "0002_pipeline_state")]

    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def test_existing_rows_get_safe_pipeline_defaults(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Workspace = old_apps.get_model("users", "Workspace")
        VideoProject = old_apps.get_model("videos", "VideoProject")
        user_model = old_apps.get_model(*get_user_model()._meta.label_lower.split("."))
        owner = user_model.objects.create(username="legacy", email="legacy@test.com")
        workspace = Workspace.objects.create(name="Legacy", owner_id=owner.pk)
        project = VideoProject.objects.create(
            workspace_id=workspace.pk, title="Legacy", metadata={"max_clips": 3}
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        migrated = new_apps.get_model("videos", "VideoProject").objects.get(pk=project.pk)

        self.assertEqual(migrated.pipeline_stage, "uploaded")
        self.assertEqual(migrated.pipeline_stage_status, {})
        self.assertEqual(migrated.pipeline_attempts, 0)
        self.assertEqual(migrated.pipeline_error_code, "")
        self.assertIsNone(migrated.pipeline_error_at)
        self.assertEqual(migrated.metadata, {"max_clips": 3})
