"""
What the networks of a workspace need (gap 10): the last read of its accounts,
the unlinks, the first link, the mark of an orphan, a workspace that may be
gone (SET_NULL), and one row per provider profile.

`workspace` becoming nullable is done by hand on PostgreSQL: Django's AlterField
drops and re-adds the foreign key, which takes an ACCESS EXCLUSIVE lock on
users_workspace while old containers still serve. Only the column changes
(on_delete lives in Django), under a short lock_timeout: a long transaction on
the table makes the migration fail fast instead of queueing every request.

Before the unique constraint, rows that share a provider profile (two
workspaces with the same legacy ayrshare_profile_key: each one could publish
to the other's accounts) keep it only in the oldest row; the others lose their
ref and connect again.
"""
import django.db.models.deletion
from django.db import migrations, models

LOCK = "SET LOCAL lock_timeout = '5s'"


class NullableWorkspace(migrations.AlterField):
    """AlterField that, on PostgreSQL, only drops (or sets) NOT NULL on the column."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return super().database_forwards(app_label, schema_editor, from_state, to_state)
        schema_editor.execute(LOCK, params=None)
        schema_editor.execute('ALTER TABLE integrations_socialprofile ALTER COLUMN workspace_id DROP NOT NULL', params=None)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != 'postgresql':
            return super().database_backwards(app_label, schema_editor, from_state, to_state)
        schema_editor.execute(LOCK, params=None)
        schema_editor.execute('DELETE FROM integrations_socialprofile WHERE workspace_id IS NULL', params=None)
        schema_editor.execute('ALTER TABLE integrations_socialprofile ALTER COLUMN workspace_id SET NOT NULL', params=None)


def keep_one_row_per_profile(apps, schema_editor):
    SocialProfile = apps.get_model('integrations', 'SocialProfile')
    seen, cleared = set(), 0
    for pk, provider, ref in (
        SocialProfile.objects.exclude(provider_ref='').order_by('created_at', 'pk')
        .values_list('pk', 'provider', 'provider_ref').iterator()
    ):
        if (provider, ref) in seen:
            SocialProfile.objects.filter(pk=pk).update(provider_ref='', provider_id='', networks=None)
            cleared += 1
        seen.add((provider, ref))
    if cleared:
        print(f"\n  integrations.0003: {cleared} perfiles compartían la key de otro espacio y quedaron sin ella.")


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0002_perfiles_desde_ayrshare_profile_key"),
        ("users", "0011_invitaciones"),
    ]

    operations = [
        migrations.AddField(model_name="socialprofile", name="networks", field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name="socialprofile", name="networks_read_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="socialprofile", name="disconnected_at", field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name="socialprofile", name="linked_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="socialprofile", name="orphaned_at", field=models.DateTimeField(blank=True, null=True)),
        NullableWorkspace(
            model_name="socialprofile",
            name="workspace",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="social_profile",
                to="users.workspace",
            ),
        ),
        migrations.RunPython(keep_one_row_per_profile, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="socialprofile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("provider_ref", ""), _negated=True),
                fields=("provider", "provider_ref"),
                name="integrations_one_row_per_provider_profile",
            ),
        ),
    ]
