import logging
import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import PAYMENTS_ALLOW_SIMULATED, PAYMENTS_PROVIDER, REST_FRAMEWORK, env

# --- MODO PRODUCCIÓN ---
# ¡CRÍTICO! Nunca True en producción, o te pueden robar las claves.
DEBUG = False

# Leer hosts permitidos del .env (Ej: onecreator.app, api.onecreator.app)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

# --- CORS (Restrictivo) ---
# Solo permitir peticiones desde tu dominio frontend real
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])

# --- PROXIES ---
# Required: the per-IP attempt limits (login, refresh) read the client's
# address from X-Forwarded-For, NUM_PROXIES hops from the end. A wrong value
# lets a client forge its address (too high) or puts every user behind the
# same proxy in one bucket (0 behind a proxy). See back/.env.example.
if 'NUM_PROXIES' not in os.environ:
    raise ImproperlyConfigured(
        'NUM_PROXIES is required in production: the number of proxies in front of Django '
        'that add the client address to X-Forwarded-For (0 if Django gets the requests directly).'
    )
REST_FRAMEWORK = {**REST_FRAMEWORK, 'NUM_PROXIES': env.int('NUM_PROXIES')}

# --- EMAIL ---
# The account emails (sign-up, change of email, password reset:
# apps.users.email_verification) and the alerts to ADMINS send through this.
# Without it Django tries localhost:25: the sign-up and the change answer an
# error and nothing is sent. The links open FRONTEND_URL.
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_USE_SSL = env.bool('EMAIL_USE_SSL', default=False)
# Seconds an SMTP call may take before the request gives up.
EMAIL_TIMEOUT = env.int('EMAIL_TIMEOUT', default=10)
_missing_email = [name for name in ('FRONTEND_URL', 'DEFAULT_FROM_EMAIL') if not os.environ.get(name)]
if EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend' and not EMAIL_HOST:
    _missing_email.append('EMAIL_HOST')
if _missing_email:
    raise ImproperlyConfigured(
        f"Email is not configured for production: set {', '.join(_missing_email)} (see back/.env.example)."
    )

# --- PAGOS ---
# The simulated provider approves payments nobody made and credits their tokens.
# Production never runs it by accident: only a demo that asks for it with
# PAYMENTS_ALLOW_SIMULATED=True, and then it is logged at every start.
if PAYMENTS_PROVIDER == 'simulated':
    if not PAYMENTS_ALLOW_SIMULATED:
        raise ImproperlyConfigured(
            'PAYMENTS_PROVIDER=simulated approves payments without charging them. In production it needs '
            'PAYMENTS_ALLOW_SIMULATED=True (a demo only); leave PAYMENTS_PROVIDER empty otherwise.'
        )
    logging.getLogger('backend.settings').warning(
        'payments.simulated_provider_on PAYMENTS_ALLOW_SIMULATED is set: payments are approved without charging'
    )

# --- SEGURIDAD (Hardening) ---
# Forzar HTTPS (Descomentar cuando tengas certificado SSL en el server)
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# SECURE_BROWSER_XSS_FILTER = True
# SECURE_CONTENT_TYPE_NOSNIFF = True

# --- ALMACENAMIENTO ESTÁTICO (WhiteNoise / S3) ---
# En producción, Django no sirve archivos estáticos eficientemente.
# Opción A: WhiteNoise (Lo más fácil para empezar en Docker)
# INSTALLED_APPS += ["whitenoise.runserver_nostatic"]
# MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Opción B: AWS S3 (Para cuando tengas el bucket creado)
# if env('AWS_ACCESS_KEY_ID', default=None):
#     DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
#     STATICFILES_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# --- CACHE ---
# Without CACHE_REDIS_URL, base.py leaves a LocMem cache per process: each
# gunicorn worker and container keeps its own plans list, throttles and locks.
# Logged now, while the settings load (before LOGGING applies: it goes to stderr).
if 'CACHE_REDIS_URL' not in os.environ:
    logging.getLogger('backend.settings').warning(
        "cache.not_shared CACHE_REDIS_URL is not set: CACHES falls back to a LocMemCache per process"
    )
