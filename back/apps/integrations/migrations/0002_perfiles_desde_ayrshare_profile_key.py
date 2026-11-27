"""
The Ayrshare profile keys that existed (Workspace.ayrshare_profile_key) become
SocialProfile rows of provider "ayrshare". The column is left as it is: a worker
of the previous release may still read it until it restarts.

Going back copies the keys of the Ayrshare profiles to the workspaces whose
column is empty (the profiles created after this migration ran), so the
previous release keeps publishing with them.
"""
from django.db import migrations, models


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


def copy_profiles_back(apps, schema_editor):
    Workspace = apps.get_model('users', 'Workspace')
    SocialProfile = apps.get_model('integrations', 'SocialProfile')
    profiles = (
        SocialProfile.objects.filter(provider='ayrshare', workspace__isnull=False).exclude(provider_ref='')
        .values_list('workspace_id', 'provider_ref')
    )
    for workspace_id, key in profiles.iterator():
        Workspace.objects.filter(pk=workspace_id).filter(
            models.Q(ayrshare_profile_key__isnull=True) | models.Q(ayrshare_profile_key=''),
        ).update(ayrshare_profile_key=key)


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0001_perfiles_de_redes'),
        ('users', '0011_invitaciones'),
    ]

    operations = [
        # Going back fills the old column where it is empty; then 0001 drops the table.
        migrations.RunPython(copy_profile_keys, copy_profiles_back),
    ]
