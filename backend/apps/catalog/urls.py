from django.urls import path

from apps.catalog.views import (
    PublicCategoryListView,
    PublicProductDetailView,
    PublicProductListView,
)


app_name = "catalog"

urlpatterns = [
    path("categories/", PublicCategoryListView.as_view(), name="category-list"),
    path("products/", PublicProductListView.as_view(), name="product-list"),
    path(
        "products/<slug:slug>/",
        PublicProductDetailView.as_view(),
        name="product-detail",
    ),
]
