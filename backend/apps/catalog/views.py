from django.db.models import Exists, Min, OuterRef, Prefetch, Q
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny

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
    serializer_class = PublicCategorySerializer
    authentication_classes = ()
    permission_classes = (AllowAny,)
    pagination_class = None
    queryset = Category.objects.filter(is_active=True).order_by("name", "id")


class PublicProductListView(generics.ListAPIView):
    serializer_class = PublicProductListSerializer
    authentication_classes = ()
    permission_classes = (AllowAny,)
    pagination_class = ProductPagination

    def get_queryset(self):
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
    serializer_class = PublicProductDetailSerializer
    authentication_classes = ()
    permission_classes = (AllowAny,)
    lookup_field = "slug"
    queryset = (
        Product.objects.filter(
            is_active=True,
            category__is_active=True,
        )
        .select_related("category")
        .prefetch_related(
            Prefetch(
                "skus",
                queryset=SKU.objects.filter(is_active=True)
                .select_related("inventory")
                .order_by("price", "code"),
                to_attr="public_skus",
            )
        )
    )
