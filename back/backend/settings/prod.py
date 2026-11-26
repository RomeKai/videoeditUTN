import logging
import os

from django.conf import global_settings
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import REST_FRAMEWORK, env

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
# The change of email (apps.users.email_verification) and the alerts to ADMINS
# send through this. Without it Django tries localhost:25: the change answers
# an error and nothing is sent. The links open FRONTEND_URL.
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

# --- SEGURIDAD (Hardening) ---
# Django corre detrás de un reverse proxy del host que termina el TLS (ADR-006,
# documents/runbooks/deploy-vps.md). El proxy tiene que pisar X-Forwarded-Proto
# con el esquema real: Django confía en ese header para saber si el pedido es HTTPS.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# False solo para probar sin certificado (p. ej. un staging por IP).
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=True)
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=True)
SECURE_CONTENT_TYPE_NOSNIFF = True
# HSTS arranca en 0: una vez activado, los navegadores no vuelven a HTTP hasta
# que vence. Subirlo (p. ej. 31536000) cuando el dominio y el certificado estén firmes.
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)

# --- ARCHIVOS ESTÁTICOS (WhiteNoise) ---
# gunicorn sirve los estáticos del admin y de la documentación de la API desde
# STATIC_ROOT (lo llena collectstatic al arrancar web). Los videos van a R2 por
# storage_service, no por el storage default, que queda como en base.py.
MIDDLEWARE = [*MIDDLEWARE]  # noqa: F405
MIDDLEWARE.insert(
    MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
    'whitenoise.middleware.WhiteNoiseMiddleware',
)
STORAGES = {
    **global_settings.STORAGES,
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

# --- CACHE ---
# Without CACHE_REDIS_URL, base.py leaves a LocMem cache per process: each
# gunicorn worker and container keeps its own plans list, throttles and locks.
# Logged now, while the settings load (before LOGGING applies: it goes to stderr).
if 'CACHE_REDIS_URL' not in os.environ:
    logging.getLogger('backend.settings').warning(
        "cache.not_shared CACHE_REDIS_URL is not set: CACHES falls back to a LocMemCache per process"
    )
