import os

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
