from decimal import Decimal

from django.conf import settings
from django.db import models


class FlashSale(models.Model):
    """秒杀活动只定义开放时间；库存仍由 SKU 的 Inventory 保存。"""

    name = models.CharField(max_length=160)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=False)

    class Meta:
        constraints = (
            models.CheckConstraint(
                condition=models.Q(starts_at__lt=models.F("ends_at")),
                name="promotions_sale_start_before_end",
            ),
        )

    def __str__(self):
        return self.name


class FlashSaleItem(models.Model):
    """活动中的购买单位；下单时将活动价格写入订单快照。"""

    flash_sale = models.ForeignKey(
        FlashSale,
        on_delete=models.PROTECT,
        related_name="items",
    )
    sku = models.ForeignKey(
        "catalog.SKU",
        on_delete=models.PROTECT,
        related_name="flash_sale_items",
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=False)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("flash_sale", "sku"),
                name="promotions_item_unique_sale_sku",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=Decimal("0.00")),
                name="promotions_item_price_gte_0",
            ),
        )

    def __str__(self):
        return f"Flash sale item {self.pk}: SKU {self.sku_id}"


class FlashSalePurchase(models.Model):
    """每张秒杀订单独立保留购买历史，旧订单事件不能改写新订单名额。"""

    class Status(models.TextChoices):
        HELD = "HELD", "Held"
        CONSUMED = "CONSUMED", "Consumed"
        RELEASED = "RELEASED", "Released"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="flash_sale_purchases",
    )
    flash_sale_item = models.ForeignKey(
        FlashSaleItem,
        on_delete=models.PROTECT,
        related_name="purchases",
    )
    order = models.OneToOneField(
        "ordering.Order",
        on_delete=models.PROTECT,
        related_name="flash_sale_purchase",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.HELD,
    )
    # MySQL 唯一键允许多个 NULL；已释放的历史保留 NULL，待付或成功记录保留 1。
    blocking_marker = models.PositiveSmallIntegerField(
        null=True,
        editable=False,
        default=1,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = (
            models.UniqueConstraint(
                fields=("customer", "flash_sale_item", "blocking_marker"),
                name="promotions_purchase_one_blocking",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=("HELD", "CONSUMED"),
                        blocking_marker__isnull=False,
                        blocking_marker=1,
                    )
                    | models.Q(status="RELEASED", blocking_marker__isnull=True)
                ),
                name="promotions_purchase_status_marker_consistent",
            ),
        )

    def __str__(self):
        return f"Flash sale purchase for order {self.order_id} ({self.status})"
