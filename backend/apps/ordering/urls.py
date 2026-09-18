from django.urls import path

from apps.ordering.views import (
    CartDetailView,
    CartItemCollectionView,
    CartItemDetailView,
)


app_name = "ordering"

urlpatterns = [
    path("", CartDetailView.as_view(), name="cart-detail"),
    path("items/", CartItemCollectionView.as_view(), name="cart-item-add"),
    path(
        "items/<str:sku_code>/",
        CartItemDetailView.as_view(),
        name="cart-item-detail",
    ),
]
