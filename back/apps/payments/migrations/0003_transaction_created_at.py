# Adds Transaction.created_at so stale reservations can be found by age
# (settle_stale_reservations). Rows that existed before this migration get
# the time it runs: they become "old" a full --min-age-hours after the deploy,
# never sooner than a reservation made just before it.

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
