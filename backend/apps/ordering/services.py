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
    """购物车业务错误：code 供前端稳定判断，detail 供用户阅读。"""

    code: str
    detail: str


@dataclass(frozen=True)
class OrderRuleError(Exception):
    """下单业务错误：由 API 层转换成约定的 HTTP 状态和响应。"""

    code: str
    detail: str
    http_status: int


def _locked_cart_for(user):
    """取得并锁住当前用户的购物车，串行化同一购物车的新增操作。"""

    cart, _ = Cart.objects.get_or_create(user=user)
    return Cart.objects.select_for_update().get(pk=cart.pk)


def _purchasable_sku(sku_code):
    """检查 SKU 当前是否可加入购物车；最终能否下单仍须在下单事务中重查。"""

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
    """给购物车操作即时反馈；这里的库存读数不构成购买时的库存承诺。"""

    if quantity > sku.inventory.available_stock:
        raise CartRuleError(
            "insufficient_stock",
            "Requested quantity exceeds the currently available stock.",
        )


def _money(value):
    """统一按人民币分位舍入，避免金额计算出现不一致的小数位。"""

    return value.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)


def _request_fingerprint(address_id):
    """将下单参数规范化后求摘要，用来识别同一个幂等键下的不同请求。"""

    normalized = json.dumps(
        {"address_id": address_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _order_with_details(order_id):
    """一次取出订单、地址快照和商品快照，供 API 返回已落库的事实。"""

    return (
        Order.objects.select_related("address_snapshot")
        .prefetch_related("items")
        .get(pk=order_id)
    )


@transaction.atomic
def create_order_from_cart(*, user, address_id, idempotency_key):
    """用一次数据库事务完成下单、库存预占和购物车清理。

    执行顺序：先锁用户并检查幂等键，再锁地址、购物车和库存；随后用服务端
    当前 SKU 价格计算金额，写入订单与历史快照，把可售库存转为预留库存，
    最后删除本次下单的购物车项。任一步抛错时事务整体回滚，订单、库存、
    流水和购物车不会只成功一半。支付网关调用不在此函数内，应在提交后进行。

    面试重点：相同用户、相同键、相同参数返回原订单；相同键但参数不同返回
    冲突。锁住用户行使同一用户的并发下单按顺序检查，数据库唯一约束提供
    最后一层保护，不能只依赖前端禁用按钮。
    """

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

    # 地址必须属于当前用户；订单保存独立快照，之后编辑地址不会改写历史订单。
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

    # 购物车项与库存都在同一事务里锁住；库存按 SKU 顺序取锁，降低并发死锁风险。
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

    # 不能信任购物车保存时的价格和库存：商品可能下架、涨价或被其他订单买走。
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

    # 金额全部用服务端 Decimal 重算；第一版无优惠、运费为 0，仍保留完整公式。
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

    # 预占只在 available 与 reserved 之间转移，不表示最终售出；支付成功后再核销。
    # expires_at 记录 15 分钟期限，本阶段尚不自动触发超时关闭或库存释放。
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

    # 最后清理购物车；若前面或这里失败，同一事务会恢复库存并保留购物车项。
    CartItem.objects.filter(pk__in=[item.pk for item in cart_items]).delete()
    return _order_with_details(order.pk), True


@transaction.atomic
def add_cart_item(*, user, sku_code, quantity):
    """新增或合并同一 SKU 的购物车项。

    同一购物车内 `(cart, sku)` 唯一；锁购物车后计算目标数量，避免普通重复点击
    意外写出两行。99 件上限与当前库存是加入购物车时的反馈，不保证结账成功；
    下单服务还会重新检查并锁住库存。
    """

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
    """仅更新当前用户已有的购物车项，并在写入前重新检查 SKU 与库存。

    查询条件包含 `cart__user`，因此知道别人的 SKU 编码也不能修改其购物车；
    锁住待更新的行，防止同一行的并发修改互相覆盖。本操作不预占库存。
    """

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
