import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env


DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ImproperlyConfigured(
        "DATABASE_URL is required when using backend.settings.ci."
    )

parsed_database_url = urlparse(DATABASE_URL)
if parsed_database_url.scheme not in {"postgres", "postgresql"}:
    raise ImproperlyConfigured(
        "DATABASE_URL for CI must use PostgreSQL, not SQLite or another engine."
    )

allowed_database_hosts = {"localhost", "127.0.0.1", "::1"}
if parsed_database_url.hostname not in allowed_database_hosts:
    raise ImproperlyConfigured(
        "DATABASE_URL for CI must target an explicitly allowed test host: "
        "localhost, 127.0.0.1, or ::1."
    )

DATABASES = {"default": env.db("DATABASE_URL")}
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("DATABASE_URL for CI must configure PostgreSQL.")

DEBUG = False
SECRET_KEY = "ci-only-not-a-secret"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

USE_S3 = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "onecreator-ci",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

MEDIA_ROOT = Path(
    os.environ.get(
        "CI_MEDIA_ROOT",
        Path(tempfile.gettempdir()) / "onecreator-ci-media",
    )
)

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
