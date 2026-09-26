from rest_framework import serializers

from apps.payments.models import PaymentAttempt


class CreatePaymentAttemptSerializer(serializers.Serializer):
    # 客户端只定位订单；可信金额和币种由 Service 从 Order 读取。
    order_public_id = serializers.UUIDField(
        error_messages={
            "required": "请提供订单编号。",
            "invalid": "订单编号格式无效。",
        }
    )


class PaymentAttemptSerializer(serializers.ModelSerializer):
    """只公开顾客理解支付进度所需字段，不泄露内部主键或供应商标识。"""

    order_public_id = serializers.UUIDField(source="order.public_id", read_only=True)
    refund_status = serializers.SerializerMethodField()

    def get_refund_status(self, attempt):
        refund = getattr(attempt, "refund_simulation", None)
        return refund.status if refund is not None else None

    class Meta:
        model = PaymentAttempt
        fields = (
            "public_id",
            "order_public_id",
            "status",
            "amount",
            "currency",
            "refund_status",
            "created_at",
        )
