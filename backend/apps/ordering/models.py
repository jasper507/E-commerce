from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Cart(models.Model):
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
