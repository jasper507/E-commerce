"""项目 API、Django Admin 与健康检查的总路由。"""

from django.contrib import admin
from django.urls import include, path

from config.health import health_check

urlpatterns = [
    path("api/auth/", include("apps.accounts.urls")),
    path("api/addresses/", include("apps.accounts.address_urls")),
    path("api/catalog/", include("apps.catalog.urls")),
    path("api/cart/", include("apps.ordering.urls")),
    path("api/orders/", include("apps.ordering.order_urls")),
    path("api/payments/", include("apps.payments.urls")),
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health"),
]
