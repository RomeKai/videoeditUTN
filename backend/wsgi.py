import os
from django.core.wsgi import get_wsgi_application

# CAMBIA A 'backend.settings.prod'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings.prod')

application = get_wsgi_application()