"""
Django settings for MediaConverter project.
Optimized for Render.com deployment.
"""

import os
from pathlib import Path

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent

# Security settings
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-y+g_o71o9$l0my@03v%bgl(0!iipbqde-5l0yf1iy^8!2uhf&h')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

# Render.com specific
RENDER = os.getenv('RENDER', 'False') == 'True'
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')

# Allowed hosts
ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'converter',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'MediaConverter.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
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

# ASGI Application
ASGI_APPLICATION = 'MediaConverter.asgi.application'
WSGI_APPLICATION = 'MediaConverter.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files
MEDIA_URL = '/media/'
# MEDIA_ROOT = BASE_DIR / 'media'
# Use /tmp on Render (writable), /app/media locally
if RENDER or RENDER_EXTERNAL_HOSTNAME:
    MEDIA_ROOT = '/tmp/media'
    print("[INFO] Using /tmp/media for file storage (Render deployment)")
else:
    MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
    print(f"[INFO] Using {MEDIA_ROOT} for file storage (local development)")

# Ensure media directory exists with proper permissions
try:
    os.makedirs(MEDIA_ROOT, mode=0o777, exist_ok=True)
    print(f"[SUCCESS] Media directory created/verified: {MEDIA_ROOT}")
    print(f"[INFO] Media directory writable: {os.access(MEDIA_ROOT, os.W_OK)}")
except Exception as e:
    print(f"[ERROR] Failed to create media directory: {e}")

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========== REDIS & CELERY CONFIGURATION ==========
# Try to get Redis URL from environment
REDIS_URL = os.environ.get('REDIS_URL')

# Print for debugging (will show in Render logs)
print(f"[DEBUG] REDIS_URL: {'SET' if REDIS_URL else 'NOT SET'}")
if REDIS_URL:
    # Mask the URL for security in logs
    masked_url = REDIS_URL.split('@')[0] + '@...' if '@' in REDIS_URL else REDIS_URL[:30] + '...'
    print(f"[DEBUG] Redis URL starts with: {masked_url}")

# Fallback to localhost for local development
if not REDIS_URL:
    if DEBUG:
        print("[WARNING] REDIS_URL not set, using localhost (development only)")
        REDIS_URL = 'redis://localhost:6379/0'
    else:
        print("[ERROR] REDIS_URL not set in production!")
        # Don't raise error yet - let's try to start anyway
        REDIS_URL = 'redis://red-d61lascr85hc739irm3g:6379'

# Parse Redis URL for Render
if REDIS_URL.startswith('rediss://'):
    # Render uses rediss:// for SSL connections
    import ssl
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    CELERY_BROKER_USE_SSL = {'ssl_cert_reqs': ssl.CERT_NONE}
    CELERY_REDIS_BACKEND_USE_SSL = {'ssl_cert_reqs': ssl.CERT_NONE}
    
    # Channels SSL config
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [{
                    "address": REDIS_URL,
                }],
                "capacity": 1500,
                "expiry": 10,
            },
        },
    }
else:
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL
    
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
                "capacity": 1500,
                "expiry": 10,
            },
        },
    }

# Celery configuration
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_RESULT_EXPIRES = 3600  # 1 hour
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1

# Celery retry settings
CELERY_BROKER_CONNECTION_RETRY = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_MAX_RETRIES = 10

# ========== VIDEO CONVERTER SETTINGS ==========
MAX_VIDEO_DURATION = int(os.getenv('MAX_VIDEO_DURATION', '600'))  # 10 minutes
AUDIO_QUALITY = os.getenv('AUDIO_QUALITY', '192')
AUDIO_FORMAT = os.getenv('AUDIO_FORMAT', 'mp3')
FILE_CLEANUP_AFTER = int(os.getenv('FILE_CLEANUP_AFTER', '1800'))  # 30 minutes

# ========== ASGI/DAPHNE TIMEOUTS ==========
ASGI_THREADS = 2
ASGI_APPLICATION_CLOSE_TIMEOUT = 5

# ========== CSRF CONFIGURATION ==========
CSRF_TRUSTED_ORIGINS = [
    'https://media-wapn.onrender.com',
    'https://*.onrender.com',
]

if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f'https://{RENDER_EXTERNAL_HOSTNAME}')

CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_USE_SESSIONS = False

# ========== SECURITY SETTINGS ==========
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}