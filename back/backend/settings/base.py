from pathlib import Path
import os
import environ

# 1. Path Configuration
# Located in backend/settings/base.py, move up 3 levels to reach the root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 2. Initialize Environ
env = environ.Env()
# Read .env file from root
if os.environ.get('DJANGO_SETTINGS_MODULE') != 'backend.settings.ci':
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

# The web app sends the active workspace in X-Workspace-Id (apps.core.workspace):
# without it in the list, the browser's preflight drops every scoped request.
from corsheaders.defaults import default_headers  # noqa: E402

# The web app sends the active workspace on every scoped request, and an
# Idempotency-Key on writes that must not run twice when retried (publishing).
# Without them in this list the browser's preflight blocks those requests.
CORS_ALLOW_HEADERS = (*default_headers, 'x-workspace-id', 'idempotency-key')

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
    # drf-spectacular's AutoSchema, plus the X-Workspace-Id header on scoped views.
    'DEFAULT_SCHEMA_CLASS': 'apps.core.schema.WorkspaceAutoSchema',
}

# --- AI CONFIG & FEATURE FLAGS ---
AI_CORE_V2_ENABLED = env.bool('AI_CORE_V2_ENABLED', default=False)
# Gradual enablement (AICORE-9): ids of the users (VideoProject.uploaded_by)
# that get V2 while the master switch is on; empty = everyone. The master
# switch stays the kill switch.
AI_CORE_V2_ALLOWLIST = env.list('AI_CORE_V2_ALLOWLIST', default=[])

# LiteLLM reads model capabilities (reasoning support, prices) from its bundled map
# instead of downloading the latest one on import: the map is pinned with the
# library version, and importing never depends on the network (AICORE-14).
os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')

# Provider Credentials (obtained exclusively from environment variables)
GEMINI_API_KEY = env('GEMINI_API_KEY', default=None)
GROQ_API_KEY = env('GROQ_API_KEY', default=None)
XAI_API_KEY = env('XAI_API_KEY', default=None)

# OpenAI — Moderation API (AI_Security_Shield) and LLM fallback for clip selection
OPENAI_API_KEY = env('OPENAI_API_KEY', default=None)
# Hard timeout (seconds) for the Moderation call. The shield fails closed: a timeout blocks the
# content and the task retries with backoff; SDK-level retries are disabled (Celery owns retries).
MODERATION_TIMEOUT_SECONDS = env.float('MODERATION_TIMEOUT_SECONDS', default=10.0)
# ADR-011: the whole text is moderated in blocks of this size (D-M1), with a pinned model
# (D-M6). Flagged categories in the non-blocking list are only measured; any other flagged
# category blocks, including ones the API adds later (D-M5, fail-closed).
MODERATION_MODEL = env('MODERATION_MODEL', default='omni-moderation-latest')
MODERATION_BLOCK_CHARS = env.int('MODERATION_BLOCK_CHARS', default=10000)
MODERATION_NON_BLOCKING_CATEGORIES = env.list(
    'MODERATION_NON_BLOCKING_CATEGORIES',
    default=['harassment', 'violence', 'self-harm', 'illicit'],
)

# AI Core V2 Model Configuration
AI_DEFAULT_LLM_MODEL = env('AI_DEFAULT_LLM_MODEL', default='gemini/gemini-3.8-flash')
# Fallback is same-provider (Gemini Flash-Lite) and TEMPORARY: it covers model-level failures but not a Gemini
# outage, quota or key problem. Objective: cross-provider; candidate groq/openai/gpt-oss-120b, gated by the
# org's real Groq TPM limits. Llama models on Groq only support json_object and are unpriced. The fallback
# only activates if that provider's key is set.
AI_FALLBACK_LLM_MODEL = env('AI_FALLBACK_LLM_MODEL', default='gemini/gemini-3.5-flash-lite')
# Hard timeout per LLM call; retries are owned by Celery, not LiteLLM.
AI_LLM_TIMEOUT_SECONDS = env.float('AI_LLM_TIMEOUT_SECONDS', default=60.0)
# ADR-012 / AICORE-14: caps the selection model's hidden reasoning (billed as output).
# minimal | low | medium | high; empty keeps the provider default. Only sent to models
# LiteLLM knows can reason (see services/ai/llm_options.py).
AI_LLM_REASONING_EFFORT = env('AI_LLM_REASONING_EFFORT', default='')
# Transcript cap sized for the smallest context window plausible in the chain (gpt-4o-mini, 128K tokens),
# so the same cap stays valid if a cross-provider fallback is configured.
AI_LLM_MAX_TRANSCRIPT_CHARS = env.int('AI_LLM_MAX_TRANSCRIPT_CHARS', default=400000)
# Clip boundaries (AICORE-16): with word timestamps, clips are snapped to whole
# words and kept within [MIN, MAX] seconds at natural boundaries (a pause of at
# least PAUSE seconds between words, or a sentence end). See clip_validation.py.
AI_CLIP_MIN_SECONDS = env.float('AI_CLIP_MIN_SECONDS', default=15.0)
AI_CLIP_MAX_SECONDS = env.float('AI_CLIP_MAX_SECONDS', default=60.0)
AI_CLIP_SNAP_TOLERANCE_SECONDS = env.float('AI_CLIP_SNAP_TOLERANCE_SECONDS', default=1.0)
AI_CLIP_PAUSE_SECONDS = env.float('AI_CLIP_PAUSE_SECONDS', default=0.5)
AI_DEFAULT_TRANSCRIPTION_MODEL = env('AI_DEFAULT_TRANSCRIPTION_MODEL', default='whisper-large-v3-turbo')
# Timeout of each Groq transcription request, i.e. one audio chunk (up to 24 MB
# of FLAC or a 600 s window). httpx applies it to each phase (connect, upload,
# wait for the answer). SDK-level retries are disabled: Celery owns retries, and
# a timeout is a retryable AITimeoutError whose billing is uncertain.
AI_TRANSCRIPTION_TIMEOUT_SECONDS = env.float('AI_TRANSCRIPTION_TIMEOUT_SECONDS', default=120.0)

# Transcription backend when AI_CORE_V2_ENABLED=True: 'groq' (default) | 'local'.
# There is no automatic fallback between backends (ADR-008): Groq failures are
# retried by Celery; 'local' loads openai-whisper and is meant for dev/rollback only.
TRANSCRIPTION_BACKEND = env('TRANSCRIPTION_BACKEND', default='groq')

# Local Whisper (legacy backend) on the worker (CORE-04).
# TRANSCRIPTION_DEVICE: 'auto' uses CUDA when torch sees a GPU, else CPU; 'cpu' or
# 'cuda' force it. fp16 inference is enabled only on CUDA.
TRANSCRIPTION_DEVICE = env('TRANSCRIPTION_DEVICE', default='auto')
# Load the local Whisper model once per worker process at startup (only when the
# local backend is active), instead of on the first task that needs it.
WHISPER_PRELOAD = env.bool('WHISPER_PRELOAD', default=True)
WHISPER_LOCAL_MODEL_SIZE = env('WHISPER_LOCAL_MODEL_SIZE', default='base')
# Prompt-to-Edit (PTE-01a): longest request sent to the parser; longer text is cut.
AI_PROMPT_MAX_CHARS = env.int('AI_PROMPT_MAX_CHARS', default=1000)

# Upload validation (CORE-05, #12): limits of the pipeline itself, checked with
# ffprobe before the project is saved. The plan limits stay in PricingEngine.
AI_UPLOAD_MAX_DURATION_SECONDS = env.int('AI_UPLOAD_MAX_DURATION_SECONDS', default=10800)
AI_UPLOAD_MAX_LONG_SIDE_PX = env.int('AI_UPLOAD_MAX_LONG_SIDE_PX', default=4096)
AI_UPLOAD_MIN_SHORT_SIDE_PX = env.int('AI_UPLOAD_MIN_SHORT_SIDE_PX', default=240)
# "Editar el video completo" (single_edit): the whole video is one clip rendered
# by one task, so it is for short videos.
AI_SINGLE_EDIT_MAX_DURATION_SECONDS = env.int('AI_SINGLE_EDIT_MAX_DURATION_SECONDS', default=600)

# --- PLANS (PAY-03) ---
# The plan a new workspace starts on (users/signals.py, via
# apps.payments.services.plans.get_default_plan_id). If no active plan has this
# name, the workspace is created without one and it is logged
# (`plans.default_plan_missing` or `plans.default_plan_inactive`);
# `manage.py assign_default_plan` assigns it later.
DEFAULT_PLAN_NAME = env('DEFAULT_PLAN_NAME', default='Gratis')

# JWT Configuration
from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=25),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=3),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# --- LOGGING (INFRA-02) ---
# LOG_FORMAT 'json' writes one JSON object per line (apps.core.structured_logging),
# for a log platform to index; 'text' keeps the readable console format for
# development. LOG_SERVICE tags every line (web, worker, beat).
LOG_FORMAT = env('LOG_FORMAT', default='text' if DEBUG else 'json')
LOG_LEVEL = env('LOG_LEVEL', default='INFO')
LOG_SERVICE = env('LOG_SERVICE', default='')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'celery_task': {'()': 'apps.core.structured_logging.CeleryTaskContextFilter'},
    },
    'formatters': {
        'json': {
            '()': 'apps.core.structured_logging.JsonFormatter',
            'service': LOG_SERVICE,
        },
        'text': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json' if LOG_FORMAT == 'json' else 'text',
            'filters': ['celery_task'],
        },
    },
    'root': {'handlers': ['console'], 'level': LOG_LEVEL},
    'loggers': {
        # Django logs every request at INFO through django.server/django.request;
        # WARNING keeps the 4xx/5xx and drops the noise.
        'django': {'level': 'WARNING'},
        'celery': {'level': LOG_LEVEL},
    },
}
# Celery must not replace the root handlers with its own text format.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# Alert when a Celery task fails this many times in a row (apps.core.task_alerts):
# CRITICAL log alert.task_failing and an email to ADMINS. 0 disables it.
TASK_FAILURE_ALERT_THRESHOLD = env.int('TASK_FAILURE_ALERT_THRESHOLD', default=3)
# ADMIN_EMAILS: comma-separated addresses that receive the alerts.
ADMINS = [(email, email) for email in env.list('ADMIN_EMAILS', default=[])]
SERVER_EMAIL = env('SERVER_EMAIL', default='alertas@onecreator.local')

# --- CELERY CONFIG ---
if 'REDIS_URL' in os.environ:
    CELERY_BROKER_URL = env('REDIS_URL')
    CELERY_RESULT_BACKEND = env('REDIS_URL')

# Redis used for the distributed pipeline lock (same instance as the broker).
REDIS_URL = env('REDIS_URL', default='redis://redis:6379/0')
# Short TTL on purpose: the lock is renewed by a heartbeat while its worker is
# alive, so the TTL only decides how long a crashed worker's lock keeps the
# redelivered task waiting (it retries, see PIPELINE_LOCK_* in tasks.py).
PIPELINE_LOCK_TTL = env.int('PIPELINE_LOCK_TTL', default=300)

# --- CACHE ---
# One store for every web and worker process (documents/ia/07, "Cache
# compartida"): the public plans list, throttles and the cache.add locks only
# work if all of them see the same keys. Django's default LocMem is one cache
# per process. CACHE_REDIS_URL is its own Redis database, never the broker's:
# django-redis runs cache.clear() as FLUSHDB, whatever the key prefix, and
# that would also empty the Celery queues, the project locks and the capacity
# buckets. Two aliases (apps/core/cache.py):
# - default fails open (a Redis that does not answer is a cache miss) and logs
#   cache.unavailable: plans, throttles, probe caches.
# - locks raises: a lock that cannot be taken stops the caller.
# Without CACHE_REDIS_URL (a runserver with no Redis) both are LocMem, and
# prod.py warns.
CACHE_KEY_PREFIX = env('CACHE_KEY_PREFIX', default='onecreator')
if 'CACHE_REDIS_URL' in os.environ:
    CACHE_REDIS_URL = env('CACHE_REDIS_URL')
    _CACHE_REDIS_OPTIONS = {
        # A Redis that does not answer fails the call in seconds instead of
        # holding the request or the task.
        'SOCKET_CONNECT_TIMEOUT': 2,
        'SOCKET_TIMEOUT': 2,
    }
    CACHES = {
        'default': {
            'BACKEND': 'apps.core.cache.FailOpenRedisCache',
            'LOCATION': CACHE_REDIS_URL,
            'KEY_PREFIX': CACHE_KEY_PREFIX,
            'TIMEOUT': 300,
            'OPTIONS': {**_CACHE_REDIS_OPTIONS, 'IGNORE_EXCEPTIONS': True},
        },
        'locks': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': CACHE_REDIS_URL,
            'KEY_PREFIX': f'{CACHE_KEY_PREFIX}:locks',
            'TIMEOUT': 300,
            'OPTIONS': dict(_CACHE_REDIS_OPTIONS),
        },
    }
else:
    CACHE_REDIS_URL = None
    CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'onecreator'},
        'locks': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'onecreator-locks'},
    }


def _redis_database(url):
    """(host, port, db) of a redis:// URL, to tell whether two URLs are the same database."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return (parsed.hostname, parsed.port or 6379, (parsed.path or '/0').strip('/') or '0')


if CACHE_REDIS_URL and _redis_database(CACHE_REDIS_URL) == _redis_database(REDIS_URL):
    import logging as _logging

    # Logged while the settings load (before LOGGING applies: it goes to stderr).
    _logging.getLogger('backend.settings').warning(
        "cache.shares_broker_db CACHE_REDIS_URL is the broker's Redis database: cache.clear() would empty its queues"
    )

# Redis broker visibility timeout. Tasks use acks_late, so a running task stays
# unacked and Redis redelivers it once this timeout passes. It must be comfortably
# greater than PIPELINE_LOCK_TTL plus the longest stage (long-video transcription,
# heavy renders); the default of 1 hour would redeliver still-running tasks.
# Trade-off: if a whole worker host dies, its unacked tasks come back only after
# this timeout (the lock TTL, not this value, governs same-host crash recovery).
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': env.int('CELERY_VISIBILITY_TIMEOUT', default=4 * 3600),
}

# Ceiling for one ingestion run (download, transcription, selection). The lock
# heartbeat keeps a hung worker's lock alive forever, so without a limit a stuck
# FFmpeg/provider call would block the project for good. The soft limit raises
# SoftTimeLimitExceeded inside the task: the project fails with that static code
# and the lock is released. The hard limit only kills a worker that ignored it.
# Both must stay below CELERY_VISIBILITY_TIMEOUT.
PIPELINE_SOFT_TIME_LIMIT = env.int('PIPELINE_SOFT_TIME_LIMIT', default=2 * 3600)
PIPELINE_TIME_LIMIT = env.int('PIPELINE_TIME_LIMIT', default=PIPELINE_SOFT_TIME_LIMIT + 300)
# Ceiling for one paper-edit render (render_video_segments). The soft limit is a
# final failure: the project fails and its render reservation is returned. The
# hard limit only kills a worker that ignored it: Celery acks that message, so
# nothing redelivers it and recover_stuck_renders picks the project up. Both
# must stay below CELERY_VISIBILITY_TIMEOUT.
RENDER_SEGMENTS_SOFT_TIME_LIMIT = env.int('RENDER_SEGMENTS_SOFT_TIME_LIMIT', default=3 * 3600)
RENDER_SEGMENTS_TIME_LIMIT = env.int(
    'RENDER_SEGMENTS_TIME_LIMIT', default=RENDER_SEGMENTS_SOFT_TIME_LIMIT + 300,
)
# Most starts of one clip render (render_clip_task) whose delivery never ended,
# counting the one about to start. A clip that always kills its worker (an OOM
# in MoviePy) is redelivered by acks_late; past this many it fails instead of
# coming back forever. A start that ended (delivered, or an error handled with
# a retry or a final failure) does not count, so transient retries never use
# it up. At least 2: with 1, a single broker blip (a redelivered copy taking
# over a live render) would fail a healthy clip.
RENDER_CLIP_MAX_STARTS = env.int('RENDER_CLIP_MAX_STARTS', default=3)
# Ceiling for one clip render (render_clip_task). The soft limit fails the
# clip for good and clears its claim, so a hung render never keeps it. The
# hard limit only kills a worker that ignored it: Celery acks that message and
# recover_stuck_renders takes the clip over. Fixed, like the other tasks (none
# scales its limit to its input). Both stay below RENDER_STUCK_AFTER_SECONDS
# (enforced there) and CELERY_VISIBILITY_TIMEOUT.
RENDER_CLIP_SOFT_TIME_LIMIT = env.int('RENDER_CLIP_SOFT_TIME_LIMIT', default=2 * 3600)
RENDER_CLIP_TIME_LIMIT = env.int('RENDER_CLIP_TIME_LIMIT', default=RENDER_CLIP_SOFT_TIME_LIMIT + 300)
# Worst render seconds per second of clip (4K, face tracking and the active
# speaker layout on CPU). The approval refuses a segment longer than
# RENDER_CLIP_SOFT_TIME_LIMIT over this (1800 s by default): it would hit the
# soft limit and fail after its tokens were reserved. Never below 1.
RENDER_CLIP_SECONDS_PER_SECOND = env.float('RENDER_CLIP_SECONDS_PER_SECOND', default=4.0)
# A render whose reservation is still RESERVED (a paper edit, or a render clip
# by clip), with no project lock held and no start, approval or re-queue for
# this long, is stuck: recover_stuck_renders re-queues it, charges it
# (delivered) or, for a paper edit that lost its worker too many times, fails
# it and returns the reservation. Never below the longer hard limit
# (RENDER_SEGMENTS_TIME_LIMIT, RENDER_CLIP_TIME_LIMIT) + 15 min, whatever the
# environment says: a render that started less than its hard limit ago may
# still be running. The environment can only raise it.
RENDER_STUCK_AFTER_SECONDS = max(
    env.int('RENDER_STUCK_AFTER_SECONDS', default=0),
    max(RENDER_SEGMENTS_TIME_LIMIT, RENDER_CLIP_TIME_LIMIT) + 15 * 60,
)

# Heavy MoviePy renders get their own queue so they cannot starve ingestion.
# Workers must consume both queues: `celery -A backend worker -Q celery,render`.
CELERY_TASK_ROUTES = {
    'apps.videos.tasks.render_clip_task': {'queue': 'render'},
    # The paper-edit render is as heavy as a clip render (MoviePy, face tracking).
    'apps.videos.tasks.render_video_segments': {'queue': 'render'},
}

# A worker reserves only the task it is running: long ingestion tasks are not
# hoarded by a worker that cannot start them (AICORE-11).
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# Worker recycling (CORE-04). MoviePy/FFmpeg/Whisper leave memory behind that a
# long-lived pool process never returns, so each child is replaced after
# MAX_TASKS tasks, or after the task during which it went over MAX_MEMORY
# (KiB; 0 disables it). Both checks run between tasks, never interrupting one.
CELERY_WORKER_MAX_TASKS_PER_CHILD = env.int('CELERY_WORKER_MAX_TASKS_PER_CHILD', default=50)
CELERY_WORKER_MAX_MEMORY_PER_CHILD = env.int('CELERY_WORKER_MAX_MEMORY_PER_CHILD', default=0) or None

# Clip previews (PTE-03): free, temporary, low-resolution renders of a clip.
# Each one expires PREVIEW_TTL_SECONDS after it is ready; a workspace can have
# at most PREVIEW_MAX_ACTIVE_PER_WORKSPACE previews pending at the same time
# (they cost compute but no tokens). Expired ones are deleted by the
# cleanup_expired_previews task (Celery Beat) and on every new request.
PREVIEW_HEIGHT = env.int('PREVIEW_HEIGHT', default=360)
PREVIEW_TTL_SECONDS = env.int('PREVIEW_TTL_SECONDS', default=3600)
PREVIEW_MAX_ACTIVE_PER_WORKSPACE = env.int('PREVIEW_MAX_ACTIVE_PER_WORKSPACE', default=3)
CELERY_BEAT_SCHEDULE = {
    'cleanup-expired-previews': {
        'task': 'apps.videos.tasks.cleanup_expired_previews',
        'schedule': 15 * 60,
    },
    # Paper edits left RENDERING with their tokens reserved (a hard time limit,
    # a lost redelivery). Without a beat process, the same routine runs from
    # cron: manage.py recover_stuck_renders --apply.
    'recover-stuck-renders': {
        'task': 'apps.videos.tasks.recover_stuck_renders',
        'schedule': 15 * 60,
    },
}

# --- AI capacity (AICORE-11, ADR-012 D-C4) ---
# Token buckets in Valkey reserved before every Groq/LLM call of the ingestion
# pipeline. Off until the limits of the environment are configured (runbook).
# AI_CAPACITY_LIMITS (JSON): {"<model>": {"<resource>_per_<window>": limit}},
# resources audio_seconds | tokens | requests, windows minute | hour | day,
# set BELOW the provider's limits (e.g. 80 %). Model keys: the LiteLLM model
# string, and "groq/<model>" for transcription.
AI_CAPACITY_ENABLED = env.bool('AI_CAPACITY_ENABLED', default=False)
AI_CAPACITY_LIMITS = env.json('AI_CAPACITY_LIMITS', default={})
# Longest a project may wait for capacity per stage before failing with
# CapacityTimeoutError; each re-queue waits at most AI_CAPACITY_MAX_COUNTDOWN_SECONDS.
AI_CAPACITY_MAX_WAIT_SECONDS = env.int('AI_CAPACITY_MAX_WAIT_SECONDS', default=3600)
AI_CAPACITY_MAX_COUNTDOWN_SECONDS = env.int('AI_CAPACITY_MAX_COUNTDOWN_SECONDS', default=300)
# Selection reservation estimate: input from the audio length (G0: ~456 tokens
# per minute) plus the prompt and the output (visible + reasoning); settled
# with the real usage after the call.
AI_CAPACITY_LLM_TOKENS_PER_AUDIO_MINUTE = env.int('AI_CAPACITY_LLM_TOKENS_PER_AUDIO_MINUTE', default=456)
AI_CAPACITY_LLM_PROMPT_TOKENS = env.int('AI_CAPACITY_LLM_PROMPT_TOKENS', default=1500)
AI_CAPACITY_LLM_OUTPUT_TOKENS = env.int('AI_CAPACITY_LLM_OUTPUT_TOKENS', default=4000)

# --- Social metadata per post (AICORE-17, ADR-013) ---
# Language of the caption, and the limit of caption + hashtags per platform.
AI_SEO_LANGUAGE = env('AI_SEO_LANGUAGE', default='es')
AI_SEO_CAPTION_MAX_CHARS = env.json('AI_SEO_CAPTION_MAX_CHARS', default={
    'TIKTOK': 2200, 'INSTAGRAM_REELS': 2200, 'YOUTUBE_SHORTS': 5000,
})

SPECTACULAR_SETTINGS = {
    'TITLE': 'OneCreator API',
    'DESCRIPTION': 'API for automated AI video management and editing.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    # SubscriptionPlan.max_resolution and the projects' render_resolution share
    # their tiers: one enum instead of MaxResolutionEnum and RenderResolutionEnum.
    'ENUM_NAME_OVERRIDES': {
        'ResolutionEnum': 'apps.payments.models.RESOLUTION_CHOICES',
    },
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
USE_R2 = env.bool('USE_R2', default=False)

# Custom Endpoint for R2
CLOUDFLARE_R2_ENDPOINT_URL = f"https://{CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if CLOUDFLARE_R2_ACCOUNT_ID else None

# --- ASSETS ---
FONTS_DIR = os.path.join(BASE_DIR, 'assets', 'fonts')

AYRSHARE_API_KEY = env('AYRSHARE_API_KEY', default=None)
