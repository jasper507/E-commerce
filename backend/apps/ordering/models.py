import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Cart(models.Model):
    """每位顾客一辆活动购物车；唯一关系从数据库层保证不会有第二辆。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cart",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart for {self.user.username}"


class CartItem(models.Model):
    """购物车的一种 SKU；同车同 SKU 合并，数量限制在 1～99。"""

    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name="items",
    )
    sku = models.ForeignKey(
        "catalog.SKU",
        on_delete=models.PROTECT,
        related_name="cart_items",
    )
    quantity = models.PositiveSmallIntegerField(
        validators=(MinValueValidator(1), MaxValueValidator(99)),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = (
            models.UniqueConstraint(
                fields=("cart", "sku"),
                name="ordering_cartitem_unique_cart_sku",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1, quantity__lte=99),
                name="ordering_cartitem_quantity_1_99",
            ),
        )

    def __str__(self):
        return f"{self.cart.user.username}: {self.sku.code} x {self.quantity}"


class Order(models.Model):
    """下单时形成的交易事实；金额和状态不能由客户端直接决定。"""

    class CreationScope(models.TextChoices):
        CART = "cart_checkout", "Cart checkout"
        FLASH_SALE = "flash_sale_checkout", "Flash sale checkout"

    class Status(models.TextChoices):
        PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
        PAID = "PAID", "Paid"
        SHIPPED = "SHIPPED", "Shipped"
        COMPLETED = "COMPLETED", "Completed"
        CANCELED = "CANCELED", "Canceled"
        EXPIRED = "EXPIRED", "Expired"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING_PAYMENT,
    )
    currency = models.CharField(max_length=3, default="CNY", editable=False)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    shipping_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total = models.DecimalField(max_digits=12, decimal_places=2)
    creation_scope = models.CharField(
        max_length=32,
        choices=CreationScope.choices,
        default=CreationScope.CART,
        editable=False,
    )
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = (
            models.Index(
                fields=("user", "created_at", "id"),
                name="ordering_order_user_created_id",
            ),
        )
        # 不同下单入口独立识别同一次请求；旧订单默认属于购物车入口。
        constraints = (
            models.UniqueConstraint(
                fields=("user", "creation_scope", "idempotency_key"),
                name="ordering_order_unique_user_scope_key",
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=Decimal("0.00")),
                name="ordering_order_subtotal_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_total__gte=Decimal("0.00")),
                name="ordering_order_discount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(shipping_fee__gte=Decimal("0.00")),
                name="ordering_order_shipping_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=Decimal("0.00")),
                name="ordering_order_total_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_total__lte=models.F("subtotal")),
                name="ordering_order_discount_lte_subtotal",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    total=(
                        models.F("subtotal")
                        - models.F("discount_total")
                        + models.F("shipping_fee")
                    )
                ),
                name="ordering_order_total_formula",
            ),
        )

    def __str__(self):
        return str(self.public_id)


class OrderItem(models.Model):
    """购买明细快照：商品后续改名、改价也不应改写成交时的内容。"""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    sku = models.ForeignKey(
        "catalog.SKU",
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    product_name_snapshot = models.CharField(max_length=160)
    sku_code_snapshot = models.CharField(max_length=64)
    color_snapshot = models.CharField(max_length=50, blank=True)
    attributes_snapshot = models.JSONField(default=dict, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ("id",)
        # 折扣不能超过原价；明细应付金额由单价、数量和折扣共同决定。
        constraints = (
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1, quantity__lte=99),
                name="ordering_orderitem_quantity_1_99",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=Decimal("0.00")),
                name="ordering_orderitem_unit_price_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(line_total__gte=Decimal("0.00")),
                name="ordering_orderitem_line_total_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=Decimal("0.00")),
                name="ordering_orderitem_discount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    discount_amount__lte=(
                        models.F("unit_price") * models.F("quantity")
                    )
                ),
                name="ordering_orderitem_discount_lte_subtotal",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    line_total=(
                        models.F("unit_price") * models.F("quantity")
                        - models.F("discount_amount")
                    )
                ),
                name="ordering_orderitem_line_total_formula",
            ),
        )

    def __str__(self):
        return f"{self.order.public_id}: {self.sku_code_snapshot} x {self.quantity}"


class AddressSnapshot(models.Model):
    """收货地址的下单时快照，不随顾客后来修改地址而变化。"""

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="address_snapshot",
    )
    recipient_name = models.CharField(max_length=50)
    phone = models.CharField(max_length=11)
    province = models.CharField(max_length=50)
    city = models.CharField(max_length=50)
    district = models.CharField(max_length=50)
    detail = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.order.public_id}: {self.recipient_name}"


class Shipment(models.Model):
    """模拟发货事实；一对一关系从数据库层防止同一订单重复发货。"""

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="shipment",
    )
    shipped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="processed_shipments",
    )
    shipped_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Shipment for {self.order.public_id}"
