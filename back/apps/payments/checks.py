"""
Startup checks of the payments settings. They run with every management
command (``check``, ``migrate``, ``runserver``); only settings are read.
"""
from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

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
