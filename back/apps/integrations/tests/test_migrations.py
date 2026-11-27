"""SocialProfile's data migration: the Ayrshare keys that existed become profiles, and the old column stays."""
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.integrations.models import SocialProfile
from apps.users.models import Workspace


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor.loader.project_state(target).apps


class SocialProfileMigrationTests(TransactionTestCase):
    migrate_from = [("integrations", "0001_perfiles_de_redes")]
    migrate_to = [("integrations", "0002_perfiles_desde_ayrshare_profile_key")]

    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def test_existing_keys_become_ayrshare_profiles(self):
        old_apps = _migrate(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")
        OldWorkspace = old_apps.get_model("users", "Workspace")
        owner = OldUser.objects.create(username="duena", email="duena@test.com", password="x")
        with_key = OldWorkspace.objects.create(name="Con perfil", owner=owner, ayrshare_profile_key=" KEY-1 ")
        empty = OldWorkspace.objects.create(name="Vacío", owner=owner, ayrshare_profile_key="  ")
        without = OldWorkspace.objects.create(name="Sin perfil", owner=owner, ayrshare_profile_key=None)

        _migrate(self.migrate_to)
        call_command("migrate", verbosity=0)

        profiles = list(SocialProfile.objects.values_list("workspace_id", "provider", "provider_ref"))
        self.assertEqual(profiles, [(with_key.pk, "ayrshare", "KEY-1")])
        # The column keeps its value: a worker of the previous release may still read it.
        self.assertEqual(Workspace.objects.get(pk=with_key.pk).ayrshare_profile_key, " KEY-1 ")
        self.assertFalse(SocialProfile.objects.filter(workspace_id__in=[empty.pk, without.pk]).exists())
