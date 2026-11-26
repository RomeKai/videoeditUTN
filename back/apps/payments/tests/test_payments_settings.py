"""
Nothing fake runs in production: without PAYMENTS_PROVIDER there is no
provider, and the simulated one needs DEBUG or an explicit demo flag
(backend/settings/prod.py refuses it otherwise).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.checks import run_checks
from django.test import override_settings

from apps.payments.services.payment_providers import get_payment_provider

BACK = Path(__file__).resolve().parents[3]

PRODUCTION = {
    "DJANGO_SETTINGS_MODULE": "backend.settings.prod",
    "NUM_PROXIES": "1",
    "FRONTEND_URL": "https://app.example.com",
    "DEFAULT_FROM_EMAIL": "OneCreator <no-reply@example.com>",
    "EMAIL_HOST": "smtp.example.com",
}
PROVIDER = (
    "import django; django.setup(); "
    "from apps.payments.services.payment_providers import get_payment_provider; "
    "provider = get_payment_provider(); print(provider.name if provider else 'none')"
)


def _run_prod(**overrides):
    if (BACK / ".env").exists():
        pytest.skip("back/.env would fill the variables")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PAYMENTS_")}
    env.update(PRODUCTION)
    env.update(overrides)
    return subprocess.run([sys.executable, "-c", PROVIDER], env=env, cwd=BACK, capture_output=True, text=True)


def test_production_has_no_payments_provider_by_default():
    result = _run_prod()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "none"


def test_production_refuses_the_simulated_provider_without_the_demo_flag():
    result = _run_prod(PAYMENTS_PROVIDER="simulated")

    assert result.returncode != 0
    assert "ImproperlyConfigured: PAYMENTS_PROVIDER=simulated approves payments without charging them" in result.stderr


def test_a_demo_runs_the_simulated_provider_in_production_only_when_it_asks_and_says_so():
    result = _run_prod(PAYMENTS_PROVIDER="simulated", PAYMENTS_ALLOW_SIMULATED="True")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "simulated"
    assert "payments.simulated_provider_on" in result.stderr


@pytest.mark.parametrize("overrides, expected", [
    ({"PAYMENTS_PROVIDER": "", "DEBUG": True}, None),
    ({"PAYMENTS_PROVIDER": "simulated", "PAYMENTS_ALLOW_SIMULATED": False, "DEBUG": False}, None),
    ({"PAYMENTS_PROVIDER": "simulated", "PAYMENTS_ALLOW_SIMULATED": False, "DEBUG": True}, "simulated"),
    ({"PAYMENTS_PROVIDER": "simulated", "PAYMENTS_ALLOW_SIMULATED": True, "DEBUG": False}, "simulated"),
    ({"PAYMENTS_PROVIDER": "stripe", "PAYMENTS_ALLOW_SIMULATED": True, "DEBUG": True}, None),
])
def test_which_provider_runs(overrides, expected):
    with override_settings(**overrides):
        provider = get_payment_provider()

    assert (provider.name if provider else None) == expected


def test_an_unknown_provider_stops_the_checks():
    with override_settings(PAYMENTS_PROVIDER="strype"):
        ids = [message.id for message in run_checks()]

    assert "payments.E001" in ids


def test_the_deploy_check_warns_about_the_simulated_provider():
    with override_settings(PAYMENTS_PROVIDER="simulated", PAYMENTS_ALLOW_SIMULATED=True, DEBUG=False):
        ids = [message.id for message in run_checks(include_deployment_checks=True)]
    with override_settings(PAYMENTS_PROVIDER="", DEBUG=False):
        clean = [message.id for message in run_checks(include_deployment_checks=True)]

    assert "payments.W001" in ids
    assert "payments.W001" not in clean
