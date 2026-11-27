"""SocialProfile's data migration: the Ayrshare keys that existed become profiles, and the old column stays."""
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.integrations.models import SocialProfile
from apps.users.models import User, Workspace


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


class SocialProfileRollbackTests(TransactionTestCase):
    """Review of #186: going back keeps the keys of profiles created after 0002 (the old code reads the column)."""

    def tearDown(self):
        call_command("migrate", verbosity=0)

    def test_going_back_fills_the_empty_column(self):
        owner = User.objects.create_user(username="vuelta", email="vuelta@test.com", password="pw")
        later = Workspace.objects.create(name="Creado después", owner=owner)
        kept = Workspace.objects.create(name="Con su key", owner=owner, ayrshare_profile_key="OLD")
        SocialProfile.objects.create(workspace=later, provider="ayrshare", provider_ref="NEW-1")
        SocialProfile.objects.create(workspace=kept, provider="ayrshare", provider_ref="OTHER")

        _migrate([("integrations", "0001_perfiles_de_redes")])

        keys = dict(Workspace.objects.filter(pk__in=[later.pk, kept.pk]).values_list("pk", "ayrshare_profile_key"))
        self.assertEqual(keys, {later.pk: "NEW-1", kept.pk: "OLD"})  # a key already there is never replaced

