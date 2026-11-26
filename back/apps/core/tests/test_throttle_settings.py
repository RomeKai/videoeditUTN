"""A malformed attempt limit stops the app when it starts, not at the first login."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACK = Path(__file__).resolve().parents[3]


def _load_settings(**variables):
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "backend.settings.ci", **variables}
    code = "from django.conf import settings; print(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['login'], settings.LOGIN_FAILURE_RATES['account_ip'])"
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)


def test_valid_rates_load():
    result = _load_settings(THROTTLE_LOGIN="20/hour", LOGIN_FAILURES_PER_ACCOUNT_IP="5/30m")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["20/hour", "5/30m"]


@pytest.mark.parametrize(
    "variable, value",
    [
        ("THROTTLE_LOGIN", "10"),
        ("THROTTLE_LOGIN", "10/month"),
        ("THROTTLE_TOKEN_REFRESH", "0/min"),
        ("THROTTLE_PASSWORD_CHANGE", "cinco/hour"),
        ("THROTTLE_EMAIL_CHANGE", ""),
        ("LOGIN_FAILURES_PER_ACCOUNT_IP", "10/15min"),
        ("LOGIN_FAILURES_PER_ACCOUNT", "100"),
    ],
)
def test_a_malformed_rate_stops_the_start(variable, value):
    result = _load_settings(**{variable: value})

    assert result.returncode != 0
    assert f"ImproperlyConfigured: {variable}={value!r} is not a valid rate" in result.stderr
