"""
Writes ROLE_MATRIX (apps/core/permissions.py) to front/src/lib/role-matrix.json.

The frontend keeps its own copy of the table (front/src/lib/permissions.ts) to
disable what a role cannot do. That file is what keeps the two in step: run this
command after changing ROLE_MATRIX and commit the result. ``--check`` writes
nothing and fails when the file is out of date.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.core.permissions import ROLE_MATRIX_FIXTURE, role_matrix_json


class Command(BaseCommand):
    help = "Writes the role matrix to front/src/lib/role-matrix.json (--check only compares)."

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true', help="Fail if the file is out of date; write nothing.")

    def handle(self, *args, check=False, **options):
        expected = role_matrix_json()
        if check:
            current = ROLE_MATRIX_FIXTURE.read_text(encoding='utf-8') if ROLE_MATRIX_FIXTURE.exists() else None
            if current != expected:
                raise CommandError(
                    f"{ROLE_MATRIX_FIXTURE} is out of date. Run: python manage.py export_role_matrix"
                )
            self.stdout.write("role-matrix.json is up to date.")
            return
        ROLE_MATRIX_FIXTURE.write_text(expected, encoding='utf-8')
        self.stdout.write(f"Wrote {ROLE_MATRIX_FIXTURE}")
