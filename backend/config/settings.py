"""Project One 的开发、测试与部署共用配置；环境变量决定实际运行边界。"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

# .env 只方便本地开发；真实部署必须由运行环境提供配置，敏感值绝不能提交到 Git。
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
    "apps.promotions.apps.PromotionsConfig",
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
PAYMENT_MAX_ATTEMPTS = env_int("PAYMENT_MAX_ATTEMPTS", 3)

# 首个搜索读入口只连接隔离测试来源，开发/部署保持原MySQL浏览。
CATALOG_SEARCH_ENABLED = env_bool("CATALOG_SEARCH_ENABLED", False)
CATALOG_SEARCH_URL = env_value("CATALOG_SEARCH_URL", "http://127.0.0.1:9201")
CATALOG_SEARCH_INDEX = env_value("CATALOG_SEARCH_INDEX", "")
if CATALOG_SEARCH_ENABLED and ENVIRONMENT != "test":
    raise ImproperlyConfigured("Catalog search currently requires an isolated test environment.")

# 默认保留同步基线；本期异步运行仅允许本地/隔离测试，不自动迁移开发库。
PAYMENT_EVENTS_ASYNC = env_bool("PAYMENT_EVENTS_ASYNC", False)
PAYMENT_EVENT_PROCESSING_LEASE_SECONDS = env_int("PAYMENT_EVENT_PROCESSING_LEASE_SECONDS", 60)
if PAYMENT_EVENT_PROCESSING_LEASE_SECONDS < 1:
    raise ImproperlyConfigured("PAYMENT_EVENT_PROCESSING_LEASE_SECONDS must be positive.")
if PAYMENT_EVENTS_ASYNC and not IS_LOCAL_ENVIRONMENT:
    raise ImproperlyConfigured("Payment MQ is currently a local development/test slice.")

CELERY_BROKER_URL = env_value(
    "CELERY_BROKER_URL",
    "amqp://project_one:project-one-mq-local-only@127.0.0.1:5672/project_one",
)
CELERY_TASK_DEFAULT_QUEUE = env_value("CELERY_TASK_DEFAULT_QUEUE", "payments")
CELERY_TASK_ROUTES = {
    "payments.process_event": {"queue": CELERY_TASK_DEFAULT_QUEUE},
}
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TASK_DEFAULT_DELIVERY_MODE = "persistent"
CELERY_TASK_IGNORE_RESULT = True
CELERY_RESULT_BACKEND = None
CELERY_TASK_CREATE_MISSING_QUEUES = True
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_BROKER_TRANSPORT_OPTIONS = {"confirm_publish": True}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# RabbitMQ 4.3 rejects transient non-exclusive queues. Celery's control and
# event queues use durable declarations while their default expiry cleans up
# unused queues; this does not replace payment Inbox recovery.
CELERY_CONTROL_QUEUE_DURABLE = True
CELERY_EVENT_QUEUE_DURABLE = True
CELERY_ENABLE_UTC = True
CELERY_TIMEZONE = "UTC"

# 可选的本地目录实验；不会把 Session、库存或支付存储改为 Redis。
CATALOG_CACHE_ENABLED = env_bool("CATALOG_CACHE_ENABLED", False)
CATALOG_CACHE_REDIS_URL = env_value("CATALOG_CACHE_REDIS_URL", "redis://127.0.0.1:6380/0")
CATALOG_CACHE_KEY_PREFIX = env_value("CATALOG_CACHE_KEY_PREFIX", "project-one:development")
CATALOG_CACHE_TTL = env_int("CATALOG_CACHE_TTL", 30)
CATALOG_CACHE_TTL_JITTER = env_int("CATALOG_CACHE_TTL_JITTER", 5)

if CATALOG_CACHE_TTL < 1 or not 0 <= CATALOG_CACHE_TTL_JITTER < CATALOG_CACHE_TTL:
    raise ImproperlyConfigured("Catalog cache requires TTL > jitter >= 0.")

if CATALOG_CACHE_ENABLED and not IS_LOCAL_ENVIRONMENT:
    raise ImproperlyConfigured("Catalog cache is currently a local development/test experiment.")

# 独立于商品缓存的准入实验；Redis 无法确认时不进入秒杀下单事务。
FLASH_SALE_ADMISSION_ENABLED = env_bool("FLASH_SALE_ADMISSION_ENABLED", False)
FLASH_SALE_ADMISSION_REDIS_URL = env_value("FLASH_SALE_ADMISSION_REDIS_URL", "redis://127.0.0.1:6380/0")
FLASH_SALE_ADMISSION_KEY_PREFIX = env_value("FLASH_SALE_ADMISSION_KEY_PREFIX", "project-one:development")
FLASH_SALE_ADMISSION_LIMIT = env_int("FLASH_SALE_ADMISSION_LIMIT", 20)
FLASH_SALE_ADMISSION_WINDOW_MS = env_int("FLASH_SALE_ADMISSION_WINDOW_MS", 1000)

if not (1 <= FLASH_SALE_ADMISSION_LIMIT <= 2147483647 and 1 <= FLASH_SALE_ADMISSION_WINDOW_MS <= 2147483647):
    raise ImproperlyConfigured("Flash-sale admission limit and window must be positive 32-bit integers.")
if FLASH_SALE_ADMISSION_ENABLED and not IS_LOCAL_ENVIRONMENT:
    raise ImproperlyConfigured("Flash-sale admission is currently a local development/test experiment.")

if PAYMENT_MAX_ATTEMPTS < 1:
    raise ImproperlyConfigured("PAYMENT_MAX_ATTEMPTS must be at least 1.")

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
