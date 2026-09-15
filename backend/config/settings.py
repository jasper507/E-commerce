"""Project One settings shared by local development, tests, and deployment."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

# .env is a local convenience only. Real deployment configuration must be
# supplied by the deployment environment and must never be committed.
load_dotenv(PROJECT_ROOT / ".env")


def env_value(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def env_bool(name: str, default: bool) -> bool:
    value = env_value(name)
    if value is None:
        return default

    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean value.")


def env_int(name: str, default: int) -> int:
    value = env_value(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer.") from exc


def env_csv(name: str, default: str) -> list[str]:
    value = env_value(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


VALID_ENVIRONMENTS = {"development", "test", "staging", "production"}
LOCAL_ENVIRONMENTS = {"development", "test"}
ENVIRONMENT = env_value("DJANGO_ENV", "development").lower()

if ENVIRONMENT not in VALID_ENVIRONMENTS:
    choices = ", ".join(sorted(VALID_ENVIRONMENTS))
    raise ImproperlyConfigured(f"DJANGO_ENV must be one of: {choices}.")

IS_LOCAL_ENVIRONMENT = ENVIRONMENT in LOCAL_ENVIRONMENTS
INSECURE_LOCAL_SECRET = "django-insecure-project-one-local-only"
INSECURE_LOCAL_DB_PASSWORD = "project-one-local-only"

SECRET_KEY = env_value("DJANGO_SECRET_KEY", INSECURE_LOCAL_SECRET)
if not IS_LOCAL_ENVIRONMENT and SECRET_KEY == INSECURE_LOCAL_SECRET:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a non-default value outside local development."
    )

DEBUG = env_bool("DJANGO_DEBUG", default=ENVIRONMENT == "development")
if not IS_LOCAL_ENVIRONMENT and DEBUG:
    raise ImproperlyConfigured("DJANGO_DEBUG must be false outside local development.")

ALLOWED_HOSTS = env_csv(
    "DJANGO_ALLOWED_HOSTS",
    "localhost,127.0.0.1,testserver" if IS_LOCAL_ENVIRONMENT else "",
)
if not IS_LOCAL_ENVIRONMENT and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must contain at least one host outside local development."
    )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts.apps.AccountsConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.inventory.apps.InventoryConfig",
    "apps.ordering.apps.OrderingConfig",
    "apps.payments.apps.PaymentsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

MYSQL_DATABASE = env_value("MYSQL_DATABASE", "project_one")
MYSQL_USER = env_value("MYSQL_USER", "project_one")
MYSQL_PASSWORD = env_value("MYSQL_PASSWORD", INSECURE_LOCAL_DB_PASSWORD)
MYSQL_HOST = env_value("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = env_int("MYSQL_PORT", 3307)
MYSQL_TEST_DATABASE = env_value("MYSQL_TEST_DATABASE", "test_project_one")

if not MYSQL_TEST_DATABASE.startswith("test_"):
    raise ImproperlyConfigured(
        "MYSQL_TEST_DATABASE must start with 'test_' to protect non-test databases."
    )

if not IS_LOCAL_ENVIRONMENT and MYSQL_PASSWORD == INSECURE_LOCAL_DB_PASSWORD:
    raise ImproperlyConfigured(
        "MYSQL_PASSWORD must be set to a non-default value outside local development."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": MYSQL_DATABASE,
        "USER": MYSQL_USER,
        "PASSWORD": MYSQL_PASSWORD,
        "HOST": MYSQL_HOST,
        "PORT": MYSQL_PORT,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        "TEST": {
            "NAME": MYSQL_TEST_DATABASE,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = PROJECT_ROOT / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not IS_LOCAL_ENVIRONMENT
CSRF_COOKIE_SECURE = not IS_LOCAL_ENVIRONMENT
CSRF_FAILURE_VIEW = "apps.accounts.views.csrf_failure"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": env_value("DJANGO_LOG_LEVEL", "INFO"),
    },
}
