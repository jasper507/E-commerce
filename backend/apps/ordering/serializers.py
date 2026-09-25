from decimal import Decimal

from rest_framework import serializers

from apps.ordering.models import (
    AddressSnapshot,
    CartItem,
    Order,
    OrderItem,
)


def item_availability(item):
    """给购物车页面显示当前可用性；真正的库存判定发生在下单事务里。"""

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
    """只校验加入购物车请求的字段和数量范围。"""

    sku_code = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, max_value=99)


class UpdateCartItemSerializer(serializers.Serializer):
    """只接收目标数量，SKU 身份来自 URL。"""

    quantity = serializers.IntegerField(min_value=1, max_value=99)


class CartItemSerializer(serializers.ModelSerializer):
    """展示 SKU 的当前信息；这里不是成交后的商品快照。"""

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
    """客户端只选择地址，价格和应付金额必须由后端读取 SKU 后计算。"""

    address_id = serializers.IntegerField(min_value=1)


class OrderItemSerializer(serializers.ModelSerializer):
    """输出下单时保存的商品与价格快照。"""

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
    """输出下单时保存的收货地址，不依赖顾客当前地址。"""

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
    """把订单事实、商品快照和地址快照组合为顾客可见响应。"""

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
