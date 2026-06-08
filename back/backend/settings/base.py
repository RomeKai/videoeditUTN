from pathlib import Path
import os
import environ

# 1. Path Configuration
# Located in backend/settings/base.py, move up 3 levels to reach the root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 2. Initialize Environ
env = environ.Env()
# Read .env file from root
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

# --- SECURITY ---
SECRET_KEY = env('SECRET_KEY', default='django-insecure-temp-key-dev')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

# --- APPLICATIONS (Modular) ---
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
    'drf_spectacular',
]

LOCAL_APPS = [
    'apps.core',
    'apps.users',
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
    'corsheaders.middleware.CorsMiddleware', # CORS must be before Common
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
        'APP_DIRS': True, # Search for templates within each app
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

# --- DATABASE ---
# Uses DATABASE_URL (Postgres) if available in .env, defaults to SQLite
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
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# --- STATIC & MEDIA ---
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CUSTOM USER MODEL ---
AUTH_USER_MODEL = 'users.User' 

# --- DRF CONFIG ---
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': None,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# --- AI CONFIG ---
OPENAI_API_KEY = env('OPENAI_API_KEY', default=None)
GEMINI_API_KEY = env('GEMINI_API_KEY', default=None)

# JWT Configuration
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=25),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=3),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# --- CELERY CONFIG ---
if 'REDIS_URL' in os.environ:
    CELERY_BROKER_URL = env('REDIS_URL')
    CELERY_RESULT_BACKEND = env('REDIS_URL')

SPECTACULAR_SETTINGS = {
    'TITLE': 'OneCreator API',
    'DESCRIPTION': 'API for automated AI video management and editing.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': True,
    },
}

# --- STORAGE: CLOUDFLARE R2 (S3 COMPATIBLE) ---
# We use R2 for zero egress fees on heavy video files
CLOUDFLARE_R2_ACCOUNT_ID = env('CLOUDFLARE_R2_ACCOUNT_ID', default=None)
CLOUDFLARE_R2_ACCESS_KEY_ID = env('CLOUDFLARE_R2_ACCESS_KEY_ID', default=None)
CLOUDFLARE_R2_SECRET_ACCESS_KEY = env('CLOUDFLARE_R2_SECRET_ACCESS_KEY', default=None)
CLOUDFLARE_R2_BUCKET_NAME = env('CLOUDFLARE_R2_BUCKET_NAME', default=None)
CLOUDFLARE_R2_REGION = 'auto' # R2 standard

# Custom Endpoint for R2
CLOUDFLARE_R2_ENDPOINT_URL = f"https://{CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if CLOUDFLARE_R2_ACCOUNT_ID else None

# --- ASSETS ---
FONTS_DIR = os.path.join(BASE_DIR, 'assets', 'fonts')
