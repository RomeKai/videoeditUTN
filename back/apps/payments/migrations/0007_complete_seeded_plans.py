import logging
from decimal import Decimal

from django.db import migrations

# The four plans of the pricing page (artboards A6/G3), as 0005 seeded them
# except Estudio, which now allows 3 h (10800 s, the AI_UPLOAD_MAX_DURATION_SECONDS
# ceiling) instead of 4 h.
#
# 0005 used `get_or_create` by name, so a row that already existed kept the bare
# values of 0004: price 0.00, no description, sort_order 0 and not recommended.
# That happens where a plan with one of these names was there before 0005, and
# after rolling back past 0004 and migrating again. This migration fills those
# fields only while they still hold the bare value, so an edit made in the admin
# stays. A plan that is missing is created, as 0005 would.
PLANS = [
    {
        'name': 'Gratis',
        'max_video_duration_seconds': 600,
        'max_resolution': '720p',
        'has_watermark': True,
        'allow_scheduling': False,
        'allow_crossposting': False,
        'has_seo_optimization': False,
        'has_thumbnail_engine': False,
        'base_discount_rate': Decimal('0.00'),
        'max_members': 1,
        'price_usd_monthly': Decimal('0.00'),
        'description': 'Para probar con videos cortos.',
        'sort_order': 1,
    },
    {
        'name': 'Creador',
        'max_video_duration_seconds': 3600,
        'max_resolution': '1080p',
        'has_watermark': False,
        'allow_scheduling': True,
        'allow_crossposting': False,
        'has_seo_optimization': True,
        'has_thumbnail_engine': False,
        'base_discount_rate': Decimal('0.05'),
        'max_members': 2,
        'price_usd_monthly': Decimal('19.00'),
        'description': 'Para publicar seguido en una red.',
        'sort_order': 2,
    },
    {
        'name': 'Pro',
        'max_video_duration_seconds': 10800,
        'max_resolution': '4K',
        'has_watermark': False,
        'allow_scheduling': True,
        'allow_crossposting': True,
        'has_seo_optimization': True,
        'has_thumbnail_engine': False,
        'base_discount_rate': Decimal('0.10'),
        'max_members': 5,
        'price_usd_monthly': Decimal('49.00'),
        'description': 'Para equipos que publican en todas las redes.',
        'sort_order': 3,
    },
    {
        'name': 'Estudio',
        'max_video_duration_seconds': 10800,
        'max_resolution': '4K',
        'has_watermark': False,
        'allow_scheduling': True,
        'allow_crossposting': True,
        'has_seo_optimization': True,
        'has_thumbnail_engine': False,
        'base_discount_rate': Decimal('0.20'),
        'max_members': 15,
        'price_usd_monthly': Decimal('129.00'),
        'description': 'Para estudios con videos largos y más gente.',
        'sort_order': 4,
    },
]

RECOMMENDED = 'Pro'

# The value 0004 gives each pricing field: still holding it means nobody set it.
BARE = {
    'price_usd_monthly': Decimal('0.00'),
    'description': '',
    'sort_order': 0,
}

# Estudio's old limit (0005), moved to 3 h only if nobody changed it.
ESTUDIO_OLD_MAX_SECONDS = 14400
ESTUDIO_MAX_SECONDS = 10800


def seed_plans(SubscriptionPlan, using='default'):
    """
    Leave the four plans complete and public. Safe to run again: it only writes
    what is missing or still bare. The plan tests call it too (payments/tests/conftest.py).
    """
    plans = SubscriptionPlan.objects.using(using)

    for entry in PLANS:
        values = dict(entry)
        name = values.pop('name')
        plan, created = plans.get_or_create(name=name, defaults={**values, 'is_public': True})
        if created:
            continue

        changed = []
        for field, bare in BARE.items():
            if getattr(plan, field) == bare and values[field] != bare:
                setattr(plan, field, values[field])
                changed.append(field)
        if not plan.is_public:
            plan.is_public = True
            changed.append('is_public')
        if name == 'Estudio' and plan.max_video_duration_seconds == ESTUDIO_OLD_MAX_SECONDS:
            plan.max_video_duration_seconds = ESTUDIO_MAX_SECONDS
            changed.append('max_video_duration_seconds')
        if changed:
            plan.save(update_fields=changed)

    # One recommended plan (0008 makes it a constraint): Pro, unless someone
    # already marked another one. With more than one, Pro (or the first in
    # display order) keeps the mark.
    recommended = list(plans.filter(is_recommended=True).order_by('sort_order', 'name'))
    if not recommended:
        plans.filter(name=RECOMMENDED).update(is_recommended=True)
    elif len(recommended) > 1:
        keep = next((plan for plan in recommended if plan.name == RECOMMENDED), recommended[0])
        plans.filter(is_recommended=True).exclude(pk=keep.pk).update(is_recommended=False)


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    seed_plans(apps.get_model('payments', 'SubscriptionPlan'), using=using)

    # Historical models do not send the signals that refresh the public list.
    # A cache error never fails the migration (the cache is only an optimization,
    # and the next migrations must still run): it is logged and the TTL ends the list.
    try:
        from apps.payments.services.plans import invalidate_public_plans

        invalidate_public_plans(using=using)
    except Exception as exc:
        logging.getLogger(__name__).warning('plans.cache_unavailable operation=migrate error=%s', type(exc).__name__)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_subscriptionplan_is_public_and_limits'),
    ]

    operations = [
        # Reversing keeps the values: they are valid for the code of 0005 too, and
        # rolling back further drops the pricing columns (0004) and is_public (0006).
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
