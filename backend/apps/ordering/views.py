from decimal import Decimal

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ordering.models import CartItem
from apps.ordering.serializers import (
    AddCartItemSerializer,
    CartItemSerializer,
    UpdateCartItemSerializer,
)
from apps.ordering.services import (
    CartRuleError,
    add_cart_item,
    update_cart_item,
)


def cart_items_for(user):
    return (
        CartItem.objects.filter(cart__user=user)
        .select_related("sku__product__category", "sku__inventory")
        .order_by("created_at", "id")
    )


def cart_payload(user):
    items = list(cart_items_for(user))
    subtotal = sum(
        (item.sku.price * item.quantity for item in items),
        start=Decimal("0.00"),
    )
    return {
        "items": CartItemSerializer(items, many=True).data,
        "item_count": len(items),
        "total_quantity": sum(item.quantity for item in items),
        "subtotal": f"{subtotal.quantize(Decimal('0.01')):.2f}",
    }


def rule_error_response(error):
    response_status = (
        status.HTTP_404_NOT_FOUND
        if error.code == "cart_item_not_found"
        else status.HTTP_400_BAD_REQUEST
    )
    return Response(
        {"code": error.code, "detail": error.detail},
        status=response_status,
    )


class CartDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response(cart_payload(request.user))


class CartItemCollectionView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = AddCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            _, created = add_cart_item(
                user=request.user,
                **serializer.validated_data,
            )
        except CartRuleError as error:
            return rule_error_response(error)

        return Response(
            cart_payload(request.user),
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )


class CartItemDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, sku_code):
        serializer = UpdateCartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            update_cart_item(
                user=request.user,
                sku_code=sku_code,
                **serializer.validated_data,
            )
        except CartRuleError as error:
            return rule_error_response(error)

        return Response(cart_payload(request.user))

    def delete(self, request, sku_code):
        deleted, _ = CartItem.objects.filter(
            cart__user=request.user,
            sku__code=sku_code,
        ).delete()
        if deleted == 0:
            return Response(
                {"detail": "Cart item does not exist."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
