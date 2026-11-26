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


def _load_prod(code=None, **overrides):
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
    code = code or (
        "import django; django.setup(); from django.conf import settings; "
        "print(settings.REST_FRAMEWORK['NUM_PROXIES'], settings.EMAIL_HOST, settings.EMAIL_TIMEOUT, settings.FRONTEND_URL)"
    )
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=BACK, capture_output=True, text=True)


def test_production_starts_with_its_settings():
    result = _load_prod()

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["1", "smtp.example.com", "10", "https://app.example.com"]


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


HARDENING = (
    "import django; django.setup(); from django.conf import settings; "
    "print(settings.SECURE_SSL_REDIRECT, settings.SESSION_COOKIE_SECURE, settings.CSRF_COOKIE_SECURE, "
    "settings.SECURE_HSTS_SECONDS, settings.SECURE_PROXY_SSL_HEADER[0])"
)


def test_production_forces_https_behind_the_proxy():
    result = _load_prod(HARDENING)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True", "True", "0", "HTTP_X_FORWARDED_PROTO"]


def test_production_https_settings_come_from_the_environment():
    result = _load_prod(
        HARDENING,
        SECURE_SSL_REDIRECT="False",
        SESSION_COOKIE_SECURE="False",
        CSRF_COOKIE_SECURE="False",
        SECURE_HSTS_SECONDS="31536000",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False", "False", "31536000", "HTTP_X_FORWARDED_PROTO"]


def test_production_serves_static_files_with_whitenoise():
    result = _load_prod(
        "import django; django.setup(); from django.conf import settings; "
        "m = settings.MIDDLEWARE; "
        "print(m[m.index('django.middleware.security.SecurityMiddleware') + 1]); "
        "print(settings.STORAGES['staticfiles']['BACKEND']); "
        "print(settings.STORAGES['default']['BACKEND'])"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "whitenoise.middleware.WhiteNoiseMiddleware",
        "whitenoise.storage.CompressedManifestStaticFilesStorage",
        "django.core.files.storage.FileSystemStorage",
    ]
