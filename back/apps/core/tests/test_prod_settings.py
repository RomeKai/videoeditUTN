"""backend.settings.prod refuses to start without the settings production needs."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACK = Path(__file__).resolve().parents[3]

# What production needs on top of the CI environment; each test drops one.
PRODUCTION = {
    "NUM_PROXIES": "1",
}


def _load_prod(**overrides):
    if (BACK / ".env").exists():
        pytest.skip("back/.env would fill the missing variables")
    env = {key: value for key, value in os.environ.items() if key not in PRODUCTION}
    env.update(PRODUCTION)
    env.update({"DJANGO_SETTINGS_MODULE": "backend.settings.prod"})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    code = "import django; django.setup(); from django.conf import settings; print(settings.REST_FRAMEWORK['NUM_PROXIES'])"
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)


def test_production_starts_with_its_settings():
    result = _load_prod()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_production_needs_num_proxies():
    result = _load_prod(NUM_PROXIES=None)

    assert result.returncode != 0
    assert "ImproperlyConfigured: NUM_PROXIES is required in production" in result.stderr
