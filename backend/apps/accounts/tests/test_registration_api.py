from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


User = get_user_model()


class RegistrationAPITests(APITestCase):
    def test_registration_creates_normalized_user_without_logging_in(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "  Alice_01  ",
                "password": "S3cure-registration-pass!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get()
        self.assertEqual(
            response.json(),
            {
                "id": user.id,
                "username": "alice_01",
            },
        )
        self.assertEqual(user.username, "alice_01")
        self.assertTrue(user.check_password("S3cure-registration-pass!"))
        self.assertNotIn("password", response.json())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_registration_rejects_invalid_username(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "1alice",
                "password": "S3cure-registration-pass!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.json())
        self.assertFalse(User.objects.exists())

    def test_registration_rejects_duplicate_normalized_username(self):
        User.objects.create_user(
            username="alice",
            password="Existing-user-pass-123!",
        )

        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": " Alice ",
                "password": "S3cure-registration-pass!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.json())
        self.assertEqual(User.objects.count(), 1)

    def test_registration_rejects_weak_password(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "alice",
                "password": "short",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.json())
        self.assertFalse(User.objects.exists())

    def test_registration_cannot_create_admin_user(self):
        response = self.client.post(
            reverse("accounts:register"),
            {
                "username": "alice",
                "password": "S3cure-registration-pass!",
                "is_staff": True,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
