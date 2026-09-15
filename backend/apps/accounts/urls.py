from django.urls import path

from apps.accounts.views import (
    CsrfTokenView,
    CurrentUserView,
    LoginView,
    LogoutView,
    RegisterView,
)


app_name = "accounts"

urlpatterns = [
    path("csrf/", CsrfTokenView.as_view(), name="csrf"),
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("me/", CurrentUserView.as_view(), name="me"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
