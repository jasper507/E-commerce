from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class Product(models.Model):
    """商品的共享信息；真正的价格和可购买规格保存在 SKU。"""

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ("name", "id")

    def __str__(self):
        return self.name


class SKU(models.Model):
    """实际购买单位，价格和可售状态都落在具体规格上。"""

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="skus",
    )
    code = models.CharField(max_length=64, unique=True)
    color = models.CharField(max_length=50, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ("price", "code")
        constraints = (
            models.CheckConstraint(
                condition=models.Q(price__gte=Decimal("0.00")),
                name="catalog_sku_price_gte_0",
            ),
        )

    def clean(self):
        super().clean()
        # 展示型规格只接受 JSON 对象；常用查询字段应保持为普通列。
        if not isinstance(self.attributes, dict):
            raise ValidationError(
                {"attributes": "SKU attributes must be a JSON object."}
            )

    def __str__(self):
        return self.code
