from decimal import Decimal

from django.db import migrations

# The four plans of the pricing page (artboards A6/G3). `get_or_create` by name:
# a plan someone already created or edited in the admin is left as it is.
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
        'is_recommended': False,
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
        'is_recommended': False,
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
        'is_recommended': True,
    },
    {
        'name': 'Estudio',
        'max_video_duration_seconds': 14400,
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
        'is_recommended': False,
    },
]


def seed_plans(apps, schema_editor):
    SubscriptionPlan = apps.get_model('payments', 'SubscriptionPlan')
    for plan in PLANS:
        values = dict(plan)
        name = values.pop('name')
        SubscriptionPlan.objects.get_or_create(name=name, defaults=values)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0004_subscriptionplan_public_pricing'),
    ]

    operations = [
        # Reversing keeps the rows: workspaces may already point at them.
        migrations.RunPython(seed_plans, migrations.RunPython.noop),
    ]
