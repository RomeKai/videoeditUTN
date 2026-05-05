from .base import *

# --- MODO DESARROLLO ---
DEBUG = True
ALLOWED_HOSTS = ["*"]  # Acepta conexiones de cualquier lado (localhost, Docker, Postman)

# --- CORS (Permisos para el Frontend) ---
# En desarrollo, permitimos que cualquiera consulte la API (ej: tu React en localhost:3000)
CORS_ALLOW_ALL_ORIGINS = True

# --- EMAILS ---
# En vez de mandar emails reales, imprímelos en la terminal.
# Útil para probar "Reset Password" sin configurar Gmail/AWS SES.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# --- DEBUG TOOLBAR (Opcional - Preparado para el futuro) ---
# Si decides instalar django-debug-toolbar más adelante, aquí iría su config.
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
# INTERNAL_IPS = ["127.0.0.1", "localhost"]