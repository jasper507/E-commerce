"""秒杀资格只随原订单事务转换，不按顾客查找另一张订单的当前名额。"""

from django.db import connection

from apps.ordering.models import Order
from apps.promotions.models import FlashSalePurchase


class FlashSalePurchaseInvariantError(Exception):
    """购买记录与交易事实不一致；调用者必须回滚并保留调查记录。"""


def lock_flash_sale_purchase(order, *, expected_status):
    """调用者已锁 Order；在库存锁之前取得该订单自己的购买记录锁。"""

    if order.creation_scope != Order.CreationScope.FLASH_SALE:
        return None
    if not connection.in_atomic_block:
        raise FlashSalePurchaseInvariantError("purchase_requires_order_transaction")
    purchase = FlashSalePurchase.objects.select_for_update().filter(order=order).first()
    if purchase is None:
        raise FlashSalePurchaseInvariantError("missing_flash_sale_purchase")
    marker = None if expected_status == FlashSalePurchase.Status.RELEASED else 1
    if (
        purchase.customer_id != order.user_id
        or purchase.status != expected_status
        or purchase.blocking_marker != marker
    ):
        raise FlashSalePurchaseInvariantError("flash_sale_purchase_state_mismatch")
    return purchase


def transition_flash_sale_purchase(purchase, *, target_status):
    """调用者持有 Order/Purchase 锁；转换与订单、库存及事件共同提交。"""

    if purchase is None:
        return
    if (
        not connection.in_atomic_block
        or purchase.status != FlashSalePurchase.Status.HELD
        or purchase.blocking_marker != 1
        or target_status not in {
            FlashSalePurchase.Status.CONSUMED,
            FlashSalePurchase.Status.RELEASED,
        }
    ):
        raise FlashSalePurchaseInvariantError("invalid_flash_sale_purchase_transition")
    purchase.status = target_status
    purchase.blocking_marker = (
        None if target_status == FlashSalePurchase.Status.RELEASED else 1
    )
    purchase.save(update_fields=("status", "blocking_marker", "updated_at"))
