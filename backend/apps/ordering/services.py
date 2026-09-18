import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Address
from apps.catalog.models import SKU
from apps.inventory.models import (
    Inventory,
    InventoryMovement,
    InventoryReservation,
)
from apps.ordering.models import (
    AddressSnapshot,
    Cart,
    CartItem,
    Order,
    OrderItem,
)


User = get_user_model()
MONEY_PLACES = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class CartRuleError(Exception):
    code: str
    detail: str


@dataclass(frozen=True)
class OrderRuleError(Exception):
    code: str
    detail: str
    http_status: int


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


def _money(value):
    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _request_fingerprint(address_id):
    normalized = json.dumps(
        {"address_id": address_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _order_with_details(order_id):
    return (
        Order.objects.select_related("address_snapshot")
        .prefetch_related("items")
        .get(pk=order_id)
    )


@transaction.atomic
def create_order_from_cart(*, user, address_id, idempotency_key):
    locked_user = User.objects.select_for_update().get(pk=user.pk)
    fingerprint = _request_fingerprint(address_id)
    existing = Order.objects.filter(
        user=locked_user,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise OrderRuleError(
                "idempotency_conflict",
                "This Idempotency-Key was already used with a different request.",
                409,
            )
        return _order_with_details(existing.pk), False

    try:
        address = Address.objects.select_for_update().get(
            pk=address_id,
            user=locked_user,
        )
    except Address.DoesNotExist as exc:
        raise OrderRuleError(
            "address_not_found",
            "Address does not exist.",
            404,
        ) from exc

    cart = Cart.objects.select_for_update().filter(user=locked_user).first()
    if cart is None:
        raise OrderRuleError("cart_empty", "Cart is empty.", 400)

    cart_items = list(
        CartItem.objects.select_for_update()
        .select_related("sku__product__category")
        .filter(cart=cart)
        .order_by("sku_id", "id")
    )
    if not cart_items:
        raise OrderRuleError("cart_empty", "Cart is empty.", 400)

    inventories = {
        inventory.sku_id: inventory
        for inventory in Inventory.objects.select_for_update()
        .filter(sku_id__in=[item.sku_id for item in cart_items])
        .order_by("sku_id")
    }

    for item in cart_items:
        sku = item.sku
        if not sku.product.category.is_active:
            raise OrderRuleError(
                "category_inactive",
                f"Category for SKU {sku.code} is inactive.",
                409,
            )
        if not sku.product.is_active:
            raise OrderRuleError(
                "product_inactive",
                f"Product for SKU {sku.code} is inactive.",
                409,
            )
        if not sku.is_active:
            raise OrderRuleError(
                "sku_inactive",
                f"SKU {sku.code} is inactive.",
                409,
            )
        inventory = inventories.get(sku.pk)
        if inventory is None or inventory.available_stock < item.quantity:
            raise OrderRuleError(
                "insufficient_stock",
                f"SKU {sku.code} does not have enough available stock.",
                409,
            )

    priced_items = []
    for item in cart_items:
        unit_price = _money(item.sku.price)
        item_subtotal = _money(unit_price * item.quantity)
        discount_amount = ZERO_MONEY
        line_total = _money(item_subtotal - discount_amount)
        priced_items.append(
            (item, unit_price, item_subtotal, discount_amount, line_total)
        )
    subtotal = _money(
        sum((item_subtotal for _, _, item_subtotal, _, _ in priced_items), ZERO_MONEY)
    )
    discount_total = _money(
        sum((discount for _, _, _, discount, _ in priced_items), ZERO_MONEY)
    )
    shipping_fee = ZERO_MONEY
    total = _money(subtotal - discount_total + shipping_fee)

    order = Order.objects.create(
        user=locked_user,
        subtotal=subtotal,
        discount_total=discount_total,
        shipping_fee=shipping_fee,
        total=total,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    AddressSnapshot.objects.create(
        order=order,
        recipient_name=address.recipient_name,
        phone=address.phone,
        province=address.province,
        city=address.city,
        district=address.district,
        detail=address.detail,
    )

    expires_at = timezone.now() + timedelta(minutes=15)
    for cart_item, unit_price, _, discount_amount, line_total in priced_items:
        sku = cart_item.sku
        inventory = inventories[sku.pk]
        order_item = OrderItem.objects.create(
            order=order,
            sku=sku,
            product_name_snapshot=sku.product.name,
            sku_code_snapshot=sku.code,
            color_snapshot=sku.color,
            attributes_snapshot=sku.attributes,
            unit_price=unit_price,
            quantity=cart_item.quantity,
            discount_amount=discount_amount,
            line_total=line_total,
        )
        inventory.available_stock -= cart_item.quantity
        inventory.reserved_stock += cart_item.quantity
        inventory.save(update_fields=("available_stock", "reserved_stock"))
        reservation = InventoryReservation.objects.create(
            order_item=order_item,
            inventory=inventory,
            quantity=cart_item.quantity,
            expires_at=expires_at,
        )
        InventoryMovement.objects.create(
            inventory=inventory,
            reservation=reservation,
            movement_type=InventoryMovement.Type.RESERVE,
            available_delta=-cart_item.quantity,
            reserved_delta=cart_item.quantity,
        )

    CartItem.objects.filter(pk__in=[item.pk for item in cart_items]).delete()
    return _order_with_details(order.pk), True


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
