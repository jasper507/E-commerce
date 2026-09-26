"""覆盖当前 Admin/model.save 目录写入；bulk update 不发送这些信号。"""

from functools import partial

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.catalog.cache import invalidate_product_details
from apps.catalog.models import Category, Product, SKU


@receiver(post_save, sender=Product)
def product_saved(sender, instance, using, raw, **kwargs):
    if raw or not settings.CATALOG_CACHE_ENABLED:
        return
    transaction.on_commit(
        partial(invalidate_product_details, (instance.pk,)), using=using
    )


def invalidate_category(category_id, using):
    product_ids = Product.objects.using(using).filter(
        category_id=category_id
    ).values_list("pk", flat=True)
    invalidate_product_details(product_ids)


@receiver(post_save, sender=Category)
def category_saved(sender, instance, using, raw, **kwargs):
    if raw or not settings.CATALOG_CACHE_ENABLED:
        return
    transaction.on_commit(partial(invalidate_category, instance.pk, using), using=using)


@receiver(pre_save, sender=SKU)
def remember_sku_product(sender, instance, using, raw, update_fields, **kwargs):
    if raw or not settings.CATALOG_CACHE_ENABLED:
        return
    instance._cache_previous_product_id = None
    if instance._state.adding:
        return
    if update_fields is not None and not {"product", "product_id"}.intersection(
        update_fields
    ):
        return
    # SKU 可在 Admin 改归属，旧商品也必须移除这个 SKU 的缓存副本。
    instance._cache_previous_product_id = SKU.objects.using(using).filter(
        pk=instance.pk
    ).values_list("product_id", flat=True).first()


@receiver(post_save, sender=SKU)
def sku_saved(sender, instance, using, raw, **kwargs):
    if raw or not settings.CATALOG_CACHE_ENABLED:
        return
    product_ids = (
        instance.product_id,
        getattr(instance, "_cache_previous_product_id", None),
    )
    transaction.on_commit(partial(invalidate_product_details, product_ids), using=using)
