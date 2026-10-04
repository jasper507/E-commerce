"""后台进程与 Django 共用配置；支付最终结果仍保存在 MySQL。"""

import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("project_one")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
