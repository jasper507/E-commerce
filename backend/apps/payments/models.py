"""支付审计事实：一次尝试、收到的事件，以及 Mock 供应商侧的结果。"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models


class PaymentAttempt(models.Model):
    """一次主动支付请求；同一订单可有失败历史，但只能有一个活跃尝试。

    Order 记录交易，Attempt 记录一次支付过程；不能把失败 Attempt 直接当成
    失败订单。public_id 给客户端，内部主键和 provider_reference 不对外暴露。
    """

    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        PENDING = "PENDING", "Pending"
        UNKNOWN = "UNKNOWN", "Unknown"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        EXPIRED = "EXPIRED", "Expired"
        CANCELED = "CANCELED", "Canceled"

    order = models.ForeignKey(
        "ordering.Order",
        on_delete=models.PROTECT,
        related_name="payment_attempts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payment_attempts",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider = models.CharField(max_length=32, default="mock", editable=False)
    provider_reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.CREATED,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64, editable=False)
    attempt_number = models.PositiveSmallIntegerField()
    # MySQL 唯一键允许多个 NULL，却只允许同一订单的一条 (order, 1)：
    # 活跃 Attempt 填 1，终态填 NULL，以数据库约束兜底“最多一个活跃尝试”。
    active_marker = models.PositiveSmallIntegerField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = (
            models.UniqueConstraint(
                fields=("user", "idempotency_key"),
                name="payments_attempt_unique_user_key",
            ),
            models.UniqueConstraint(
                fields=("order", "active_marker"),
                name="payments_attempt_one_active_per_order",
            ),
            models.UniqueConstraint(
                fields=("order", "attempt_number"),
                name="payments_attempt_unique_order_number",
            ),
            models.CheckConstraint(
                condition=(models.Q(active_marker__isnull=True) | models.Q(active_marker=1)),
                name="payments_attempt_active_null_or_1",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal("0.00")),
                name="payments_attempt_amount_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_number__gte=1),
                name="payments_attempt_number_gte_1",
            ),
        )

    def __str__(self):
        return f"{self.public_id} ({self.status})"


class PaymentEvent(models.Model):
    """Webhook Inbox：即使事件被拒绝或处理失败，也保留原始输入供排查。

    (provider, event_id) 唯一防同一事件重复入箱；处理结果与原文分开记录。
    error_code 是供程序/审计查询的稳定标识，因此保留英文，不当作中文提示。
    """

    class ProcessingStatus(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        PROCESSING = "PROCESSING", "Processing"
        PROCESSED = "PROCESSED", "Processed"
        REJECTED = "REJECTED", "Rejected"
        ERROR = "ERROR", "Error"

    class FailureKind(models.TextChoices):
        NONE = "NONE", "None"
        TRANSIENT = "TRANSIENT", "Transient"
        BLOCKED = "BLOCKED", "Needs investigation"
        EXHAUSTED = "EXHAUSTED", "Attempts exhausted"

    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=128)
    raw_payload = models.JSONField()
    signature = models.CharField(max_length=128, blank=True)
    attempt = models.ForeignKey(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
    )
    processing_status = models.CharField(
        max_length=16,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    error_code = models.CharField(max_length=64, blank=True)
    # 总次数包含首次；必须在业务事务之外提交，不能因业务回滚而重置预算。
    processing_attempt_count = models.PositiveSmallIntegerField(default=0, editable=False)
    failure_kind = models.CharField(
        max_length=16, choices=FailureKind.choices, default=FailureKind.NONE, editable=False
    )
    next_retry_at = models.DateTimeField(null=True, blank=True, editable=False)
    # 历史 PROCESSING 没有领取时刻证据；空租约不能根据收件时间猜测接管。
    processing_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    processing_lease_until = models.DateTimeField(null=True, blank=True, editable=False)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("received_at", "id")
        constraints = (
            models.UniqueConstraint(
                fields=("provider", "event_id"),
                name="payments_event_unique_provider_id",
            ),
        )

    def __str__(self):
        return f"{self.provider}:{self.event_id} ({self.processing_status})"


class MockGatewayPayment(models.Model):
    """模拟供应商自己的状态；查单读取它，而不是相信浏览器提示。"""

    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    attempt = models.OneToOneField(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name="mock_gateway_payment",
    )
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Mock payment for {self.attempt.public_id} ({self.outcome})"


class RefundSimulation(models.Model):
    """一次成功支付对应最多一次模拟退款；不代表真实资金已退回。"""

    class Status(models.TextChoices):
        SIMULATED = "SIMULATED", "Simulated"

    class Reason(models.TextChoices):
        LATE_SUCCESS = "LATE_SUCCESS", "Payment succeeded after order closed"
        PAID_CANCEL = "PAID_CANCEL", "Paid order canceled before shipment"

    attempt = models.OneToOneField(
        PaymentAttempt,
        on_delete=models.PROTECT,
        related_name="refund_simulation",
    )
    order = models.ForeignKey(
        "ordering.Order",
        on_delete=models.PROTECT,
        related_name="refund_simulations",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=16, choices=Reason.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.SIMULATED
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Mock refund for attempt {self.attempt_id} ({self.reason})"
