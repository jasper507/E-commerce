"""订单 API：下单/查询，以及已发货订单的顾客确认收货。"""

from django.urls import path

from apps.ordering.views import (
    OrderCancelView,
    OrderCollectionView,
    OrderConfirmReceiptView,
    OrderDetailView,
)


app_name = "orders"

urlpatterns = [
    path("", OrderCollectionView.as_view(), name="order-list"),
    path(
        "<uuid:public_id>/cancel/",
        OrderCancelView.as_view(),
        name="order-cancel",
    ),
    path(
        "<uuid:public_id>/confirm-receipt/",
        OrderConfirmReceiptView.as_view(),
        name="order-confirm-receipt",
    ),
    path("<uuid:public_id>/", OrderDetailView.as_view(), name="order-detail"),
]
