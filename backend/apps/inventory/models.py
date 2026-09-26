from django.db import models


class Inventory(models.Model):
    """每个 SKU 一条库存：available 可下单，reserved 已被未支付订单占用。"""

    sku = models.OneToOneField(
        "catalog.SKU",
        on_delete=models.PROTECT,
        related_name="inventory",
    )
    available_stock = models.PositiveIntegerField(default=0)
    reserved_stock = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = (
            models.CheckConstraint(
                condition=models.Q(available_stock__gte=0),
                name="inventory_available_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_stock__gte=0),
                name="inventory_reserved_gte_0",
            ),
        )

    def __str__(self):
        return f"Inventory for {self.sku.code}"


class InventoryReservation(models.Model):
    """一条订单明细对应一次预占；状态区分占用、核销和释放。"""

    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        COMMITTED = "COMMITTED", "Committed"
        RELEASED = "RELEASED", "Released"
        RESTORED = "RESTORED", "Restored after paid cancellation"

    order_item = models.OneToOneField(
        "ordering.OrderItem",
        on_delete=models.PROTECT,
        related_name="reservation",
    )
    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    quantity = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RESERVED,
    )
    # 期限只是业务时间点；必须由后续关闭流程处理，字段本身不会自动释放库存。
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="inventory_reservation_quantity_gte_1",
            ),
        )

    def __str__(self):
        return f"Reservation {self.pk}: {self.quantity}"


class InventoryMovement(models.Model):
    """库存变化审计流水；保留增减量，便于追查库存数字的来源。"""

    class Type(models.TextChoices):
        RESERVE = "RESERVE", "Reserve"
        COMMIT = "COMMIT", "Commit"
        RELEASE = "RELEASE", "Release"
        RESTORE = "RESTORE", "Restore after paid cancellation"

    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    reservation = models.ForeignKey(
        InventoryReservation,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(max_length=16, choices=Type.choices)
    available_delta = models.IntegerField()
    reserved_delta = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        # 同一预占的同类动作最多记一笔，配合 Service 的状态检查防止重复核销/释放。
        constraints = (
            models.UniqueConstraint(
                fields=("reservation", "movement_type"),
                name="inventory_movement_unique_reservation_type",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(available_delta=0)
                    | ~models.Q(reserved_delta=0)
                ),
                name="inventory_movement_has_delta",
            ),
        )

    def __str__(self):
        return f"{self.movement_type}: {self.inventory.sku.code}"
