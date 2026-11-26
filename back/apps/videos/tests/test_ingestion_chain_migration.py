"""
videos.0017: every project failed at deploy time without a chain gets one,
so a tokenless message still in flight never reopens it for free.
"""

import re

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("videos", "0016_layouts_podcast_trending_y_opciones")]
AFTER = [("videos", "0017_cadena_de_ingesta_en_proyectos_fallados")]


class IngestionChainMigrationTests(TransactionTestCase):
    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def test_failed_projects_without_a_chain_get_one_and_nothing_else_changes(self):
        old_apps = self.migrate(BEFORE)
        Workspace = old_apps.get_model("users", "Workspace")
        VideoProject = old_apps.get_model("videos", "VideoProject")
        user_model = old_apps.get_model(*get_user_model()._meta.label_lower.split("."))
        owner = user_model.objects.create(username="cadena", email="cadena@test.com", password="!")
        workspace = Workspace.objects.create(name="Cadena", owner_id=owner.pk)

        def project(stage, metadata):
            return VideoProject.objects.create(
                workspace_id=workspace.pk, title=stage, pipeline_stage=stage, metadata=metadata,
            ).pk

        tokenless = project("failed", {"reservation_id": "r1", "max_clips": 3})
        with_chain = project("failed", {"ingestion_chain": "a-chain-of-its-own"})
        empty = project("failed", {})
        not_a_dict = project("failed", ["legacy", "row"])
        running = project("transcribed", {"reservation_id": "r2"})

        new_apps = self.migrate(AFTER)
        migrated = {row.pk: row.metadata for row in new_apps.get_model("videos", "VideoProject").objects.all()}

        self.assertRegex(migrated[tokenless].pop("ingestion_chain"), re.compile(r"^[0-9a-f]{32}$"))
        self.assertEqual(migrated[tokenless], {"reservation_id": "r1", "max_clips": 3})
        self.assertRegex(migrated[empty]["ingestion_chain"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(migrated[empty]["ingestion_chain"], migrated[with_chain]["ingestion_chain"])
        self.assertEqual(migrated[with_chain], {"ingestion_chain": "a-chain-of-its-own"})
        self.assertEqual(migrated[not_a_dict], ["legacy", "row"])
        self.assertEqual(migrated[running], {"reservation_id": "r2"})

        # Reversing it does nothing.
        back_apps = self.migrate(BEFORE)
        reverted = back_apps.get_model("videos", "VideoProject").objects.get(pk=empty).metadata
        self.assertEqual(reverted, migrated[empty])

    def test_a_tokenless_message_no_longer_reopens_a_failed_project(self):
        """What the token is for: _run_ingestion only reopens a failed project for its stored chain."""
        from apps.videos.tasks.ingestion import _chain_current

        old_apps = self.migrate(BEFORE)
        Workspace = old_apps.get_model("users", "Workspace")
        VideoProject = old_apps.get_model("videos", "VideoProject")
        user_model = old_apps.get_model(*get_user_model()._meta.label_lower.split("."))
        owner = user_model.objects.create(username="tarde", email="tarde@test.com", password="!")
        workspace = Workspace.objects.create(name="Tarde", owner_id=owner.pk)
        pk = VideoProject.objects.create(
            workspace_id=workspace.pk, title="Fallado", pipeline_stage="failed", metadata={},
        ).pk
        self.assertTrue(_chain_current({}, None))  # before: a tokenless copy is the current chain

        new_apps = self.migrate(AFTER)
        metadata = new_apps.get_model("videos", "VideoProject").objects.get(pk=pk).metadata

        self.assertFalse(_chain_current(metadata, None))
