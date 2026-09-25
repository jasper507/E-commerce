from django.urls import path

from apps.payments.views import (
    MockPaymentWebhookView,
    PaymentAttemptCollectionView,
    PaymentAttemptDetailView,
    PaymentAttemptReconcileView,
)


app_name = "payments"

urlpatterns = [
    path("attempts/", PaymentAttemptCollectionView.as_view(), name="attempt-list"),
    path(
        "attempts/<uuid:public_id>/reconcile/",
        PaymentAttemptReconcileView.as_view(),
        name="attempt-reconcile",
    ),
    path(
        "attempts/<uuid:public_id>/",
        PaymentAttemptDetailView.as_view(),
        name="attempt-detail",
    ),
    path("mock/webhook/", MockPaymentWebhookView.as_view(), name="mock-webhook"),
]
