"""支付 HTTP 入口：认证/归属在 View，状态与库存规则集中交给 Service。"""

from django.conf import settings

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ordering.models import Order
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.serializers import (
    CreatePaymentAttemptSerializer,
    PaymentAttemptSerializer,
)
from apps.payments.services import (
    PaymentRuleError,
    receive_payment_event,
    reconcile_payment_attempt,
    start_payment,
)


def payment_error_response(error):
    """保留稳定英文 code 供前端判断；detail 给用户显示中文原因。"""

    return Response(
        {"code": error.code, "detail": error.detail},
        status=error.http_status,
    )


class PaymentAttemptCollectionView(APIView):
    """顾客只能列出自己的尝试，并为自己的待支付订单创建尝试。"""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        serializer = CreatePaymentAttemptSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        order_public_id = serializer.validated_data["order_public_id"]
        if not Order.objects.filter(public_id=order_public_id, user=request.user).exists():
            return payment_error_response(
                PaymentRuleError("order_not_found", "订单不存在，或你无权查看这张订单。", 404)
            )
        attempts = (
            PaymentAttempt.objects.filter(
                order__public_id=order_public_id, user=request.user
            )
            .select_related("order")
            .order_by("-created_at", "-id")
        )
        return Response(PaymentAttemptSerializer(attempts, many=True).data)

    def post(self, request):
        serializer = CreatePaymentAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            attempt, created = start_payment(
                user=request.user,
                order_public_id=serializer.validated_data["order_public_id"],
                idempotency_key=request.headers.get("Idempotency-Key", "").strip(),
            )
        except PaymentRuleError as error:
            return payment_error_response(error)
        return Response(
            PaymentAttemptSerializer(attempt).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PaymentAttemptDetailView(APIView):
    """UUID 不是授权凭据；查询仍必须限定为当前用户。"""

    permission_classes = (IsAuthenticated,)

    def get(self, request, public_id):
        attempt = (
            PaymentAttempt.objects.select_related("order")
            .filter(public_id=public_id, user=request.user)
            .first()
        )
        if attempt is None:
            return payment_error_response(
                PaymentRuleError(
                    "attempt_not_found", "支付尝试不存在，或你无权查看。", 404
                )
            )
        return Response(PaymentAttemptSerializer(attempt).data)


class PaymentAttemptReconcileView(APIView):
    """只查原支付尝试的结果；Service 负责 UNKNOWN 状态收敛。"""

    permission_classes = (IsAuthenticated,)

    def post(self, request, public_id):
        try:
            attempt = reconcile_payment_attempt(
                user=request.user, public_id=public_id
            )
        except PaymentRuleError as error:
            return payment_error_response(error)
        return Response(PaymentAttemptSerializer(attempt).data)


class MockPaymentWebhookView(APIView):
    """仅本机开发/测试开放的模拟回调；验签代替顾客 Session 身份。

    先限制访问来源和请求体，再保存事件；真实第三方 Webhook 需要独立协议、
    密钥管理与运行防护，不能把这个 Mock 入口直接当成生产支付接口。
    """

    authentication_classes = ()
    permission_classes = (AllowAny,)
    max_body_bytes = 16 * 1024

    def post(self, request):
        if (
            not settings.IS_LOCAL_ENVIRONMENT
            or request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}
        ):
            return Response(status=status.HTTP_404_NOT_FOUND)
        content_length = request.META.get("CONTENT_LENGTH", "")
        if content_length.isdigit() and int(content_length) > self.max_body_bytes:
            return Response(
                {"code": "event_too_large", "detail": "模拟支付事件内容过大。"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        if len(request._request.body) > self.max_body_bytes:
            return Response(
                {"code": "event_too_large", "detail": "模拟支付事件内容过大。"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            event = receive_payment_event(
                payload=request.data,
                signature=request.headers.get("X-Mock-Signature", ""),
            )
        except PaymentRuleError as error:
            return payment_error_response(error)
        return Response(
            {
                "event_id": event.event_id,
                "processing_status": event.processing_status,
                "error_code": event.error_code,
            },
            status=(
                status.HTTP_200_OK
                if event.processing_status == PaymentEvent.ProcessingStatus.PROCESSED
                else status.HTTP_400_BAD_REQUEST
            ),
        )
