import logging

from django.db import migrations

# Pro allows 10 members instead of 5. Only if nobody changed it: a value set in
# the admin stays. A new migration and not 0005, so a database that already ran
# the seed gets it too.
PRO_PLAN = 'Pro'
OLD_MAX_MEMBERS = 5
MAX_MEMBERS = 10


def widen_pro_plan(SubscriptionPlan, using='default'):
    """Safe to run again. The plan tests call it after 0007's seed (payments/tests/conftest.py)."""
    return SubscriptionPlan.objects.using(using).filter(name=PRO_PLAN, max_members=OLD_MAX_MEMBERS).update(
        max_members=MAX_MEMBERS
    )


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    if not widen_pro_plan(apps.get_model('payments', 'SubscriptionPlan'), using=using):
        return

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
        ('payments', '0013_creator_plan_five_members'),
    ]

    operations = [
        # Reversing keeps 10: no code before this one needs Pro at 5, and
        # lowering it could leave a workspace with more members than its plan.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
