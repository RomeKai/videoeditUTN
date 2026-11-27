"""
When a connect link was last asked for (`connect_requested_at`): the cleanup
of never-linked profiles skips one that someone may be linking right now.
A nullable column, under a short lock_timeout set first in either direction.
"""
from django.db import migrations, models

LOCK = "SET LOCAL lock_timeout = '5s'"


def short_lock_wait(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(LOCK, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0003_redes_por_espacio"),
    ]

    operations = [
        migrations.RunPython(short_lock_wait, migrations.RunPython.noop),
        migrations.AddField(
            model_name="socialprofile",
            name="connect_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Going back, this one runs first.
        migrations.RunPython(migrations.RunPython.noop, short_lock_wait),
    ]
