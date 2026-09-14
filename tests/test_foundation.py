import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse


@pytest.mark.django_db
def test_health_check_uses_mysql(client):
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.django_db
def test_project_uses_custom_user_model():
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="foundation_user",
        password="foundation-test-password",
    )

    assert user_model._meta.label == "accounts.User"
    assert user.check_password("foundation-test-password")


def test_timezone_policy_is_explicit():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "Asia/Shanghai"
