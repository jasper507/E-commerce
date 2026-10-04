"""目录 save 提交后切换全局缓存代号；批量 update 不发送这些信号。"""

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.catalog.cache import invalidate_catalog_details
from apps.catalog.models import Category, Product, SKU


@receiver(post_save, sender=Product)
@receiver(post_save, sender=Category)
@receiver(post_save, sender=SKU)
def catalog_saved(sender, instance, using, raw, **kwargs):
    if raw or not settings.CATALOG_CACHE_ENABLED:
        return
    transaction.on_commit(invalidate_catalog_details, using=using)
