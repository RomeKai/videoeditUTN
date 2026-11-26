import logging

from django.db import migrations

# Gratis allows 2 members (its owner and one more) instead of 1. Only if nobody
# changed it: a value set in the admin stays. A new migration and not 0007, so a
# database that already ran 0007 gets it too.
FREE_PLAN = 'Gratis'
OLD_MAX_MEMBERS = 1
MAX_MEMBERS = 2


def widen_free_plan(SubscriptionPlan, using='default'):
    """Safe to run again. The plan tests call it after 0007's seed (payments/tests/conftest.py)."""
    SubscriptionPlan.objects.using(using).filter(name=FREE_PLAN, max_members=OLD_MAX_MEMBERS).update(
        max_members=MAX_MEMBERS
    )


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    widen_free_plan(apps.get_model('payments', 'SubscriptionPlan'), using=using)

    # update() sends no signal: drop the cached public list here.
    # A cache error never fails the migration (the cache is only an optimization,
    # and the next migrations must still run): it is logged and the TTL ends the list.
    try:
        from apps.payments.services.plans import invalidate_public_plans

        invalidate_public_plans(using=using)
    except Exception as exc:
        logging.getLogger(__name__).warning('plans.cache_unavailable operation=migrate error=%s', type(exc).__name__)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0008_subscriptionplan_single_recommended'),
    ]

    operations = [
        # Reversing keeps 2: no code before this one needs Gratis at 1, and
        # lowering it could leave a workspace with more members than its plan.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
