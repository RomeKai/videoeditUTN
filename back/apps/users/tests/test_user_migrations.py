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

        migrated = User.objects.get(pk=old.pk)
        assert (migrated.email, migrated.pending_email) == ("Mixto@ejemplo.com", "Nuevo@otro.com")
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=User.objects.create_user(username="x", email="x@test.com", password="x").pk).update(
                email="mixto@ejemplo.com",
            )


class NormalizedEmailDomainMigrationTests(TransactionTestCase):
    migrate_from = [("users", "0007_email_unico_sin_mayusculas")]
    migrate_to = [("users", "0008_dominios_de_email_normalizados")]

    def tearDown(self):
        call_command("migrate", verbosity=0)

    def test_it_normalizes_what_does_not_collide_and_never_fails(self):
        old_apps = _migrate(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")

        def stored(username, email, pending_email=None):
            return OldUser.objects.create(username=username, email=email, password="!", pending_email=pending_email).pk

        plain = stored("plain", "tester@test.com")
        collides = stored("collides", "tester@ｔｅｓｔ.com")
        full_width = stored("ancho", "ana@ＥＪＥＭＰＬＯ.com", pending_email="Nueva@Mañana.com")
        unicode_first = stored("uno", "ana@mañana.com")
        # The same domain with a combining tilde: after the first one is rewritten, it collides.
        unicode_second = stored("dos", "ana@man\u0303ana.com")
        invalid = stored("raro", "tester@℀.com", pending_email="otra@℀.com")
        literal = stored("literal", "root@[127.0.0.1]")

        with self.assertLogs("apps.users.migrations.0008_dominios_de_email_normalizados", level="WARNING") as logs:
            _migrate(self.migrate_to)

        emails = dict(User.objects.values_list("pk", "email"))
        assert emails[plain] == "tester@test.com"
        assert emails[collides] == "tester@ｔｅｓｔ.com"
        assert emails[full_width] == "ana@ejemplo.com"
        assert {emails[unicode_first], emails[unicode_second]} == {"ana@xn--maana-pta.com", "ana@man\u0303ana.com"}
        assert emails[invalid] == "tester@℀.com"
        assert emails[literal] == "root@[127.0.0.1]"
        assert User.objects.get(pk=full_width).pending_email == "Nueva@xn--maana-pta.com"
        assert User.objects.get(pk=invalid).pending_email == "otra@℀.com"
        # How many were left, never which emails.
        assert [record.getMessage() for record in logs.records] == ["users.email_domains_not_normalized count=3"]
        assert "@" not in "".join(logs.output)

    def test_with_nothing_to_fix_it_is_silent(self):
        old_apps = _migrate(self.migrate_from)
        old_apps.get_model("users", "User").objects.create(username="plain", email="tester@test.com", password="!")

        with self.assertNoLogs("apps.users.migrations.0008_dominios_de_email_normalizados", level="WARNING"):
            _migrate(self.migrate_to)
