from .base import *


DEBUG = True
ALLOWED_HOSTS = ["*"]

# Base de datos local (usa env.db de base.py por defecto)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
