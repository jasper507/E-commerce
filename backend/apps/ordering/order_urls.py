from django.urls import path

from apps.ordering.views import OrderCollectionView, OrderDetailView


app_name = "orders"

urlpatterns = [
    path("", OrderCollectionView.as_view(), name="order-list"),
    path("<uuid:public_id>/", OrderDetailView.as_view(), name="order-detail"),
]
