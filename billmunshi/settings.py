"""
Django settings for Billmunshi

This file is organized by functional sections with concise comments.
Environment variables are loaded via `django-environ`. See `.env` for values.
"""

import os
from pathlib import Path

import environ
from corsheaders.defaults import default_headers
from django.utils.translation import gettext_lazy

# ======================================================================================
# PATHS & ENV
# ======================================================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize env and read `.env` from the project root
env = environ.Env()
env.read_env(os.path.join(BASE_DIR, ".env"))

# ======================================================================================
# CORE / SECURITY
# ======================================================================================

# Never hardcode secrets in production. Override via .env
SECRET_KEY = env("SECRET_KEY", default="django-insecure-vTA7Mk3amTPdLCSuduER9I0o2n0KKTBst9j31kIO")

# DEBUG off in production
DEBUG = env.bool("DEBUG", default=True)

# Set explicit hosts in production (comma-separated in .env)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

# ======================================================================================
# APPLICATIONS
# ======================================================================================

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sitemaps",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.forms",
]

THIRD_PARTY_APPS = [
    # Auth / Accounts
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.mfa",
    # APIs & Docs
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "rest_framework_api_key",
    # Async / Tasks / Flags / Health
    "celery_progress",
    "waffle",
    "health_check",
    "health_check.db",
    "health_check.contrib.celery",
    "health_check.contrib.redis",
    "corsheaders",  # Already included
    "rest_framework_nested",  # Add this
]

# Add your local apps here
PROJECT_APPS = [
    "apps.users",
    "apps.teams",
    "apps.subscriptions",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PROJECT_APPS

# ======================================================================================
# MIDDLEWARE
# ======================================================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "waffle.middleware.WaffleMiddleware",
    # Add teams middleware
    "apps.teams.middleware.OrganizationContextMiddleware",
    "apps.teams.middleware.APIKeyAuthenticationMiddleware",
    "apps.teams.middleware.APIKeyRateLimitMiddleware",
    # Users middleware
    "apps.users.middleware.UserActivityTrackingMiddleware",
    "apps.users.middleware.SessionCleanupMiddleware",
    "apps.users.middleware.UserPreferenceMiddleware",
    "apps.users.middleware.APIUsageTrackingMiddleware",
    "apps.users.middleware.SecurityHeadersMiddleware",
]

# ======================================================================================
# URLS / TEMPLATES / WSGI
# ======================================================================================

ROOT_URLCONF = "billmunshi.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Add a global template directory; app templates still work via APP_DIRS=True
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "billmunshi.wsgi.application"

# ======================================================================================
# DATABASE
# ======================================================================================
# Prefer single DATABASE_URL (e.g. postgresql://user:pass@host:5432/db)
# Falls back to individual env vars for local development.

if "DATABASE_URL" in env:
    DATABASES = {"default": env.db()}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DJANGO_DATABASE_NAME", default="base"),
            "USER": env("DJANGO_DATABASE_USER", default="postgres"),
            "PASSWORD": env("DJANGO_DATABASE_PASSWORD", default="***"),
            "HOST": env("DJANGO_DATABASE_HOST", default="localhost"),
            "PORT": env("DJANGO_DATABASE_PORT", default="5432"),
        }
    }

AUTH_USER_MODEL = 'users.CustomUser'

# ======================================================================================
# AUTH / PASSWORDS
# ======================================================================================

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ======================================================================================
# I18N / TIMEZONE
# ======================================================================================

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ======================================================================================
# DJANGO-ALLAUTH (Headless-ready)
# ======================================================================================

ACCOUNT_ADAPTER = "apps.teams.adapter.AcceptInvitationAdapter"
HEADLESS_ADAPTER = "apps.users.adapter.CustomHeadlessAdapter"

# Only email login; no username
ACCOUNT_LOGIN_METHODS = {"email"}
# Minimal signup field set; * indicates required in allauth headless forms
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]

ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False  # don't reveal whether an email exists
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_UNIQUE_EMAIL = True

# Anti-bot field on signup
ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "phone_number_x"

# Session behavior
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_BY_CODE_ENABLED = True

# How to display users in UI/admin
ACCOUNT_USER_DISPLAY = lambda user: user.get_display_name()  # noqa: E731

# Use React SPA routes for account flows when headless mode is enabled
FRONTEND_ADDRESS = env("FRONTEND_ADDRESS", default="http://localhost:5174")
USE_HEADLESS_URLS = env.bool("USE_HEADLESS_URLS", default=False)
if USE_HEADLESS_URLS:
    HEADLESS_FRONTEND_URLS = {
        "account_confirm_email": f"{FRONTEND_ADDRESS}/account/verify-email/{{key}}",
        "account_reset_password_from_key": f"{FRONTEND_ADDRESS}/account/password/reset/key/{{key}}",
        "account_signup": f"{FRONTEND_ADDRESS}/account/signup",
    }

# ======================================================================================
# CSRF / CORS / SESSIONS
# ======================================================================================
# Ensure CSRF and CORS domains match your frontend host(s)

CSRF_TRUSTED_ORIGINS = [FRONTEND_ADDRESS]
CSRF_COOKIE_DOMAIN = env("CSRF_COOKIE_DOMAIN", default=None)

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = (*default_headers, "x-password-reset-key", "x-email-verification-key")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[FRONTEND_ADDRESS])

SESSION_COOKIE_DOMAIN = env("SESSION_COOKIE_DOMAIN", default=None)

# "mandatory" | "optional" | "none" — use env to toggle email flows
ACCOUNT_EMAIL_VERIFICATION = env("ACCOUNT_EMAIL_VERIFICATION", default="none")

AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
)

# ======================================================================================
# STATIC & MEDIA
# ======================================================================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static_root"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # Use `CompressedManifestStaticFilesStorage` in production to fingerprint assets
        # (May require adjusting asset paths in CSS/SCSS)
        # "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Optional: S3-backed media storage (configure env and set USE_S3_MEDIA=True)
USE_S3_MEDIA = env.bool("USE_S3_MEDIA", default=False)
if USE_S3_MEDIA:
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="base-media")
    AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"
    PUBLIC_MEDIA_LOCATION = "media"
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/{PUBLIC_MEDIA_LOCATION}/"
    STORAGES["default"] = {"BACKEND": "apps.web.storage_backends.PublicMediaStorage"}

# ======================================================================================
# EMAIL
# ======================================================================================

# Default server identity and sender
SERVER_EMAIL = env("SERVER_EMAIL", default="noreply@localhost:8000")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="heramb1008@gmail.com")

# In dev, print emails to console; override in .env for real email backends
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_SUBJECT_PREFIX = "[Billmunshi] "

# ======================================================================================
# DJANGO SITES
# ======================================================================================

SITE_ID = 1

# ======================================================================================
# DRF / API
# ======================================================================================

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ("apps.api.permissions.IsAuthenticatedOrHasUserAPIKey",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Billmunshi",
    "DESCRIPTION": "Billing Automation",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_SETTINGS": {"displayOperationId": True},
    "PREPROCESSING_HOOKS": ["apps.api.schema.filter_schema_apis"],
    "APPEND_COMPONENTS": {
        "securitySchemes": {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "Authorization"}}
    },
    "SECURITY": [{"ApiKeyAuth": []}],
}

# ======================================================================================
# REDIS / CACHE / CELERY
# ======================================================================================

# Resolve Redis URL from common env names; fallback to host/port
if "REDIS_URL" in env:
    REDIS_URL = env("REDIS_URL")
elif "REDIS_TLS_URL" in env:
    REDIS_URL = env("REDIS_TLS_URL")
else:
    REDIS_HOST = env("REDIS_HOST", default="localhost")
    REDIS_PORT = env("REDIS_PORT", default="6379")
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/0"

# Allow TLS endpoints without certs (useful in some hosted envs)
if REDIS_URL.startswith("rediss"):
    REDIS_URL = f"{REDIS_URL}?ssl_cert_reqs=none"

# Default cache: dummy in DEBUG, Redis in production
DUMMY_CACHE = {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
REDIS_CACHE = {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}

CACHES = {"default": DUMMY_CACHE if DEBUG else REDIS_CACHE}

# Celery uses the same Redis for broker and results
CELERY_BROKER_URL = CELERY_RESULT_BACKEND = REDIS_URL
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ======================================================================================
# WAFFLE (Feature Flags)
# ======================================================================================

# WAFFLE_FLAG_MODEL = "teams.Flag"

# ======================================================================================
# PROJECT METADATA
# ======================================================================================

PROJECT_METADATA = {
    "NAME": gettext_lazy("Billmunshi"),
    "URL": "http://localhost:8000",
    "DESCRIPTION": gettext_lazy("Billmunshi"),
    "KEYWORDS": "SaaS Billing Automation, Billing",
    "CONTACT_EMAIL": "heramb1008@gmail.com",
}

# ======================================================================================
# URL BUILDING
# ======================================================================================

# If your deployment is behind HTTPS, and you need absolute URLs to use https://
USE_HTTPS_IN_ABSOLUTE_URLS = env.bool("USE_HTTPS_IN_ABSOLUTE_URLS", default=False)

# ======================================================================================
# LOGGING
# ======================================================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": '[{asctime}] {levelname} "{name}" {message}',
            "style": "{",
            "datefmt": "%d/%b/%Y %H:%M:%S",  # matches Django runserver format
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
        "base": {"handlers": ["console"], "level": env("BASE_LOG_LEVEL", default="INFO")},
    },
}

# ======================================================================================
# OPTIONAL: SECURITY HARDENING FOR PRODUCTION (UNCOMMENT & TUNE)
# ======================================================================================
# SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# SECURE_HSTS_SECONDS = 31536000
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True
# X_FRAME_OPTIONS = "DENY"
