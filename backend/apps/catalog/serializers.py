from rest_framework import serializers

from apps.catalog.models import Category, Product, SKU


class ProductSearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, default="", allow_blank=True, max_length=80)


def sku_is_in_stock(sku):
    """目录页只展示是否有货；下单仍要重新读取并锁定权威库存。"""

    inventory = getattr(sku, "inventory", None)
    return bool(inventory and inventory.available_stock > 0)


class PublicCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("slug", "name")


class PublicSKUSerializer(serializers.ModelSerializer):
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = SKU
        fields = ("code", "color", "attributes", "price", "in_stock")

    def get_in_stock(self, sku):
        return sku_is_in_stock(sku)


class PublicProductListSerializer(serializers.ModelSerializer):
    category = PublicCategorySerializer(read_only=True)
    lowest_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        read_only=True,
    )
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = (
            "slug",
            "name",
            "category",
            "lowest_price",
            "in_stock",
        )


class PublicProductDetailSerializer(serializers.ModelSerializer):
    category = PublicCategorySerializer(read_only=True)
    in_stock = serializers.SerializerMethodField()
    skus = PublicSKUSerializer(
        source="public_skus",
        many=True,
        read_only=True,
    )

    class Meta:
        model = Product
        fields = (
            "slug",
            "name",
            "description",
            "category",
            "in_stock",
            "skus",
        )

    def get_in_stock(self, product):
        # public_skus 由详情 View 预取，避免逐个 SKU 再查数据库。
        return any(sku_is_in_stock(sku) for sku in product.public_skus)
