from django.urls import path

from apps.accounts.views import (
    AddressCollectionView,
    AddressDetailView,
    AddressSetDefaultView,
)


app_name = "addresses"

urlpatterns = [
    path("", AddressCollectionView.as_view(), name="address-list"),
    path(
        "<int:address_id>/",
        AddressDetailView.as_view(),
        name="address-detail",
    ),
    path(
        "<int:address_id>/default/",
        AddressSetDefaultView.as_view(),
        name="address-set-default",
    ),
]
