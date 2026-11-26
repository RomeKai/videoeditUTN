"""Data migrations of User: they run on a database with the rows they fix."""
from django.core.management import call_command
from django.db import connection
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
