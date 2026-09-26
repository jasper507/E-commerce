"""订单取消和到期关闭：按支付状态选择一次性库存处理。"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Order, Shipment
from apps.payments.models import PaymentAttempt, RefundSimulation
from apps.payments.refund_services import simulate_refund


UNRESOLVED_PAYMENT_STATUSES = {
    PaymentAttempt.Status.CREATED,
    PaymentAttempt.Status.PENDING,
    PaymentAttempt.Status.UNKNOWN,
}


@dataclass(frozen=True)
class OrderLifecycleError(Exception):
    code: str
    detail: str
    http_status: int


class OrderLifecycleInvariantError(Exception):
    """订单、预占与库存账不一致；必须回滚，不能伪装成普通业务拒绝。"""


def _close_pending_order_locked(order, *, target_status):
    """调用者已锁住 Order；所有状态与库存变化留在同一个事务。"""

    attempts = list(
        PaymentAttempt.objects.select_for_update()
        .filter(order=order)
        .order_by("id")
    )
    if any(attempt.status in UNRESOLVED_PAYMENT_STATUSES for attempt in attempts):
        raise OrderLifecycleError(
            "payment_unresolved",
            "支付结果尚未确认，请先查询原支付结果，再关闭订单。",
            409,
        )
    if any(attempt.status == PaymentAttempt.Status.SUCCEEDED for attempt in attempts):
        raise OrderLifecycleInvariantError("paid_attempt_on_pending_order")

    reservations = list(
        InventoryReservation.objects.select_for_update()
        .filter(order_item__order=order)
        .order_by("inventory_id", "id")
    )
    if not reservations or len(reservations) != order.items.count():
        raise OrderLifecycleInvariantError("missing_reservation")

    inventories = {
        inventory.pk: inventory
        for inventory in Inventory.objects.select_for_update()
        .filter(pk__in=[reservation.inventory_id for reservation in reservations])
        .order_by("pk")
    }
    required_by_inventory = {}
    for reservation in reservations:
        if reservation.status != InventoryReservation.Status.RESERVED:
            raise OrderLifecycleInvariantError("invalid_reservation_state")
        required_by_inventory[reservation.inventory_id] = (
            required_by_inventory.get(reservation.inventory_id, 0)
            + reservation.quantity
        )
    for inventory_id, quantity in required_by_inventory.items():
        inventory = inventories.get(inventory_id)
        if inventory is None or inventory.reserved_stock < quantity:
            raise OrderLifecycleInvariantError("insufficient_reserved_stock")

    for reservation in reservations:
        inventory = inventories[reservation.inventory_id]
        inventory.available_stock += reservation.quantity
        inventory.reserved_stock -= reservation.quantity
        inventory.save(update_fields=("available_stock", "reserved_stock"))
        reservation.status = InventoryReservation.Status.RELEASED
        reservation.save(update_fields=("status", "updated_at"))
        InventoryMovement.objects.create(
            inventory=inventory,
            reservation=reservation,
            movement_type=InventoryMovement.Type.RELEASE,
            available_delta=reservation.quantity,
            reserved_delta=-reservation.quantity,
        )

    order.status = target_status
    order.save(update_fields=("status", "updated_at"))
    return order


def _cancel_paid_order_locked(order):
    """调用者已锁住 Order；已核销库存只恢复 available，不再操作 reserved。"""

    if Shipment.objects.filter(order=order).exists():
        raise OrderLifecycleError(
            "order_not_cancelable", "订单已经发货，不能在此取消。", 409
        )
    attempts = list(
        PaymentAttempt.objects.select_for_update()
        .filter(order=order, status=PaymentAttempt.Status.SUCCEEDED)
        .order_by("id")
    )
    if len(attempts) != 1:
        raise OrderLifecycleInvariantError("missing_or_duplicate_successful_attempt")
    attempt = attempts[0]

    reservations = list(
        InventoryReservation.objects.select_for_update()
        .filter(order_item__order=order)
        .order_by("inventory_id", "id")
    )
    if not reservations or len(reservations) != order.items.count():
        raise OrderLifecycleInvariantError("missing_reservation")
    if any(reservation.status != InventoryReservation.Status.COMMITTED for reservation in reservations):
        raise OrderLifecycleInvariantError("invalid_reservation_state")
    inventories = {
        inventory.pk: inventory
        for inventory in Inventory.objects.select_for_update()
        .filter(pk__in=[reservation.inventory_id for reservation in reservations])
        .order_by("pk")
    }
    if any(reservation.inventory_id not in inventories for reservation in reservations):
        raise OrderLifecycleInvariantError("missing_inventory")

    for reservation in reservations:
        inventory = inventories[reservation.inventory_id]
        inventory.available_stock += reservation.quantity
        inventory.save(update_fields=("available_stock",))
        reservation.status = InventoryReservation.Status.RESTORED
        reservation.save(update_fields=("status", "updated_at"))
        InventoryMovement.objects.create(
            inventory=inventory,
            reservation=reservation,
            movement_type=InventoryMovement.Type.RESTORE,
            available_delta=reservation.quantity,
            reserved_delta=0,
        )

    simulate_refund(
        order=order, attempt=attempt, reason=RefundSimulation.Reason.PAID_CANCEL
    )
    order.status = Order.Status.CANCELED
    order.save(update_fields=("status", "updated_at"))
    return order


@transaction.atomic
def cancel_order(*, public_id, user):
    """顾客取消自己的待支付或未发货已支付订单；重复请求不再改库存。"""

    order = Order.objects.select_for_update().filter(
        public_id=public_id, user=user
    ).first()
    if order is None:
        raise OrderLifecycleError("order_not_found", "订单不存在，或你无权取消。", 404)
    if order.status == Order.Status.CANCELED:
        return order, False
    if order.status == Order.Status.PENDING_PAYMENT:
        return _close_pending_order_locked(order, target_status=Order.Status.CANCELED), True
    if order.status == Order.Status.PAID:
        return _cancel_paid_order_locked(order), True
    else:
        raise OrderLifecycleError(
            "order_not_cancelable", "这张订单当前不能取消。", 409
        )


@transaction.atomic
def expire_pending_order(*, order_id, as_of=None):
    """在指定时刻关闭确实已到期的订单；锁内重查避免与支付竞争。"""

    as_of = as_of or timezone.now()
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != Order.Status.PENDING_PAYMENT:
        return order, False
    if not InventoryReservation.objects.filter(
        order_item__order=order, expires_at__lte=as_of
    ).exists():
        return order, False
    return _close_pending_order_locked(order, target_status=Order.Status.EXPIRED), True


def expire_due_orders(*, as_of=None):
    """批量扫描到期订单；每单独立事务，未确认支付跳过待查单。"""

    as_of = as_of or timezone.now()
    order_ids = list(
        InventoryReservation.objects.filter(
            expires_at__lte=as_of,
            order_item__order__status=Order.Status.PENDING_PAYMENT,
        )
        .order_by("order_item__order_id")
        .values_list("order_item__order_id", flat=True)
        .distinct()
    )
    expired = 0
    unresolved = 0
    for order_id in order_ids:
        try:
            _, changed = expire_pending_order(order_id=order_id, as_of=as_of)
        except OrderLifecycleError as exc:
            if exc.code != "payment_unresolved":
                raise
            unresolved += 1
        else:
            expired += int(changed)
    return expired, unresolved
