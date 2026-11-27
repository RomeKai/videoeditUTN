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
        # The same domain with a combining tilde: the older account takes the normalized email.
        unicode_second = stored("dos", "ana@man\u0303ana.com")
        invalid = stored("raro", "tester@℀.com", pending_email="otra@℀.com")
        literal = stored("literal", "root@[127.0.0.1]")
        # Fits as typed, not once its domain is punycode: left, not a DataError.
        long_domain = "ana@" + ".".join(["中文测试域名" * 3] * 9) + ".com"
        long_email = "a" * 220 + "@" + "中文测试域名" * 3 + ".com"
        too_long = stored("largo", long_domain, pending_email=long_email)
        only_pending = stored("pendiente", "pendiente@test.com", pending_email="otra@℀.com")
        sharp = stored("sharp", "ana@straße.de")
        double_s = stored("ss", "ana@strasse.de")

        with self.assertLogs("apps.users.migrations.0008_dominios_de_email_normalizados", level="WARNING") as logs:
            _migrate(self.migrate_to)

        emails = dict(User.objects.values_list("pk", "email"))
        assert emails[plain] == "tester@test.com"
        assert emails[collides] == "tester@ｔｅｓｔ.com"
        assert emails[full_width] == "ana@ejemplo.com"
        assert emails[unicode_first] == "ana@xn--maana-pta.com"
        assert emails[unicode_second] == "ana@man\u0303ana.com"
        assert emails[invalid] == "tester@℀.com"
        assert emails[literal] == "root@[127.0.0.1]"
        assert emails[too_long] == long_domain
        # "ß" is not "ss": two domains, two accounts, none left behind.
        assert (emails[sharp], emails[double_s]) == ("ana@xn--strae-oqa.de", "ana@strasse.de")
        pending = dict(User.objects.values_list("pk", "pending_email"))
        assert pending[full_width] == "Nueva@xn--maana-pta.com"
        assert pending[invalid] == "otra@℀.com"
        assert pending[too_long] == long_email
        assert pending[only_pending] == "otra@℀.com"
        # How many accounts were left (collides, dos, raro, largo, pendiente), never which emails.
        assert [record.getMessage() for record in logs.records] == ["users.email_domains_not_normalized count=5"]
        assert "@" not in "".join(logs.output)

    def test_a_row_the_database_refuses_is_left_and_the_rest_goes_on(self):
        from unittest import mock

        from django.db import DataError

        old_apps = _migrate(self.migrate_from)
        OldUser = old_apps.get_model("users", "User")
        first = OldUser.objects.create(username="uno", email="uno@ＥＪＥＭＰＬＯ.com", password="!").pk
        second = OldUser.objects.create(username="dos", email="dos@ＥＪＥＭＰＬＯ.com", password="!").pk
        real_update = type(OldUser.objects.all()).update
        refused = []

        def update(queryset, **changes):
            if changes.get("email") == "uno@ejemplo.com" and not refused:
                refused.append(True)
                # A real error from the database, inside the row's savepoint.
                queryset.update(email="x" * 300)
            return real_update(queryset, **changes)

        with mock.patch.object(type(OldUser.objects.all()), "update", update), \
                self.assertLogs("apps.users.migrations.0008_dominios_de_email_normalizados", level="WARNING") as logs:
            _migrate(self.migrate_to)

        assert refused
        emails = dict(User.objects.values_list("pk", "email"))
        assert emails[first] == "uno@ＥＪＥＭＰＬＯ.com"
        assert emails[second] == "dos@ejemplo.com"
        assert [record.getMessage() for record in logs.records] == ["users.email_domains_not_normalized count=1"]

    def test_with_nothing_to_fix_it_is_silent(self):
        old_apps = _migrate(self.migrate_from)
        old_apps.get_model("users", "User").objects.create(username="plain", email="tester@test.com", password="!")

        with self.assertNoLogs("apps.users.migrations.0008_dominios_de_email_normalizados", level="WARNING"):
            _migrate(self.migrate_to)


class InvitationsRollbackTests(TransactionTestCase):
    """
    0011 makes WorkspaceMember.user nullable for the invitations to emails
    without an account. Going back, the column is NOT NULL again: the reverse
    deletes those rows first, so a rollback does not stop on them.
    """

    back_to = [("users", "0010_sesiones_revocadas")]

    def tearDown(self):
        call_command("migrate", verbosity=0)

    def test_going_back_deletes_the_invitations_without_an_account_and_keeps_the_members(self):
        from apps.users.models import WorkspaceMember

        owner = User.objects.create_user(username="duena", email="duena@test.com", password="password")
        from apps.payments.models import SubscriptionPlan

        workspace = owner.owned_workspaces.get()
        workspace.subscription_plan = SubscriptionPlan.objects.create(name="Equipo de la vuelta atrás", max_members=5)
        workspace.save(update_fields=["subscription_plan"])
        WorkspaceMember.objects.create(workspace=workspace, status="pending", invited_email="sin-cuenta@example.com")

        old_apps = _migrate(self.back_to)

        OldMember = old_apps.get_model("users", "WorkspaceMember")
        assert list(OldMember.objects.values_list("user_id", "status")) == [(owner.pk, "active")]
        with pytest.raises(IntegrityError), transaction.atomic():
            OldMember.objects.create(workspace_id=workspace.pk, user=None)
