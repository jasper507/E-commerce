from django.conf import settings
from django.db.models import Exists, Min, OuterRef, Prefetch, Q, prefetch_related_objects
from django.http import Http404
from elasticsearch import ApiError
from elastic_transport import TransportError
from rest_framework import generics
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.cache import ProductDetailCache
from apps.catalog.models import Category, Product, SKU
from apps.catalog.serializers import (
    CandidateSearchQuerySerializer,
    ProductSearchQuerySerializer,
    PublicCategorySerializer,
    PublicProductDetailSerializer,
    PublicProductListSerializer,
)
from apps.catalog.search_read import SearchIndexReader, SearchReadUnavailable
from apps.inventory.models import Inventory


class ProductPagination(PageNumberPagination):
    page_size = 20


def public_product_summaries():
    # 有效SKU最低价与有货提示共用旧列表规则；不是下单的价格/库存承诺。
    return (
        Product.objects.filter(is_active=True, category__is_active=True)
        .select_related("category")
        .annotate(
            lowest_price=Min("skus__price", filter=Q(skus__is_active=True)),
            in_stock=Exists(Inventory.objects.filter(
                sku__product_id=OuterRef("pk"), sku__is_active=True, available_stock__gt=0,
            )),
        )
    )


class SearchServiceUnavailable(APIException):
    status_code = 503
    default_detail = "商品搜索暂不可用，请稍后重试；仍可使用普通商品浏览。"
    default_code = "search_unavailable"


class PublicProductSearchView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    def get(self, request):
        query = CandidateSearchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        if not settings.CATALOG_SEARCH_ENABLED:
            raise SearchServiceUnavailable()
        data = query.validated_data
        page, category = data["page"], data.get("category")
        reader = SearchIndexReader()
        try:
            ids, total = reader.candidates(data["q"], category, page)
        except (SearchReadUnavailable, ApiError, TransportError, ValueError) as exc:
            raise SearchServiceUnavailable() from exc
        if total > reader.page_size * reader.page_limit:
            raise ValidationError({"q": "候选超过10000条，请缩小关键词或分类范围。"})
        if page > 1 and (page - 1) * reader.page_size >= total:
            raise NotFound("候选页不存在。")
        queryset = public_product_summaries().filter(pk__in=ids)
        if category:
            queryset = queryset.filter(category__slug=category)
        current = {product.pk: product for product in queryset}
        # SQL IN不保留相关性顺序；过滤陈旧候选后也不补拉或重做LIKE匹配。
        products = [current[product_id] for product_id in ids if product_id in current]
        return Response({
            "candidate_count": total,
            "next_candidate_page": page + 1 if page < reader.page_limit and page * reader.page_size < total else None,
            "previous_candidate_page": page - 1 if page > 1 else None,
            "results": PublicProductListSerializer(products, many=True).data,
        })


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
        query = ProductSearchQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        queryset = public_product_summaries().order_by("name", "id")
        category_slug = self.request.query_params.get("category")
        if category_slug:
            queryset = queryset.filter(category__slug=category_slug)

        keyword = query.validated_data["q"]
        if keyword:
            # 完整关键词包含匹配；ORM负责转义LIKE通配符，不做分词或相关性排名。
            queryset = queryset.filter(Q(name__icontains=keyword) | Q(description__icontains=keyword))

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
