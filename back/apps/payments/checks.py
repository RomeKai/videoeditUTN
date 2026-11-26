"""
Startup checks of the payments settings. They run with every management
command (``check``, ``migrate``, ``runserver``); only settings are read.
"""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.db import DatabaseError

KNOWN_PROVIDERS = ('', 'simulated')


@register(Tags.compatibility)
def check_payments_provider(app_configs=None, **kwargs):
    provider = getattr(settings, 'PAYMENTS_PROVIDER', '')
    if provider not in KNOWN_PROVIDERS:
        return [Error(
            f'PAYMENTS_PROVIDER={provider!r} is not a payments provider.',
            hint="Use 'simulated' (development and demos) or leave it empty (no payments: they answer 501).",
            id='payments.E001',
        )]
    return []


@register(Tags.security, deploy=True)
def check_simulated_payments_in_deploy(app_configs=None, **kwargs):
    if getattr(settings, 'PAYMENTS_PROVIDER', '') == 'simulated' and not settings.DEBUG:
        return [Warning(
            'PAYMENTS_PROVIDER=simulated: payments are approved without charging anything.',
            hint='Only for a demo (PAYMENTS_ALLOW_SIMULATED=True). Leave PAYMENTS_PROVIDER empty in production.',
            id='payments.W001',
        )]
    return []


@register(Tags.database)
def check_public_plan_order(app_configs=None, databases=None, **kwargs):
    """
    The pricing page lists the public plans by ``sort_order``, and the web app
    labels "Subir" what comes later and "Bajar" what comes before; what it
    costs is the price (services/subscriptions.py). They must agree: a plan
    later in the list that costs less is warned. Runs with ``check --database`` and ``migrate``;
    before the payments tables exist it says nothing.
    """
    from .models import SubscriptionPlan

    warnings = []
    for alias in databases or []:
        try:
            plans = list(
                SubscriptionPlan.objects.using(alias)
                .filter(is_active=True, is_public=True)
                .order_by('sort_order', 'name')
                .values_list('name', 'price_usd_monthly')
            )
        except DatabaseError:
            continue
        for (previous, previous_price), (name, price) in zip(plans, plans[1:]):
            if price < previous_price:
                warnings.append(Warning(
                    f"Public plan {name!r} comes after {previous!r} in sort_order but costs less "
                    f"({price} < {previous_price} USD).",
                    hint='Order the public plans by price: the web app labels going up and down by sort_order.',
                    id='payments.W002',
                ))
    return warnings
