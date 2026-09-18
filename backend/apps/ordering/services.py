from dataclasses import dataclass

from django.db import transaction

from apps.catalog.models import SKU
from apps.ordering.models import Cart, CartItem


@dataclass(frozen=True)
class CartRuleError(Exception):
    code: str
    detail: str


def _locked_cart_for(user):
    cart, _ = Cart.objects.get_or_create(user=user)
    return Cart.objects.select_for_update().get(pk=cart.pk)


def _purchasable_sku(sku_code):
    try:
        sku = (
            SKU.objects.select_related("product__category", "inventory")
            .get(code=sku_code)
        )
    except SKU.DoesNotExist as exc:
        raise CartRuleError("sku_not_found", "SKU does not exist.") from exc

    if not sku.product.category.is_active:
        raise CartRuleError("category_inactive", "Category is inactive.")
    if not sku.product.is_active:
        raise CartRuleError("product_inactive", "Product is inactive.")
    if not sku.is_active:
        raise CartRuleError("sku_inactive", "SKU is inactive.")

    inventory = getattr(sku, "inventory", None)
    if inventory is None or inventory.available_stock <= 0:
        raise CartRuleError("out_of_stock", "SKU is out of stock.")

    return sku


def _ensure_stock(sku, quantity):
    if quantity > sku.inventory.available_stock:
        raise CartRuleError(
            "insufficient_stock",
            "Requested quantity exceeds the currently available stock.",
        )


@transaction.atomic
def add_cart_item(*, user, sku_code, quantity):
    sku = _purchasable_sku(sku_code)
    cart = _locked_cart_for(user)
    item = CartItem.objects.filter(cart=cart, sku=sku).first()
    target_quantity = quantity if item is None else item.quantity + quantity

    if target_quantity > 99:
        raise CartRuleError(
            "quantity_limit_exceeded",
            "Cart item quantity cannot exceed 99.",
        )
    _ensure_stock(sku, target_quantity)

    if item is None:
        item = CartItem.objects.create(
            cart=cart,
            sku=sku,
            quantity=target_quantity,
        )
        created = True
    else:
        item.quantity = target_quantity
        item.save(update_fields=("quantity", "updated_at"))
        created = False

    return item, created


@transaction.atomic
def update_cart_item(*, user, sku_code, quantity):
    try:
        item = (
            CartItem.objects.select_for_update()
            .select_related("cart", "sku__product__category", "sku__inventory")
            .get(cart__user=user, sku__code=sku_code)
        )
    except CartItem.DoesNotExist as exc:
        raise CartRuleError(
            "cart_item_not_found",
            "Cart item does not exist.",
        ) from exc

    sku = _purchasable_sku(sku_code)
    _ensure_stock(sku, quantity)
    item.quantity = quantity
    item.save(update_fields=("quantity", "updated_at"))
    return item
