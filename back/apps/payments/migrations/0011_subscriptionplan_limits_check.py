from django.db import migrations, models


class Migration(migrations.Migration):
    """Apart from 0010: on PostgreSQL, data and schema changes go in separate migrations."""

    dependencies = [
        ('payments', '0010_raise_plan_limits_to_one'),
    ]

    operations = [
        # 0010 left every row at 1 or more, so these cannot fail on existing rows.
        migrations.AddConstraint(
            model_name='subscriptionplan',
            constraint=models.CheckConstraint(condition=models.Q(('max_members__gte', 1)), name='payments_plan_max_members_min_1', violation_error_message='A plan allows at least 1 member.'),
        ),
        migrations.AddConstraint(
            model_name='subscriptionplan',
            constraint=models.CheckConstraint(condition=models.Q(('max_video_duration_seconds__gte', 1)), name='payments_plan_max_duration_min_1', violation_error_message='A plan allows videos of at least 1 second.'),
        ),
    ]
