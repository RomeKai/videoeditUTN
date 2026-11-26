import logging
import os

from .base import *  # noqa: F403
from .base import env

# --- MODO PRODUCCIÓN ---
# ¡CRÍTICO! Nunca True en producción, o te pueden robar las claves.
DEBUG = False

# Leer hosts permitidos del .env (Ej: onecreator.app, api.onecreator.app)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

# --- CORS (Restrictivo) ---
# Solo permitir peticiones desde tu dominio frontend real
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])

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
# Without REDIS_URL, base.py leaves a LocMem cache per process: each gunicorn
# worker and container keeps its own plans list and its own cache.add locks.
# Logged now, while the settings load (before LOGGING applies: it goes to stderr).
if 'REDIS_URL' not in os.environ:
    logging.getLogger('backend.settings').warning(
        "cache.not_shared REDIS_URL is not set: CACHES falls back to a LocMemCache per process"
    )
