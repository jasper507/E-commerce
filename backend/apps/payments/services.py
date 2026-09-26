"""支付领域规则：短事务创建尝试，事务外调用网关，再幂等处理事件。"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Order
from apps.payments.gateway import MockGateway, canonical_payload
from apps.payments.models import PaymentAttempt, PaymentEvent, RefundSimulation
from apps.payments.refund_services import simulate_refund


ACTIVE_ATTEMPT_STATUSES = {
    PaymentAttempt.Status.CREATED,
    PaymentAttempt.Status.PENDING,
    PaymentAttempt.Status.UNKNOWN,
}
RETRYABLE_ATTEMPT_STATUSES = {
    PaymentAttempt.Status.FAILED,
    PaymentAttempt.Status.EXPIRED,
    PaymentAttempt.Status.CANCELED,
}


@dataclass(frozen=True)
class PaymentRuleError(Exception):
    """可预期的业务拒绝：code 给程序判断，中文 detail 给用户阅读。"""

    code: str
    detail: str
    http_status: int


class PaymentInvariantError(Exception):
    """内部交易事实不一致；不应伪装成普通的支付失败。"""

    pass


def validate_idempotency_key(key):
    """校验支付请求的幂等键，避免网络重试变成新的支付尝试。"""

    if not key:
        raise PaymentRuleError(
            "idempotency_key_missing",
            "缺少 Idempotency-Key 请求头，无法安全地识别重复支付请求。",
            400,
        )
    if len(key) > 128 or not key.isprintable():
        raise PaymentRuleError(
            "idempotency_key_invalid",
            "Idempotency-Key 必须是 1 到 128 个可打印字符。",
            400,
        )
    return key


def _request_fingerprint(order_public_id):
    """把请求规范化后取摘要：同一 Key 只能指向同一张订单。"""

    normalized = json.dumps(
        {"order_public_id": str(order_public_id)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@transaction.atomic
def create_payment_attempt(*, user, order_public_id, idempotency_key):
    """在一个短事务中创建或重放支付尝试，不在锁内调用网关。

    锁 User 行把该顾客的支付创建请求串行化；先查旧 Key，再检查订单状态，
    因为第一次支付成功后订单已变成 PAID，同 Key 重试仍必须返回原 Attempt。
    新 Attempt 的金额和币种只从服务端 Order 复制，绝不信任前端报价。
    返回 (attempt, created)，created=False 表示幂等重放。
    """

    validate_idempotency_key(idempotency_key)
    fingerprint = _request_fingerprint(order_public_id)

    # 同一顾客即使对不同订单误用同一 Key，也要在同一把 User 行锁下先后检查。
    user.__class__.objects.select_for_update().get(pk=user.pk)
    existing = PaymentAttempt.objects.filter(
        user=user,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise PaymentRuleError(
                "idempotency_conflict",
                "这个 Idempotency-Key 已用于另一笔支付请求；请勿用同一个 Key 支付不同订单。",
                409,
            )
        return existing, False

    order = Order.objects.select_for_update().filter(
        public_id=order_public_id,
        user=user,
    ).first()
    if order is None:
        raise PaymentRuleError("order_not_found", "订单不存在，或你无权查看这张订单。", 404)
    if order.status != Order.Status.PENDING_PAYMENT:
        raise PaymentRuleError(
            "order_not_payable", "订单当前不是待支付状态，不能发起支付。", 409
        )

    prior = list(
        PaymentAttempt.objects.select_for_update()
        .filter(order=order)
        .order_by("attempt_number")
    )
    if any(attempt.status in ACTIVE_ATTEMPT_STATUSES for attempt in prior):
        raise PaymentRuleError(
            "active_attempt_exists",
            "当前还有结果未确认的支付尝试，请先查询原支付结果，不要重复发起。",
            409,
        )
    if any(attempt.status == PaymentAttempt.Status.SUCCEEDED for attempt in prior):
        raise PaymentRuleError(
            "payment_already_succeeded",
            "这张订单已经支付成功，不能再次支付。",
            409,
        )
    if len(prior) >= settings.PAYMENT_MAX_ATTEMPTS:
        raise PaymentRuleError(
            "attempt_limit_reached",
            "这张订单的支付尝试已达到次数上限。",
            409,
        )
    if prior and prior[-1].status not in RETRYABLE_ATTEMPT_STATUSES:
        raise PaymentRuleError(
            "previous_attempt_unresolved",
            "上一次支付尝试尚未结束，请先查询原支付结果。",
            409,
        )

    attempt = PaymentAttempt.objects.create(
        order=order,
        user=user,
        amount=order.total,
        currency=order.currency,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        attempt_number=len(prior) + 1,
        active_marker=1,
    )
    return attempt, True


@transaction.atomic
def mark_attempt_unknown(*, attempt_id):
    """网络结果丢失时标为 UNKNOWN；若并发回调已确认终态，绝不倒退状态。

    超时只代表本地不知道 Provider 的结果，不能据此标为 FAILED 或允许新支付。
    """

    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.status in {
        PaymentAttempt.Status.CREATED,
        PaymentAttempt.Status.PENDING,
    }:
        attempt.status = PaymentAttempt.Status.UNKNOWN
        attempt.save(update_fields=("status", "updated_at"))
    return attempt


def start_payment(*, user, order_public_id, idempotency_key, gateway=None):
    """先提交 Attempt，再在事务外调用 Provider，最后让可信事件确认支付。

    不能持有 Order/Inventory 行锁等网络响应。若 Provider 已处理但响应丢失，
    保留同一个 Attempt 为 UNKNOWN；顾客应查单，而不是换 Key 重新付款。
    """

    attempt, created = create_payment_attempt(
        user=user,
        order_public_id=order_public_id,
        idempotency_key=idempotency_key,
    )
    if not created:
        return attempt, False

    provider = gateway or MockGateway()
    try:
        payload, signature = provider.create_payment(attempt)
    except (TimeoutError, ConnectionError):
        return mark_attempt_unknown(attempt_id=attempt.pk), True

    receive_payment_event(payload=payload, signature=signature)
    attempt.refresh_from_db()
    return attempt, True


def _reject(event, code):
    """记录错误事件的稳定英文原因码，不改变订单、Attempt 或库存。"""

    event.processing_status = PaymentEvent.ProcessingStatus.REJECTED
    event.error_code = code
    event.processed_at = timezone.now()
    event.save(
        update_fields=("attempt", "processing_status", "error_code", "processed_at")
    )
    return event


def _commit_inventory(order):
    """支付成功时核销 MVP-3 已预留的库存，且必须与付款状态同事务。

    例如下单后 available=8、reserved=2：成功支付只把 reserved 改为 0，
    available 仍是 8；再减 available 会构成双重扣库存。先验证全部 Reservation
    都是 RESERVED 且数量足够，再逐条改状态、减 reserved 并写唯一 COMMIT 流水。
    任一步失败会让外层支付事务整体回滚，不能留下“已支付但部分库存未核销”。
    """

    reservations = list(
        InventoryReservation.objects.select_for_update()
        .filter(order_item__order=order)
        .order_by("inventory_id", "id")
    )
    if not reservations or len(reservations) != order.items.count():
        raise PaymentInvariantError("missing_reservation")

    inventories = {
        inventory.pk: inventory
        for inventory in Inventory.objects.select_for_update()
        .filter(pk__in=[reservation.inventory_id for reservation in reservations])
        .order_by("pk")
    }
    for reservation in reservations:
        inventory = inventories.get(reservation.inventory_id)
        if (
            reservation.status != InventoryReservation.Status.RESERVED
            or inventory is None
            or inventory.reserved_stock < reservation.quantity
        ):
            raise PaymentInvariantError("invalid_reservation_state")

    for reservation in reservations:
        inventory = inventories[reservation.inventory_id]
        inventory.reserved_stock -= reservation.quantity
        inventory.save(update_fields=("reserved_stock",))
        reservation.status = InventoryReservation.Status.COMMITTED
        reservation.save(update_fields=("status", "updated_at"))
        InventoryMovement.objects.create(
            inventory=inventory,
            reservation=reservation,
            movement_type=InventoryMovement.Type.COMMIT,
            available_delta=0,
            reserved_delta=-reservation.quantity,
        )


@transaction.atomic
def process_payment_event(*, event_id):
    """在一个事务里验事件、推进状态，并在成功时原子核销库存。

    锁顺序为 Event → Order → Attempt → Reservation → Inventory。相同 Event 的
    重放直接返回；终态后的不同成功 Event 只复用同一退款模拟。
    金额、币种和订单必须与服务端原始交易事实一致，前端页面不参与确认。
    """

    event = PaymentEvent.objects.select_for_update().get(pk=event_id)
    if event.processing_status in {
        PaymentEvent.ProcessingStatus.PROCESSED,
        PaymentEvent.ProcessingStatus.REJECTED,
    }:
        # Inbox 已处理或已明确拒绝：重复投递不能再产生库存副作用。
        return event

    gateway = MockGateway()
    payload = event.raw_payload
    if event.provider != gateway.provider or not gateway.verify_event(
        payload, event.signature
    ):
        return _reject(event, "invalid_signature_or_provider")
    try:
        parsed = gateway.parse_event(payload)
    except ValueError as exc:
        return _reject(event, str(exc))

    try:
        attempt_ref = uuid.UUID(parsed["provider_reference"])
    except ValueError:
        return _reject(event, "invalid_attempt_reference")
    attempt_hint = PaymentAttempt.objects.filter(
        provider_reference=attempt_ref
    ).first()
    if attempt_hint is None:
        return _reject(event, "attempt_not_found")

    order = Order.objects.select_for_update().get(pk=attempt_hint.order_id)
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt_hint.pk)
    event.attempt = attempt

    try:
        amount = Decimal(parsed["amount"])
    except (InvalidOperation, TypeError):
        return _reject(event, "invalid_amount")
    if (
        not amount.is_finite()
        or parsed["provider"] != attempt.provider
        or parsed["order_public_id"] != str(order.public_id)
        or parsed["currency"] != order.currency
        or amount != order.total
        or amount != attempt.amount
        or parsed["currency"] != attempt.currency
    ):
        return _reject(event, "payment_mismatch")

    outcome = parsed["status"]
    if order.status in {Order.Status.CANCELED, Order.Status.EXPIRED} and outcome == "SUCCEEDED":
        # 可信的迟到成功代表资金侧已成功；订单已关闭，库存不可再次核销或释放。
        # RefundSimulation 与 Attempt/Event 一同提交，重放由 Attempt 唯一关系兜底。
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.active_marker = None
        attempt.save(update_fields=("status", "active_marker", "updated_at"))
        simulate_refund(
            order=order, attempt=attempt, reason=RefundSimulation.Reason.LATE_SUCCESS
        )
        event.processing_status = PaymentEvent.ProcessingStatus.PROCESSED
        event.error_code = "late_success_refund_simulated"
        event.processed_at = timezone.now()
        event.save(
            update_fields=("attempt", "processing_status", "error_code", "processed_at")
        )
        return event

    # 已成功的 Attempt 不允许被迟到的失败/处理中事件倒退。
    if attempt.status == PaymentAttempt.Status.SUCCEEDED:
        if outcome != "SUCCEEDED" or order.status not in {
            Order.Status.PAID,
            Order.Status.SHIPPED,
            Order.Status.COMPLETED,
        }:
            return _reject(event, "terminal_attempt_conflict")
    elif attempt.status in RETRYABLE_ATTEMPT_STATUSES:
        return _reject(event, "terminal_attempt_conflict")
    elif order.status != Order.Status.PENDING_PAYMENT:
        return _reject(event, "order_state_conflict")
    elif outcome == "PENDING":
        attempt.status = PaymentAttempt.Status.PENDING
        attempt.save(update_fields=("status", "updated_at"))
    elif outcome == "FAILED":
        attempt.status = PaymentAttempt.Status.FAILED
        attempt.active_marker = None
        attempt.save(update_fields=("status", "active_marker", "updated_at"))
    elif outcome == "SUCCEEDED":
        _commit_inventory(order)
        attempt.status = PaymentAttempt.Status.SUCCEEDED
        attempt.active_marker = None
        attempt.save(update_fields=("status", "active_marker", "updated_at"))
        order.status = Order.Status.PAID
        order.save(update_fields=("status", "updated_at"))

    event.processing_status = PaymentEvent.ProcessingStatus.PROCESSED
    event.error_code = ""
    event.processed_at = timezone.now()
    event.save(
        update_fields=(
            "attempt", "processing_status", "error_code", "processed_at"
        )
    )
    return event


def receive_payment_event(*, payload, signature):
    """先独立持久化原始事件，再在另一事务中调用处理器。

    这里预先验签只为决定 Inbox 去重键：无效签名不能占用合法 event_id。
    完整验签、金额/币种/状态判断仍在原文入箱后的 process_payment_event 中。
    处理抛异常时业务事务回滚，Inbox 单独标 ERROR，之后可凭同一事件重试。
    """

    provider = payload.get("provider") if isinstance(payload, dict) else None
    external_id = payload.get("event_id") if isinstance(payload, dict) else None
    if not isinstance(provider, str) or not provider or len(provider) > 32:
        provider = "unrecognized"
    if not isinstance(signature, str):
        signature = ""
    # 无效签名使用内容摘要形成的内部事件号：留下审计原文，且不抢占未来合法事件号。
    valid_signature = provider == MockGateway.provider and MockGateway().verify_event(
        payload, signature
    )
    if (
        not valid_signature
        or not isinstance(external_id, str)
        or not external_id
        or len(external_id) > 128
    ):
        digest = hashlib.sha256(
            f"{canonical_payload(payload)}:{signature[:128]}".encode("utf-8")
        ).hexdigest()
        external_id = f"invalid-{digest}"

    with transaction.atomic():
        event, _ = PaymentEvent.objects.get_or_create(
            provider=provider,
            event_id=external_id,
            defaults={
                "raw_payload": payload,
                "signature": signature[:128],
            },
        )
    if event.raw_payload != payload:
        raise PaymentRuleError(
            "event_id_conflict",
            "同一个支付事件编号对应了不同内容，已拒绝处理。",
            409,
        )
    if event.processing_status in {
        PaymentEvent.ProcessingStatus.RECEIVED,
        PaymentEvent.ProcessingStatus.ERROR,
    }:
        try:
            return process_payment_event(event_id=event.pk)
        except Exception:
            with transaction.atomic():
                failed_event = PaymentEvent.objects.select_for_update().get(pk=event.pk)
                if failed_event.processing_status not in {
                    PaymentEvent.ProcessingStatus.PROCESSED,
                    PaymentEvent.ProcessingStatus.REJECTED,
                }:
                    failed_event.processing_status = PaymentEvent.ProcessingStatus.ERROR
                    failed_event.error_code = "processing_error"
                    failed_event.processed_at = timezone.now()
                    failed_event.save(
                        update_fields=(
                            "processing_status", "error_code", "processed_at"
                        )
                    )
            raise
    return event


def reconcile_payment_attempt(*, user, public_id, gateway=None):
    """只查询顾客自己的原 Attempt，不创建第二次支付尝试。

    CREATED/PENDING/UNKNOWN 可查 Provider；查单结果仍走统一事件校验与状态机。
    再次超时继续保持 UNKNOWN，已成功/失败的终态则直接返回。
    """

    attempt = (
        PaymentAttempt.objects.select_related("order")
        .filter(public_id=public_id, user=user)
        .first()
    )
    if attempt is None:
        raise PaymentRuleError("attempt_not_found", "支付尝试不存在，或你无权查看。", 404)
    if attempt.status not in ACTIVE_ATTEMPT_STATUSES:
        return attempt

    provider = gateway or MockGateway()
    try:
        payload, signature = provider.query_payment(attempt)
    except (TimeoutError, ConnectionError):
        return mark_attempt_unknown(attempt_id=attempt.pk)
    receive_payment_event(payload=payload, signature=signature)
    attempt.refresh_from_db()
    return attempt
