"""Data migrations of User: they run on a database with the rows they fix."""
import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import User


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    return executor.loader.project_state(target).apps


class NullPasswordMigrationTests(TransactionTestCase):
    migrate_from = [("users", "0003_perfil_email_pendiente_y_workspace")]
    migrate_to = [("users", "0005_contrasena_obligatoria")]

    def tearDown(self):
        # Leave the schema at the latest migration for the tests that follow.
        call_command("migrate", verbosity=0)

    def test_null_passwords_become_unusable_and_the_account_works(self):
        old_apps = _migrate(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")
        social = OldUser.objects.create(username="social", email="social@test.com", password=None)
        other = OldUser.objects.create(username="social2", email="social2@test.com", password=None)

        _migrate(self.migrate_to)
        # Today's models (and the API below) need the rest of the schema.
        call_command("migrate", verbosity=0)

        social = User.objects.get(pk=social.pk)
        assert social.password and not social.has_usable_password()
        assert social.password != User.objects.get(pk=other.pk).password
        # SimpleJWT hashes the password into every token (CHECK_REVOKE_TOKEN): no more 500.
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(social).access_token}")
        assert api.get("/api/v1/me/").status_code == 200


class CaseInsensitiveEmailMigrationTests(TransactionTestCase):
    migrate_from = [("users", "0005_contrasena_obligatoria")]
    migrate_to = [("users", "0007_email_unico_sin_mayusculas")]

    def tearDown(self):
        call_command("migrate", verbosity=0)

    def test_case_duplicates_stop_the_migration_with_how_many_there_are(self):
        old_apps = _migrate(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")
        OldUser.objects.create(username="ana1", email="ana@test.com", password="x")
        OldUser.objects.create(username="ana2", email="Ana@test.com", password="x")
        OldUser.objects.create(username="ana3", email="ANA@test.com", password="x")
        OldUser.objects.create(username="luis1", email="luis@test.com", password="x")
        OldUser.objects.create(username="luis2", email="Luis@Test.com", password="x")
        # Equal in upper case only: "ſ" is "S" for Postgres's UPPER.
        OldUser.objects.create(username="sol1", email="sol@test.com", password="x")
        OldUser.objects.create(username="sol2", email="ſol@test.com", password="x")

        with pytest.raises(RuntimeError, match=r"^3 emails are used by more than one account if case is ignored \(7 accounts\)"):
            _migrate(self.migrate_to)

        # Merged by hand, the migration goes on.
        OldUser.objects.filter(username__in=["ana2", "ana3", "luis2", "sol2"]).delete()
        _migrate(self.migrate_to)

    def test_domains_are_lowered_and_the_constraint_holds(self):
        old_apps = _migrate(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")
        old = OldUser.objects.create(username="mixto", email="Mixto@Ejemplo.COM", password="x", pending_email="Nuevo@Otro.COM")

        _migrate(self.migrate_to)
        # Today's models (creating a user makes its workspace) need the rest of the schema.
        call_command("migrate", verbosity=0)

        migrated = User.objects.get(pk=old.pk)
        assert (migrated.email, migrated.pending_email) == ("Mixto@ejemplo.com", "Nuevo@otro.com")
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=User.objects.create_user(username="x", email="x@test.com", password="x").pk).update(
                email="mixto@ejemplo.com",
            )
