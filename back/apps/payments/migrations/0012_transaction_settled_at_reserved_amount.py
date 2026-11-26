# The project's activity (GET /projects/<id>/activity/) shows when a
# reservation was charged or returned, and how much of a partial charge went
# back. Neither was stored: a partial charge overwrote ``amount`` and the
# settlement had no time. Rows from before this migration keep both null.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0011_subscriptionplan_limits_check'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='reserved_amount',
            field=models.DecimalField(blank=True, decimal_places=10, max_digits=20, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='settled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
