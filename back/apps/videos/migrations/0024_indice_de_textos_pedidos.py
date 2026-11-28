"""
The posts whose AI text is on its way (`seo_requested_at` set): fail_lost_ai_texts reads them on every
beat run and after every write that leaves a post due, and without an index that read scanned the
whole of videos_scheduledpost. A partial index: only the rows with a request, a few at any time.

Built with CREATE INDEX CONCURRENTLY, so the posts stay writable while it builds (the migration is
not atomic: PostgreSQL refuses it inside a transaction), under a short lock_timeout set first in either
direction: a long transaction on the table makes the migration fail fast instead of queueing every
write behind it. Such a failure can leave an invalid index with this name, which the next run drops
before building it again. On SQLite (development) it is a plain CREATE INDEX.
"""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models

INDEX = "post_seo_requested_idx"


def short_lock_wait(apps, schema_editor):
    """First in either direction: each statement waits at most 5 s for its lock (a session setting, no transaction)."""
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute("SET lock_timeout = '5s'", params=None)


def usual_lock_wait(apps, schema_editor):
    """Last in either direction: the connection's own lock_timeout again, for what runs after."""
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute("RESET lock_timeout", params=None)


def drop_failed_build(apps, schema_editor):
    """An earlier run that failed while building it left the index invalid: it goes, and is built again."""
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(%s)", [INDEX])
        row = cursor.fetchone()
    if row is not None and not row[0]:
        schema_editor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{INDEX}"', params=None)


class AddIndexConcurrentlyOnPostgres(AddIndexConcurrently):
    """CREATE INDEX CONCURRENTLY on PostgreSQL; a plain AddIndex anywhere else."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == 'postgresql':
            return super().database_forwards(app_label, schema_editor, from_state, to_state)
        return migrations.AddIndex.database_forwards(self, app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor == 'postgresql':
            return super().database_backwards(app_label, schema_editor, from_state, to_state)
        return migrations.AddIndex.database_backwards(self, app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("videos", "0023_clave_de_publicacion_incierta"),
    ]

    operations = [
        migrations.RunPython(short_lock_wait, usual_lock_wait),
        migrations.RunPython(drop_failed_build, migrations.RunPython.noop),
        AddIndexConcurrentlyOnPostgres(
            model_name="scheduledpost",
            index=models.Index(
                condition=models.Q(("seo_requested_at__isnull", False)),
                fields=["seo_requested_at"],
                name=INDEX,
            ),
        ),
        # Going back, this one runs first.
        migrations.RunPython(usual_lock_wait, short_lock_wait),
    ]
