from django.db import migrations, models


def to_square_1_1(apps, schema_editor):
    VideoProject = apps.get_model('videos', 'VideoProject')
    VideoProject.objects.filter(aspect_ratio='1:Post').update(aspect_ratio='1:1')


def to_legacy_square(apps, schema_editor):
    VideoProject = apps.get_model('videos', 'VideoProject')
    VideoProject.objects.filter(aspect_ratio='1:1').update(aspect_ratio='1:Post')


class Migration(migrations.Migration):
    """
    The square ratio was stored as '1:Post', but the render only knows '1:1',
    so square projects were rendered vertical. Same label, new value; existing
    rows are converted.
    """

    dependencies = [
        ('videos', '0008_aiusagerecord_prompt_parse_stage'),
    ]

    operations = [
        migrations.AlterField(
            model_name='videoproject',
            name='aspect_ratio',
            field=models.CharField(
                choices=[('9:16', 'Vertical (TikTok/Reels)'), ('1:1', 'Cuadrado (Post)'), ('16:9', 'Horizontal (YouTube)')],
                default='9:16', max_length=10,
            ),
        ),
        migrations.RunPython(to_square_1_1, to_legacy_square),
    ]
