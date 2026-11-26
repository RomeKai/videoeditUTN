import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0005_seed_subscription_plans'),
    ]

    operations = [
        # Off for every existing row: 0007 turns it on for the four seeded plans.
        migrations.AddField(
            model_name='subscriptionplan',
            name='is_public',
            field=models.BooleanField(default=False, verbose_name='Public'),
        ),
        # Validators only (forms and the admin): no SQL.
        migrations.AlterField(
            model_name='subscriptionplan',
            name='max_members',
            field=models.IntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)], verbose_name='Max Members'),
        ),
        migrations.AlterField(
            model_name='subscriptionplan',
            name='max_video_duration_seconds',
            field=models.IntegerField(default=60, validators=[django.core.validators.MinValueValidator(1)], verbose_name='Max Duration (sec)'),
        ),
    ]
