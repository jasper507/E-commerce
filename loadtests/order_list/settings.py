"""Settings for the dedicated Compose target, not the developer database."""

import os


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing dedicated load-test variable: {name}")
    return value


if required("ORDER_LOAD_ISOLATED") != "1":
    raise RuntimeError("Use the independent order-load Compose target.")

# Set every database selector before the normal settings can load the root .env.
# The dedicated image does not copy that file, and dotenv cannot override these.
os.environ.update({
    "DJANGO_ENV": "test", "DJANGO_DEBUG": "false",
    "DJANGO_SECRET_KEY": required("ORDER_LOAD_DJANGO_SECRET"),
    "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,app",
    "MYSQL_DATABASE": "project_one_order_load",
    "MYSQL_TEST_DATABASE": "test_project_one_order_load_unused",
    "MYSQL_USER": "project_one_order_load",
    "MYSQL_PASSWORD": required("ORDER_LOAD_MYSQL_PASSWORD"),
    "MYSQL_HOST": "db", "MYSQL_PORT": "3306",
    "CATALOG_CACHE_ENABLED": "false", "CATALOG_SEARCH_ENABLED": "false",
    "FLASH_SALE_ADMISSION_ENABLED": "false", "PAYMENT_EVENTS_ASYNC": "false",
})
required("ORDER_LOAD_TOKEN")
required("ORDER_LOAD_PASSWORD")

from config.settings import *  # noqa: E402,F403

ROOT_URLCONF = "loadtests.order_list.urls"
MIDDLEWARE = ["loadtests.order_list.urls.LoadTargetGuard", *MIDDLEWARE]
WSGI_APPLICATION = "loadtests.order_list.wsgi.application"
