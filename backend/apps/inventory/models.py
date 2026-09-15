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
