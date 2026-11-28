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
    "FRONTEND_URL": "https://app.example.com",
    "DEFAULT_FROM_EMAIL": "OneCreator <no-reply@example.com>",
    "EMAIL_HOST": "smtp.example.com",
}


def _load_prod(**overrides):
    if (BACK / ".env").exists():
        pytest.skip("back/.env would fill the missing variables")
    env = {key: value for key, value in os.environ.items() if key not in PRODUCTION and key != "AYRSHARE_BASE_URL"}
    env.update(PRODUCTION)
    env.update({"DJANGO_SETTINGS_MODULE": "backend.settings.prod"})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    code = (
        "import django; django.setup(); from django.conf import settings; "
        "print(settings.REST_FRAMEWORK['NUM_PROXIES'], settings.EMAIL_HOST, settings.EMAIL_TIMEOUT, settings.FRONTEND_URL, "
        "settings.AYRSHARE_BASE_URL)"
    )
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)


def test_production_starts_with_its_settings():
    result = _load_prod()

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "1", "smtp.example.com", "10", "https://app.example.com", "https://api.ayrshare.com/api",
    ]


def test_production_needs_num_proxies():
    result = _load_prod(NUM_PROXIES=None)

    assert result.returncode != 0
    assert "ImproperlyConfigured: NUM_PROXIES is required in production" in result.stderr


@pytest.mark.parametrize("missing", ["FRONTEND_URL", "DEFAULT_FROM_EMAIL", "EMAIL_HOST"])
def test_production_needs_the_email_settings(missing):
    result = _load_prod(**{missing: None})

    assert result.returncode != 0
    assert f"ImproperlyConfigured: Email is not configured for production: set {missing}" in result.stderr


def test_another_email_backend_does_not_need_an_smtp_host():
    result = _load_prod(EMAIL_HOST=None, EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")

    assert result.returncode == 0, result.stderr


def test_another_ayrshare_base_url_comes_from_the_environment():
    """Ensayo 5: a stand-in of Ayrshare for a rehearsal, over https in production."""
    result = _load_prod(AYRSHARE_BASE_URL="https://ayrshare-falso.example.com/api/")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-1] == "https://ayrshare-falso.example.com/api"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8765/api", "ftp://ayrshare.example.com/api", "https:///api", "api.ayrshare.com/api"])
def test_production_refuses_an_ayrshare_base_url_without_https(url):
    result = _load_prod(AYRSHARE_BASE_URL=url)

    assert result.returncode != 0
    assert "ImproperlyConfigured: AYRSHARE_BASE_URL must be an https URL in production" in result.stderr


def test_production_takes_an_https_media_origin():
    """Review of #192: the origin of the local files goes to browsers and to the publishing provider."""
    assert _load_prod(MEDIA_PUBLIC_BASE_URL="https://media.example.com/").returncode == 0

    result = _load_prod(MEDIA_PUBLIC_BASE_URL="http://media.example.com")

    assert result.returncode != 0
    assert "ImproperlyConfigured: MEDIA_PUBLIC_BASE_URL must be an https origin in production" in result.stderr


@pytest.mark.parametrize("value", ["localhost:8000", "ftp://media.example.com", "http://", "http://localhost:8000/media"])
def test_every_environment_refuses_a_media_origin_that_is_not_one(value):
    """Review of #192: `localhost:8000` gave http://<web>/localhost:8000/media/… instead of failing."""
    env = {key: val for key, val in os.environ.items() if key != "MEDIA_PUBLIC_BASE_URL"}
    env.update({"DJANGO_SETTINGS_MODULE": "backend.settings.ci", "MEDIA_PUBLIC_BASE_URL": value})
    code = "import django; django.setup(); from django.conf import settings; print(settings.MEDIA_PUBLIC_BASE_URL)"
    result = subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)

    assert result.returncode != 0
    assert "ImproperlyConfigured: MEDIA_PUBLIC_BASE_URL=" in result.stderr
