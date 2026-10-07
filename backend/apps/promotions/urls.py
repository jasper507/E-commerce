from django.urls import path

from apps.promotions.views import FlashSaleOrderCreateView, PublicFlashSaleItemCollectionView


app_name = "promotions"

urlpatterns = [
    path("items/", PublicFlashSaleItemCollectionView.as_view(), name="flash-sale-items"),
    path("orders/", FlashSaleOrderCreateView.as_view(), name="flash-sale-orders"),
]
