"""SocialProfile's data migration: the Ayrshare keys that existed become profiles, and the old column stays."""
from datetime import timedelta

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

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



class NetworksPerWorkspaceMigrationTests(TransactionTestCase):
    """Review of #187: 0003 keeps one row per provider profile and never locks users_workspace."""

    before = [("integrations", "0002_perfiles_desde_ayrshare_profile_key")]
    after = [("integrations", "0003_redes_por_espacio")]

    def tearDown(self):
        call_command("migrate", verbosity=0)

    def test_a_shared_key_stays_only_in_the_oldest_row(self):
        old_apps = _migrate(self.before)
        OldUser = old_apps.get_model("users", "User")
        OldWorkspace = old_apps.get_model("users", "Workspace")
        OldProfile = old_apps.get_model("integrations", "SocialProfile")
        owner = OldUser.objects.create(username="compartida", email="compartida@test.com", password="x")
        first, second, alone = (OldWorkspace.objects.create(name=name, owner=owner) for name in ("Primero", "Segundo", "Solo"))
        OldProfile.objects.create(workspace_id=first.pk, provider="ayrshare", provider_ref="SAME", provider_id="r1")
        OldProfile.objects.create(workspace_id=second.pk, provider="ayrshare", provider_ref="SAME", provider_id="r1")
        OldProfile.objects.create(workspace_id=alone.pk, provider="ayrshare", provider_ref="OWN")

        _migrate(self.after)

        refs = dict(SocialProfile.objects.values_list("workspace_id", "provider_ref"))
        # Each one could publish to the other's accounts: the newer row connects again.
        self.assertEqual(refs, {first.pk: "SAME", second.pk: "", alone.pk: "OWN"})

    def shared_key(self, workspaces_at, profiles_at):
        """
        Two workspaces with one key, their rows made in the opposite order (as 0002 may: it reads
        the workspaces in no given order). Returns (the workspace ids by id, the refs after 0003).
        """
        old_apps = _migrate(self.before)
        OldUser = old_apps.get_model("users", "User")
        OldWorkspace = old_apps.get_model("users", "Workspace")
        OldProfile = old_apps.get_model("integrations", "SocialProfile")
        owner = OldUser.objects.create(username="mismo", email="mismo@test.com", password="x")
        low, high = sorted(OldWorkspace.objects.create(name=name, owner=owner).pk for name in ("Uno", "Dos"))
        for pk, at in zip((low, high), workspaces_at):
            OldWorkspace.objects.filter(pk=pk).update(created_at=at)
        for pk, at in zip((high, low), profiles_at):  # the later id gets the lower row pk
            row = OldProfile.objects.create(workspace_id=pk, provider="ayrshare", provider_ref="SAME")
            OldProfile.objects.filter(pk=row.pk).update(created_at=at)

        _migrate(self.after)

        return (low, high), dict(SocialProfile.objects.values_list("workspace_id", "provider_ref"))

    def test_workspaces_of_the_same_instant_keep_the_key_in_the_one_with_the_lower_id(self):
        """Ensayo 5: rows 0002 made in one go share an instant: the row's own order changed between runs."""
        instant = timezone.now() - timedelta(days=30)
        (low, high), refs = self.shared_key(workspaces_at=(instant, instant), profiles_at=(instant, instant))
        self.assertEqual(refs, {low: "SAME", high: ""})

    def test_the_oldest_workspace_keeps_the_key_whatever_the_order_of_the_rows(self):
        old = timezone.now() - timedelta(days=30)
        (low, high), refs = self.shared_key(
            workspaces_at=(old + timedelta(days=1), old), profiles_at=(old + timedelta(seconds=1), old),
        )
        # `high` is the older workspace, though its row is the newer one.
        self.assertEqual(refs, {low: "", high: "SAME"})

    def test_the_workspace_foreign_key_is_not_dropped_and_the_lock_wait_is_short(self):
        if connection.vendor != "postgresql":
            self.skipTest("the statements are PostgreSQL's")
        _migrate(self.before)
        statements = []

        def keep(execute, sql, params, many, context):
            statements.append(str(sql))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(keep):
            _migrate(self.after)
        text = "\n".join(statements).upper()
        self.assertIn("SET LOCAL LOCK_TIMEOUT = '5S'", text)
        # Before the first statement that takes a lock (the AddFields of the columns).
        upper = [sql.upper() for sql in statements]
        first_alter = next(index for index, sql in enumerate(upper) if sql.startswith("ALTER TABLE"))
        self.assertLess(next(index for index, sql in enumerate(upper) if "LOCK_TIMEOUT" in sql), first_alter)
        self.assertIn('ALTER COLUMN WORKSPACE_ID DROP NOT NULL', text)
        # Re-creating the foreign key takes ACCESS EXCLUSIVE on users_workspace while old containers serve.
        self.assertNotIn("FOREIGN KEY", text)
        self.assertNotIn("DROP CONSTRAINT", text)

    def test_going_back_restores_not_null_without_the_orphans(self):
        owner = User.objects.create_user(username="atras", email="atras@test.com", password="pw")
        live = Workspace.objects.create(name="Vivo", owner=owner)
        SocialProfile.objects.create(workspace=live, provider="ayrshare", provider_ref="LIVE")
        SocialProfile.objects.create(workspace=None, provider="ayrshare", provider_ref="GONE")

        _migrate(self.before)

        with connection.cursor() as cursor:
            cursor.execute("SELECT workspace_id FROM integrations_socialprofile")
            self.assertEqual([row[0] for row in cursor.fetchall()], [live.pk])
            if connection.vendor == "postgresql":
                cursor.execute(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'integrations_socialprofile' AND column_name = 'workspace_id'"
                )
                self.assertEqual(cursor.fetchone()[0], "NO")
