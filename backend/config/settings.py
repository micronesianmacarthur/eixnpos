"""Django settings for eixnPOS."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'dev-secret-key-change-in-prod'
DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'accounts.admin_config.EixnPOSAdminConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'accounts',
    'users',
    'business',
    'customers',
    'suppliers',
    'products',
    'inventory',
    'sales',
    'sessions',
    'backups',

]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.PasswordChangeRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]
WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'accounts.validators.ReservedPasswordValidator',
    },
    {
        'NAME': 'accounts.validators.CharacterMixValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR.parent / 'frontend' / 'dist'] if (BASE_DIR.parent / 'frontend' / 'dist').exists() else []
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Root directory allowed for database backups. Backups may only be written
# under this root (or an explicitly approved mount in APPROVED_BACKUP_MOUNTS).
BACKUP_ROOT = BASE_DIR / 'data' / 'backups'
# Additional absolute paths (e.g. mounted volumes) approved for backup output.
APPROVED_BACKUP_MOUNTS = []

AUTH_USER_MODEL = 'accounts.User'

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = ['http://localhost:5173', 'http://127.0.0.1:5173']

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# ---------------------------------------------------------------------------
# Auth brute-force protection (S1)
#
# Failed-attempt budget per (username, client IP) before the auth endpoints
# return HTTP 429. Counters reset on success and expire after the window, so a
# legitimate user is never locked out indefinitely.
# ---------------------------------------------------------------------------
# 5 failed login attempts per 15-minute window, per username+IP.
AUTH_LOGIN_FAIL_LIMIT = 5
AUTH_LOGIN_FAIL_WINDOW = 15 * 60
# 5 wrong-current-password attempts per 15-minute window, per user+IP.
# New-password *policy* violations (weak value) do not count toward this.
AUTH_CHANGE_PASSWORD_FAIL_LIMIT = 5
AUTH_CHANGE_PASSWORD_FAIL_WINDOW = 15 * 60
# 5 rejected attempts at /admin/login/ per 15-minute window, per user+IP.
# Mirrors the `login` namespace for the hardened Django admin site
# (accounts.admin_site). Includes reserved/default-credential rejections.
AUTH_ADMIN_LOGIN_FAIL_LIMIT = 5
AUTH_ADMIN_LOGIN_FAIL_WINDOW = 15 * 60

# Reverse proxies trusted to supply the real end-client IP via
# ``X-Forwarded-For``. When the immediate peer (``REMOTE_ADDR``) is in this
# set, the first ``X-Forwarded-For`` value is used as the client IP that keys
# the throttle budget and audit records. Empty by default: the header is
# ignored entirely and ``REMOTE_ADDR`` is used, so a client cannot rotate its
# own throttle identity or audit IP.
#
# Deployment expectation: with nothing listed here the app must run such that
# ``REMOTE_ADDR`` reflects the real client -- either directly exposed, or
# behind a proxy that overwrites the peer address. Do NOT rely on
# ``X-Forwarded-For`` being trustworthy unless the immediate peer is in this
# set. Example:
#   AUTH_TRUSTED_PROXY_IPS = {'127.0.0.1', '10.0.0.5'}
AUTH_TRUSTED_PROXY_IPS = ()

# ---------------------------------------------------------------------------
# Admin seeding gate (S4)
#
# The ``post_migrate`` admin seed in ``accounts/apps.py`` only runs when the
# deployment opts in via DEBUG (dev) or the ``EIXNPOS_SEED_ADMIN=1`` env flag.
# It never resurrects a deleted admin on an existing database.
# ---------------------------------------------------------------------------
SEED_ADMIN_ENV_FLAG = os.environ.get('EIXNPOS_SEED_ADMIN', '') == '1'

# ---------------------------------------------------------------------------
# Audit logging (S2)
#
# ``auth.audit`` records login/password-change/guard events, minus passwords
# and session keys, to the console and to SERVER_LOG_FILE.
# ---------------------------------------------------------------------------
SERVER_LOG_FILE = BASE_DIR / 'server.log'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'auth': {
            'format': '%(asctime)s level=%(levelname)s logger=%(name)s %(message)s',
        },
        'plain': {
            'format': '%(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'auth',
        },
        'auth_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(SERVER_LOG_FILE),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'auth',
        },
    },
    'loggers': {
        'auth.audit': {
            'handlers': ['console', 'auth_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}