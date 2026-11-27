"""
One default brand kit per workspace at most, as a partial unique constraint.

Until now nothing kept it: marking a kit default left the previous one marked
(the web app unmarked it with a second request, which could fail). So the
data may hold workspaces with several defaults, and the constraint cannot be
created over them. First each of those workspaces keeps one default, the
oldest kit (the first of the list, the one the web app preselects), and the
others are unmarked. No kit is deleted and no workspace gains a default.
Reversing drops the constraint and leaves the data as it is.
"""

from django.db import migrations, models
from django.db.models import Count


def keep_one_default(apps, schema_editor):
    BrandKit = apps.get_model('videos', 'BrandKit')
    defaults = BrandKit.objects.filter(is_default=True)
    repeated = (
        defaults.values('workspace_id').annotate(total=Count('pk')).filter(total__gt=1)
        .values_list('workspace_id', flat=True)
    )
    for workspace_id in list(repeated):
        kept = defaults.filter(workspace_id=workspace_id).order_by('created_at', 'pk').values_list('pk', flat=True).first()
        defaults.filter(workspace_id=workspace_id).exclude(pk=kept).update(is_default=False)


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0018_clip_thumbnail_object_key'),
    ]

    operations = [
        migrations.RunPython(keep_one_default, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='brandkit',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_default', True)),
                fields=('workspace',),
                name='one_default_brand_kit_per_workspace',
            ),
        ),
    ]
