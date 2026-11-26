import logging

from django.db import migrations

# 0011 adds check constraints: max_members and max_video_duration_seconds are at
# least 1, as their validators (0006) already say. A row below that (an old plan
# saved before the validators) would make it fail, so it is raised to 1 first:
# the closest valid value to "nothing allowed". The seeded plans are all above it.
LIMITS = ('max_members', 'max_video_duration_seconds')


def raise_limits_to_one(SubscriptionPlan, using='default'):
    plans = SubscriptionPlan.objects.using(using)
    return sum(plans.filter(**{f'{field}__lt': 1}).update(**{field: 1}) for field in LIMITS)


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    if not raise_limits_to_one(apps.get_model('payments', 'SubscriptionPlan'), using=using):
        return
    # update() sends no signal: drop the cached public list. Never fails the migration.
    try:
        from apps.payments.services.plans import invalidate_public_plans

        invalidate_public_plans(using=using)
    except Exception as exc:
        logging.getLogger(__name__).warning('plans.cache_unavailable operation=migrate error=%s', type(exc).__name__)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0009_free_plan_two_members'),
    ]

    operations = [
        # Reversing keeps 1: a 0 was never a working limit.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
