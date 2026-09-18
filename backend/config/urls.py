"""Root URL configuration for Project One."""

from django.contrib import admin
from django.urls import include, path

from config.health import health_check

urlpatterns = [
    path("api/auth/", include("apps.accounts.urls")),
    path("api/addresses/", include("apps.accounts.address_urls")),
    path("api/catalog/", include("apps.catalog.urls")),
    path("api/cart/", include("apps.ordering.urls")),
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health"),
]
