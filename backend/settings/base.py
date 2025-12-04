from pathlib import Path
import os
import environ

# 1. Configuración de Rutas
# Estamos en backend/settings/base.py, así que subimos 3 niveles para llegar a la raíz
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 2. Inicializar Environ
env = environ.Env()
# Leer el archivo .env de la raíz
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# --- SEGURIDAD ---
SECRET_KEY = env('SECRET_KEY', default='django-insecure-clave-temporal-dev')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

# --- APLICACIONES (Modular) ---
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'corsheaders',
    # 'django_celery_results', # Descomentar cuando configures Celery
    # 'drf_yasg',              # Descomentar cuando instales Swagger
]

# Estas carpetas deben existir en tu directorio 'apps/'
# backend/settings/base.py

LOCAL_APPS = [
    'apps.core',
    'apps.users',  # <-- Simple y directo, ahora que la carpeta está bien ubicada
    'apps.videos',
    'apps.ia',
    'apps.payments',
    'apps.integrations',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# --- MIDDLEWARE ---
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware', # CORS debe ir antes de Common
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'
WSGI_APPLICATION = 'backend.wsgi.application'

# --- TEMPLATES ---
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True, # Busca templates dentro de cada app
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# --- BASE DE DATOS (Inteligente) ---
# Si en el .env hay DATABASE_URL, usa eso (Postgres).
# Si no hay, usa SQLite automáticamente (ideal para probar ahora mismo).
DATABASES = {
    'default': env.db('DATABASE_URL', default='sqlite:///db.sqlite3')
}

# --- PASSWORD VALIDATION ---
AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

# --- INTERNATIONALIZATION ---
LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# --- STATIC & MEDIA ---
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- USUARIO PERSONALIZADO ---
# IMPORTANTE: Mantén esto comentado hasta que crees el modelo User en apps/users/models.py
AUTH_USER_MODEL = 'users.User' 

# --- DRF CONFIG ---
# backend/settings/base.py

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated', # Por defecto, todo privado
    ),'DEFAULT_PAGINATION_CLASS': None,
}

# Configuración de JWT (Tiempos de vida del token)
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=25),  # El token de uso dura 1 hora
    'REFRESH_TOKEN_LIFETIME': timedelta(days=3),     # El de refresco dura 1 día
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# --- CELERY CONFIG (Leída de env) ---
if 'REDIS_URL' in os.environ:
    CELERY_BROKER_URL = env('REDIS_URL')
    CELERY_RESULT_BACKEND = env('REDIS_URL')