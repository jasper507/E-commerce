from django.db.models import Exists, Min, OuterRef, Prefetch, Q, prefetch_related_objects
from django.http import Http404
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.catalog.cache import ProductDetailCache
from apps.catalog.models import Category, Product, SKU
from apps.catalog.serializers import (
    PublicCategorySerializer,
    PublicProductDetailSerializer,
    PublicProductListSerializer,
)
from apps.inventory.models import Inventory


class ProductPagination(PageNumberPagination):
    page_size = 20


class PublicCategoryListView(generics.ListAPIView):
    """公开只读目录；Guest 无需 Session，写方法直接得到 405。"""

    serializer_class = PublicCategorySerializer
    authentication_classes = ()
    permission_classes = (AllowAny,)
    pagination_class = None
    queryset = Category.objects.filter(is_active=True).order_by("name", "id")


class PublicProductListView(generics.ListAPIView):
    """只列上架商品，并用数据库聚合计算当前可售规格的浏览摘要。"""

    serializer_class = PublicProductListSerializer
    authentication_classes = ()
    permission_classes = (AllowAny,)
    pagination_class = ProductPagination

    def get_queryset(self):
        # 聚合与 Exists 在数据库执行，避免列表页为每个商品逐个查 SKU/库存。
        # lowest_price 排除下架 SKU；in_stock 只是浏览提示，不是库存承诺。
        queryset = (
            Product.objects.filter(
                is_active=True,
                category__is_active=True,
            )
            .select_related("category")
            .annotate(
                lowest_price=Min(
                    "skus__price",
                    filter=Q(skus__is_active=True),
                ),
                in_stock=Exists(
                    Inventory.objects.filter(
                        sku__product_id=OuterRef("pk"),
                        sku__is_active=True,
                        available_stock__gt=0,
                    )
                ),
            )
            .order_by("name", "id")
        )
        category_slug = self.request.query_params.get("category")
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        return queryset


class PublicProductDetailView(generics.RetrieveAPIView):
    """已有商品每次确认 MySQL 可见性，短期记住真正不存在的地址。"""

    serializer_class = PublicProductDetailSerializer
    authentication_classes = ()
    permission_classes = (AllowAny,)
    lookup_field = "slug"
    queryset = Product.objects.select_related("category")

    def retrieve(self, request, *args, **kwargs):
        detail_cache = ProductDetailCache()
        slug = self.kwargs[self.lookup_field]
        if detail_cache.is_missing(slug):
            raise Http404
        # 初次代号读取失败时，在任何商品 SQL 前申请回源名额。
        with detail_cache.fallback_scope():
            try:
                product = self.get_object()
            except Http404:
                detail_cache.remember_missing(slug)
                raise
            if not product.is_active or not product.category.is_active:
                raise Http404
            payload = detail_cache.get_or_load(product.pk, lambda: self.load_detail(product))
            return Response(payload)

    def load_detail(self, product):
        prefetch_related_objects(
            [product],
            Prefetch(
                "skus",
                queryset=SKU.objects.filter(is_active=True)
                .select_related("inventory")
                .order_by("price", "code"),
                to_attr="public_skus",
            ),
        )
        # ReturnDict 带有 Serializer 引用；共享结果只保留公开数据。
        return dict(self.get_serializer(product).data)
