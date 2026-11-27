"""
The Ayrshare profile keys that existed (Workspace.ayrshare_profile_key) become
SocialProfile rows of provider "ayrshare". The column is left as it is: a worker
of the previous release may still read it until it restarts.
"""
from django.db import migrations


def copy_profile_keys(apps, schema_editor):
    Workspace = apps.get_model('users', 'Workspace')
    SocialProfile = apps.get_model('integrations', 'SocialProfile')
    rows = []
    for workspace_id, key in (
        Workspace.objects.exclude(ayrshare_profile_key__isnull=True).values_list('pk', 'ayrshare_profile_key').iterator()
    ):
        key = (key or '').strip()
        if key:
            rows.append(SocialProfile(workspace_id=workspace_id, provider='ayrshare', provider_ref=key))
    # ignore_conflicts: a profile already there (a second run) is kept as it is.
    SocialProfile.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0001_perfiles_de_redes'),
        ('users', '0011_invitaciones'),
    ]

    operations = [
        # Going back drops the table (0001); the keys are still in the old column.
        migrations.RunPython(copy_profile_keys, migrations.RunPython.noop),
    ]
