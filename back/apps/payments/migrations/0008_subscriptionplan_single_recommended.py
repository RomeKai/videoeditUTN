from django.db import migrations, models


class Migration(migrations.Migration):
    """Apart from 0007: on PostgreSQL, data and schema changes go in separate migrations."""

    dependencies = [
        ('payments', '0007_complete_seeded_plans'),
    ]

    operations = [
        # 0007 left at most one plan recommended, so this cannot fail on existing rows.
        migrations.AddConstraint(
            model_name='subscriptionplan',
            constraint=models.UniqueConstraint(condition=models.Q(('is_recommended', True)), fields=('is_recommended',), name='payments_plan_single_recommended', violation_error_message='Only one plan can be recommended. Unmark the other one first.'),
        ),
    ]
