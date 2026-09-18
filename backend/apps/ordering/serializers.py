from decimal import Decimal

from rest_framework import serializers

from apps.ordering.models import (
    AddressSnapshot,
    CartItem,
    Order,
    OrderItem,
)


def item_availability(item):
    sku = item.sku
    product = sku.product
    inventory = getattr(sku, "inventory", None)

    if not product.category.is_active:
        return False, "category_inactive"
    if not product.is_active:
        return False, "product_inactive"
    if not sku.is_active:
        return False, "sku_inactive"
    if inventory is None or inventory.available_stock < item.quantity:
        return False, "insufficient_stock"
    return True, None


class AddCartItemSerializer(serializers.Serializer):
    sku_code = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, max_value=99)


class UpdateCartItemSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=99)


class CartItemSerializer(serializers.ModelSerializer):
    sku_code = serializers.CharField(source="sku.code", read_only=True)
    product_slug = serializers.CharField(
        source="sku.product.slug",
        read_only=True,
    )
    product_name = serializers.CharField(
        source="sku.product.name",
        read_only=True,
    )
    color = serializers.CharField(source="sku.color", read_only=True)
    attributes = serializers.JSONField(source="sku.attributes", read_only=True)
    unit_price = serializers.DecimalField(
        source="sku.price",
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    line_total = serializers.SerializerMethodField()
    is_available = serializers.SerializerMethodField()
    unavailable_reason = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = (
            "sku_code",
            "product_slug",
            "product_name",
            "color",
            "attributes",
            "unit_price",
            "quantity",
            "line_total",
            "is_available",
            "unavailable_reason",
        )

    def get_line_total(self, item):
        value = item.sku.price * item.quantity
        return f"{value.quantize(Decimal('0.01')):.2f}"

    def get_is_available(self, item):
        available, _ = item_availability(item)
        return available

    def get_unavailable_reason(self, item):
        _, reason = item_availability(item)
        return reason


class CreateOrderSerializer(serializers.Serializer):
    address_id = serializers.IntegerField(min_value=1)


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = (
            "product_name_snapshot",
            "sku_code_snapshot",
            "color_snapshot",
            "attributes_snapshot",
            "unit_price",
            "quantity",
            "discount_amount",
            "line_total",
        )


class AddressSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = AddressSnapshot
        fields = (
            "recipient_name",
            "phone",
            "province",
            "city",
            "district",
            "detail",
        )


class OrderSerializer(serializers.ModelSerializer):
    address = AddressSnapshotSerializer(
        source="address_snapshot",
        read_only=True,
    )
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "public_id",
            "status",
            "currency",
            "subtotal",
            "discount_total",
            "shipping_fee",
            "total",
            "address",
            "items",
            "created_at",
        )
