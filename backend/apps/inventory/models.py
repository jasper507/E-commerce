from django.db import models


class Inventory(models.Model):
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
    class Status(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        COMMITTED = "COMMITTED", "Committed"
        RELEASED = "RELEASED", "Released"

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
    class Type(models.TextChoices):
        RESERVE = "RESERVE", "Reserve"
        COMMIT = "COMMIT", "Commit"
        RELEASE = "RELEASE", "Release"

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
