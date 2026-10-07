from rest_framework import serializers

from apps.catalog.serializers import sku_is_in_stock
from apps.promotions.models import FlashSaleItem


class PublicFlashSaleItemSerializer(serializers.ModelSerializer):
    """活动页只展示商品与有货提示，成交价格和库存仍由下单服务核对。"""

    sale_name = serializers.CharField(source="flash_sale.name", read_only=True)
    starts_at = serializers.DateTimeField(source="flash_sale.starts_at", read_only=True)
    ends_at = serializers.DateTimeField(source="flash_sale.ends_at", read_only=True)
    product_slug = serializers.CharField(source="sku.product.slug", read_only=True)
    product_name = serializers.CharField(source="sku.product.name", read_only=True)
    sku_code = serializers.CharField(source="sku.code", read_only=True)
    color = serializers.CharField(source="sku.color", read_only=True)
    attributes = serializers.JSONField(source="sku.attributes", read_only=True)
    currency = serializers.CharField(read_only=True, default="CNY")
    quantity = serializers.IntegerField(read_only=True, default=1)
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = FlashSaleItem
        fields = (
            "id", "sale_name", "starts_at", "ends_at", "product_slug", "product_name",
            "sku_code", "color", "attributes", "price", "currency", "quantity", "in_stock",
        )

    def get_in_stock(self, item):
        return sku_is_in_stock(item.sku)


class CreateFlashSaleOrderSerializer(serializers.Serializer):
    """只接收活动商品和地址，用户、数量与成交金额不由客户端指定。"""

    flash_sale_item_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
    address_id = serializers.IntegerField(min_value=1, max_value=9223372036854775807)
