from django.urls import path

from apps.catalog.views import (
    PublicCategoryListView,
    PublicProductDetailView,
    PublicProductListView,
    PublicProductSearchView,
)


app_name = "catalog"

urlpatterns = [
    path("categories/", PublicCategoryListView.as_view(), name="category-list"),
    path("products/", PublicProductListView.as_view(), name="product-list"),
    path("search/", PublicProductSearchView.as_view(), name="product-search"),
    path(
        "products/<slug:slug>/",
        PublicProductDetailView.as_view(),
        name="product-detail",
    ),
]
