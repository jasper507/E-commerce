"""秒杀下单的 MySQL 基线；当前仅供隔离实验，不开放 HTTP 入口。"""

import hashlib
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.models import Address
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import AddressSnapshot, Order, OrderItem
from apps.ordering.services import (
    ZERO_MONEY,
    OrderRuleError,
    _money,
    _order_with_details,
)
from apps.promotions.models import FlashSaleItem, FlashSalePurchase


User = get_user_model()


def _request_fingerprint(*, flash_sale_item_id, address_id):
    normalized = json.dumps(
        {"flash_sale_item_id": flash_sale_item_id, "address_id": address_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _reserve_flash_sale_inventory(sku):
    """调用者持有下单事务；失败必须回滚此前订单与资格。"""

    try:
        updated = Inventory.objects.filter(sku=sku, available_stock__gte=1).update(
            available_stock=F("available_stock") - 1,
            reserved_stock=F("reserved_stock") + 1,
        )
    except DatabaseError as exc:
        # 仅将已知库存锁竞争转换为繁忙，不吞掉连接等其他数据库故障。
        if not exc.args or exc.args[0] not in (1205, 1213):
            raise
        raise OrderRuleError("stock_busy", "库存正在更新，请重试原请求。", 409) from exc
    if updated != 1:
        raise OrderRuleError("insufficient_stock", "活动商品库存不足。", 409)
    return Inventory.objects.get(sku=sku)


@transaction.atomic
def create_flash_sale_order(*, user, flash_sale_item_id, address_id, idempotency_key):
    """购买一件活动 SKU：订单、名额、预占和流水共同提交或回滚。"""

    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key
        or len(idempotency_key) > 128
        or not idempotency_key.isprintable()
    ):
        raise OrderRuleError("idempotency_key_invalid", "下单需要有效的幂等键。", 400)

    locked_user = User.objects.select_for_update().get(pk=user.pk)
    fingerprint = _request_fingerprint(
        flash_sale_item_id=flash_sale_item_id, address_id=address_id
    )
    existing = Order.objects.filter(
        user=locked_user,
        creation_scope=Order.CreationScope.FLASH_SALE,
        idempotency_key=idempotency_key,
    ).first()
    # 重放读取已经提交的事实，不受活动结束、改价或当前名额占用影响。
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise OrderRuleError("idempotency_conflict", "该幂等键已用于不同请求。", 409)
        return _order_with_details(existing.pk), False

    address = Address.objects.select_for_update().filter(
        pk=address_id, user=locked_user
    ).first()
    if address is None:
        raise OrderRuleError("address_not_found", "收货地址不存在。", 404)

    item = (
        FlashSaleItem.objects.select_related("flash_sale", "sku__product__category")
        .filter(pk=flash_sale_item_id)
        .first()
    )
    if item is None:
        raise OrderRuleError("flash_sale_item_not_found", "活动商品不存在。", 404)
    now = timezone.now()
    if (
        not item.is_active
        or not item.flash_sale.is_active
        or not item.flash_sale.starts_at <= now < item.flash_sale.ends_at
    ):
        raise OrderRuleError("flash_sale_unavailable", "活动当前未开放。", 409)

    sku = item.sku
    for active, code in (
        (sku.product.category.is_active, "category_inactive"),
        (sku.product.is_active, "product_inactive"),
        (sku.is_active, "sku_inactive"),
    ):
        if not active:
            raise OrderRuleError(code, "活动商品当前不可购买。", 409)

    if FlashSalePurchase.objects.filter(
        customer=locked_user, flash_sale_item=item, blocking_marker=1
    ).exists():
        raise OrderRuleError("flash_sale_limit_reached", "该活动已有占用或成功购买。", 409)

    price = _money(item.price)
    order = Order.objects.create(
        user=locked_user,
        creation_scope=Order.CreationScope.FLASH_SALE,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        subtotal=price,
        discount_total=ZERO_MONEY,
        shipping_fee=ZERO_MONEY,
        total=price,
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
    order_item = OrderItem.objects.create(
        order=order,
        sku=sku,
        product_name_snapshot=sku.product.name,
        sku_code_snapshot=sku.code,
        color_snapshot=sku.color,
        attributes_snapshot=sku.attributes,
        unit_price=price,
        quantity=1,
        discount_amount=ZERO_MONEY,
        line_total=price,
    )
    # 先取得名额，再锁库存；避免与将来的关闭流程反向等待。
    try:
        FlashSalePurchase.objects.create(
            customer=locked_user, flash_sale_item=item, order=order
        )
    except IntegrityError as exc:
        if (
            exc.args
            and exc.args[0] == 1062
            and "promotions_purchase_one_blocking" in str(exc)
        ):
            raise OrderRuleError(
                "flash_sale_limit_reached", "该活动已有占用或成功购买。", 409
            ) from exc
        raise

    inventory = _reserve_flash_sale_inventory(sku)
    reservation = InventoryReservation.objects.create(
        order_item=order_item,
        inventory=inventory,
        quantity=1,
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    InventoryMovement.objects.create(
        inventory=inventory,
        reservation=reservation,
        movement_type=InventoryMovement.Type.RESERVE,
        available_delta=-1,
        reserved_delta=1,
    )
    return _order_with_details(order.pk), True
