from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ordering.serializers import OrderSerializer
from apps.ordering.services import OrderRuleError
from apps.promotions.http_services import create_flash_sale_order_for_http
from apps.promotions.models import FlashSaleItem
from apps.promotions.serializers import (
    CreateFlashSaleOrderSerializer,
    PublicFlashSaleItemSerializer,
)


class PublicFlashSaleItemCollectionView(generics.ListAPIView):
    """仅展示当前开放、商品可见的活动项；有货提示不承诺抢购成功。"""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    serializer_class = PublicFlashSaleItemSerializer

    def get_queryset(self):
        now = timezone.now()
        return (
            FlashSaleItem.objects.filter(
                is_active=True,
                flash_sale__is_active=True,
                flash_sale__starts_at__lte=now,
                flash_sale__ends_at__gt=now,
                sku__is_active=True,
                sku__product__is_active=True,
                sku__product__category__is_active=True,
            )
            .select_related("flash_sale", "sku__product__category", "sku__inventory")
            .order_by("flash_sale__starts_at", "id")
        )


class FlashSaleOrderCreateView(APIView):
    """Session 和 CSRF 通过后，将下单意图交给同一份秒杀业务事务。"""

    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = CreateFlashSaleOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key:
            return Response(
                {"code": "idempotency_key_missing", "detail": "Idempotency-Key header is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(idempotency_key) > 128 or not idempotency_key.isprintable():
            return Response(
                {
                    "code": "idempotency_key_invalid",
                    "detail": "Idempotency-Key must be 1 to 128 printable characters.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order, created = create_flash_sale_order_for_http(
                user=request.user,
                idempotency_key=idempotency_key,
                **serializer.validated_data,
            )
        except OrderRuleError as error:
            return Response(
                {"code": error.code, "detail": error.detail},
                status=error.http_status,
            )
        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
