import logging

from django.db import migrations, models

# Connecting the networks of a workspace takes a paid profile at the publishing provider
# (apps.integrations.social.accounts): the paid plans include it, Gratis does not. By name,
# like the other plan flags, not by price; a plan made in the admin starts without it.
PAID_PLANS = ('Creador', 'Pro', 'Estudio')


def allow_social_accounts(SubscriptionPlan, using='default'):
    """Safe to run again. The plan tests call it after 0007's seed (payments/tests/conftest.py)."""
    return SubscriptionPlan.objects.using(using).filter(name__in=PAID_PLANS).update(allow_social_accounts=True)


def short_lock_wait(apps, schema_editor):
    """
    First in either direction. payments_subscriptionplan is read on every
    workspace request (select_related): adding the column waits at most 5 s for
    its lock and fails, instead of queueing every request behind a long reader.
    """
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute("SET LOCAL lock_timeout = '5s'", params=None)


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    if not allow_social_accounts(apps.get_model('payments', 'SubscriptionPlan'), using=using):
        return
    # update() sends no signal: drop the cached public list here (a cache error never fails the migration).
    try:
        from apps.payments.services.plans import invalidate_public_plans

        invalidate_public_plans(using=using)
    except Exception as exc:
        logging.getLogger(__name__).warning('plans.cache_unavailable operation=migrate error=%s', type(exc).__name__)


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0018_low_balance_notice"),
    ]

    operations = [
        migrations.RunPython(short_lock_wait, migrations.RunPython.noop),
        migrations.AddField(
            model_name="subscriptionplan",
            name="allow_social_accounts",
            field=models.BooleanField(db_default=False, default=False, verbose_name="Allow Social Accounts"),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        # Going back, this one runs first.
        migrations.RunPython(migrations.RunPython.noop, short_lock_wait),
    ]
