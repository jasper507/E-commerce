"""可替换的支付网关边界；本阶段只有不处理真钱的 Mock 实现。"""

import hashlib
import hmac
import json
import uuid
from typing import Protocol

from django.conf import settings

from apps.payments.models import MockGatewayPayment, RefundSimulation


def canonical_payload(payload):
    """固定 JSON 字段顺序与格式，使签名计算不受字典插入顺序影响。"""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_payload(payload):
    """为本项目 Mock 事件计算 HMAC；这不是第三方生产支付协议。"""

    secret = f"{settings.SECRET_KEY}:project-one-mock-payment".encode("utf-8")
    return hmac.new(
        secret,
        canonical_payload(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

class PaymentProvider(Protocol):
    """Service 只依赖这些动作，不直接耦合某家供应商的请求格式。"""

    def create_payment(self, attempt): ...

    def query_payment(self, attempt): ...

    def verify_event(self, payload, signature): ...

    def parse_event(self, payload): ...

    def simulate_refund(self, *, order, attempt, reason): ...


class MockGateway:
    """在后端保存模拟 Provider 结果，供创建支付与后续查单复用。"""

    provider = "mock"
    default_outcome = MockGatewayPayment.Outcome.SUCCEEDED

    def _event_for(self, attempt, gateway_payment):
        """按同一 Attempt 和结果生成稳定事件号，便于重复投递时幂等处理。"""

        outcome = gateway_payment.outcome
        payload = {
            "provider": self.provider,
            "event_id": str(uuid.uuid5(attempt.provider_reference, outcome)),
            "provider_reference": str(attempt.provider_reference),
            "order_public_id": str(attempt.order.public_id),
            "amount": f"{attempt.amount:.2f}",
            "currency": attempt.currency,
            "status": outcome,
        }
        return payload, sign_payload(payload)

    def create_payment(self, attempt):
        """只为同一 Attempt 建立一份模拟供应商记录，并返回签名事件。"""

        gateway_payment, _ = MockGatewayPayment.objects.get_or_create(
            attempt=attempt,
            defaults={"outcome": self.default_outcome},
        )
        return self._event_for(attempt, gateway_payment)

    def query_payment(self, attempt):
        """查询原 Attempt；首次网关调用前若进程崩溃，可补建同一模拟记录。

        补建 MockGatewayPayment 不等于创建第二个 PaymentAttempt。真实 Provider
        应使用原供应商请求号进行查询或幂等补发，不能盲目重新扣款。
        """

        gateway_payment = MockGatewayPayment.objects.filter(attempt=attempt).first()
        if gateway_payment is None:
            # Attempt 已提交但尚未调用 Mock Provider 时崩溃：恢复同一次尝试。
            return self.create_payment(attempt)
        return self._event_for(attempt, gateway_payment)

    def verify_event(self, payload, signature):
        """用常量时间比较校验签名；拒绝非 ASCII，避免比较函数抛异常。"""

        return (
            isinstance(signature, str)
            and len(signature) == 64
            and signature.isascii()
            and hmac.compare_digest(
                sign_payload(payload),
                signature,
            )
        )

    def parse_event(self, payload):
        """检查模拟事件的基本形状；金额、币种、订单在 Service 中再核对。"""

        required = (
            "provider",
            "event_id",
            "provider_reference",
            "order_public_id",
            "amount",
            "currency",
            "status",
        )
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(field), str) or not payload[field]
            for field in required
        ):
            raise ValueError("invalid_event_payload")
        if payload["status"] not in {
            MockGatewayPayment.Outcome.SUCCEEDED,
            MockGatewayPayment.Outcome.FAILED,
            MockGatewayPayment.Outcome.PENDING,
        }:
            raise ValueError("invalid_event_status")
        return payload

    def simulate_refund(self, *, order, attempt, reason):
        """Mock 供应商只保存模拟事实；调用方的事务保证状态和库存同进退。"""

        if (
            attempt.order_id != order.pk
            or attempt.provider != self.provider
            or attempt.amount != order.total
            or attempt.currency != order.currency
            or attempt.status != attempt.Status.SUCCEEDED
        ):
            raise ValueError("refund_payment_mismatch")
        refund, _ = RefundSimulation.objects.get_or_create(
            attempt=attempt,
            defaults={
                "order": order,
                "amount": attempt.amount,
                "currency": attempt.currency,
                "reason": reason,
            },
        )
        if (
            refund.order_id != order.pk
            or refund.amount != attempt.amount
            or refund.currency != attempt.currency
        ):
            raise ValueError("refund_record_mismatch")
        return refund
