import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[3]


def run_settings_import(settings_module, *, environment=None, prelude=""):
    """Import a settings module in an isolated Python process."""
    process_environment = os.environ.copy()
    process_environment.pop("DATABASE_URL", None)
    process_environment.pop("DJANGO_SETTINGS_MODULE", None)
    process_environment.update(environment or {})
    process_environment["PYTHONPATH"] = str(BACKEND_ROOT)

    script = f"""
import json
{prelude}
from {settings_module} import *
print(json.dumps({{
    "database_engine": DATABASES["default"]["ENGINE"],
    "debug": DEBUG,
    "use_r2": globals().get("USE_R2"),
    "email_backend": globals().get("EMAIL_BACKEND"),
    "celery_eager": globals().get("CELERY_TASK_ALWAYS_EAGER"),
    "celery_eager_propagates": globals().get("CELERY_TASK_EAGER_PROPAGATES"),
    "celery_broker": globals().get("CELERY_BROKER_URL"),
    "celery_backend": globals().get("CELERY_RESULT_BACKEND"),
    "media_root": str(MEDIA_ROOT),
    "installed_apps": bool(INSTALLED_APPS),
    "middleware": bool(MIDDLEWARE),
    "secret_key": bool(SECRET_KEY),
    "root_urlconf": ROOT_URLCONF,
}}))
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=process_environment,
        capture_output=True,
        text=True,
        check=False,
    )


def parse_settings_output(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_ci_settings_do_not_read_dotenv():
    result = run_settings_import(
        "backend.settings.ci",
        environment={
            "DJANGO_SETTINGS_MODULE": "backend.settings.ci",
            "DATABASE_URL": "postgres://postgres:test@127.0.0.1:5432/onecreator_ci",
        },
        prelude="""
import environ
def reject_read_env(*args, **kwargs):
    raise AssertionError("CI settings attempted to read .env")
environ.Env.read_env = reject_read_env
""",
    )

    assert result.returncode == 0, result.stderr


def test_ci_settings_require_database_url():
    result = run_settings_import(
        "backend.settings.ci",
        environment={"DJANGO_SETTINGS_MODULE": "backend.settings.ci"},
    )

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///db.sqlite3",
        "postgres://postgres:test@database.example.com:5432/onecreator_ci",
        "postgres://postgres:test@testserver:5432/onecreator_ci",
    ],
)
def test_ci_settings_reject_unsafe_database_urls(database_url):
    result = run_settings_import(
        "backend.settings.ci",
        environment={
            "DJANGO_SETTINGS_MODULE": "backend.settings.ci",
            "DATABASE_URL": database_url,
        },
    )

    assert result.returncode != 0
    assert "DATABASE_URL" in result.stderr


def test_ci_settings_are_hermetic_for_postgresql():
    result = run_settings_import(
        "backend.settings.ci",
        environment={
            "DJANGO_SETTINGS_MODULE": "backend.settings.ci",
            "DATABASE_URL": "postgres://postgres:test@127.0.0.1:5432/onecreator_ci",
        },
    )
    settings = parse_settings_output(result)

    assert settings["database_engine"] == "django.db.backends.postgresql"
    assert settings["debug"] is False
    assert settings["use_r2"] is False
    assert settings["email_backend"] == "django.core.mail.backends.locmem.EmailBackend"
    assert settings["celery_eager"] is True
    assert settings["celery_eager_propagates"] is True
    assert settings["celery_broker"] == "memory://"
    assert settings["celery_backend"] == "cache+memory://"
    assert Path(settings["media_root"]) != BACKEND_ROOT / "media"


def test_ci_settings_allow_ipv6_loopback_for_postgresql():
    result = run_settings_import(
        "backend.settings.ci",
        environment={
            "DJANGO_SETTINGS_MODULE": "backend.settings.ci",
            "DATABASE_URL": "postgres://postgres:test@[::1]:5432/onecreator_ci",
        },
    )
    settings = parse_settings_output(result)

    assert settings["database_engine"] == "django.db.backends.postgresql"


def test_production_settings_compose_the_base_configuration():
    result = run_settings_import(
        "backend.settings.prod",
        environment={
            "DJANGO_SETTINGS_MODULE": "backend.settings.prod",
            "SECRET_KEY": "ci-only-not-a-secret",
            "DATABASE_URL": "postgres://postgres:test@127.0.0.1:5432/onecreator_ci",
            "ALLOWED_HOSTS": "localhost",
            "CORS_ALLOWED_ORIGINS": "https://example.invalid",
            "USE_R2": "True",
        },
    )
    settings = parse_settings_output(result)

    assert settings["installed_apps"] is True
    assert settings["middleware"] is True
    assert settings["database_engine"] == "django.db.backends.postgresql"
    assert settings["secret_key"] is True
    assert settings["root_urlconf"] == "backend.urls"
    assert settings["debug"] is False
    assert settings["use_r2"] is True
