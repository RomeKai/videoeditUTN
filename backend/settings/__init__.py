import os

# Lee la variable de entorno DJANGO_SETTINGS_MODULE
environment = os.getenv('DJANGO_SETTINGS_MODULE', 'backend.settings.dev')