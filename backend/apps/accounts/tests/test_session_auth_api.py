from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase


User = get_user_model()


class SessionAuthenticationAPITests(APITestCase):
    password = "S3cure-session-pass!"

    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="alice_01",
            password=self.password,
        )

    def get_csrf_token(self, client=None):
        client = client or self.client
        response = client.get(reverse("accounts:csrf"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        return client.cookies[settings.CSRF_COOKIE_NAME].value

    def login(self, username="alice_01", password=None):
        csrf_token = self.get_csrf_token()
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": username,
                "password": password or self.password,
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        return response, csrf_token

    def assert_csrf_rejected(self, response):
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            response.json(),
            {"detail": "CSRF verification failed."},
        )

    def test_csrf_endpoint_sets_javascript_readable_samesite_cookie(self):
        response = self.client.get(reverse("accounts:csrf"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        csrf_cookie = response.cookies[settings.CSRF_COOKIE_NAME]
        self.assertEqual(csrf_cookie["samesite"], "Lax")
        self.assertFalse(csrf_cookie["httponly"])

    def test_registration_requires_csrf_and_does_not_log_user_in(self):
        registration_data = {
            "username": "bob_02",
            "password": "S3cure-registration-pass!",
        }

        rejected_response = self.client.post(
            reverse("accounts:register"),
            registration_data,
            format="json",
        )

        self.assert_csrf_rejected(rejected_response)
        self.assertFalse(User.objects.filter(username="bob_02").exists())

        csrf_token = self.get_csrf_token()
        accepted_response = self.client.post(
            reverse("accounts:register"),
            registration_data,
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(
            accepted_response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_requires_csrf_and_does_not_create_session_when_rejected(self):
        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": self.user.username,
                "password": self.password,
            },
            format="json",
        )

        self.assert_csrf_rejected(response)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_normalizes_username_and_rotates_session_and_csrf_tokens(self):
        anonymous_session = self.client.session
        anonymous_session["anonymous_state"] = "preserved"
        anonymous_session.save()
        anonymous_session_key = anonymous_session.session_key

        response, pre_login_csrf_token = self.login(username="  ALICE_01  ")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "id": self.user.id,
                "username": "alice_01",
                "is_staff": False,
            },
        )
        self.assertEqual(
            self.client.session["_auth_user_id"],
            str(self.user.id),
        )
        self.assertNotEqual(
            self.client.session.session_key,
            anonymous_session_key,
        )

        session_cookie = response.cookies[settings.SESSION_COOKIE_NAME]
        self.assertTrue(session_cookie["httponly"])
        self.assertEqual(session_cookie["samesite"], "Lax")
        self.assertNotEqual(
            self.client.cookies[settings.CSRF_COOKIE_NAME].value,
            pre_login_csrf_token,
        )

    def test_invalid_credentials_use_one_generic_error_without_a_session(self):
        csrf_token = self.get_csrf_token()

        wrong_password_response = self.client.post(
            reverse("accounts:login"),
            {
                "username": self.user.username,
                "password": "incorrect-password",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        unknown_user_response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "unknown_user",
                "password": "incorrect-password",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        expected_error = {"detail": "Invalid username or password."}
        self.assertEqual(
            wrong_password_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            unknown_user_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(wrong_password_response.json(), expected_error)
        self.assertEqual(unknown_user_response.json(), expected_error)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_receives_the_same_invalid_credentials_error(self):
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        csrf_token = self.get_csrf_token()

        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": self.user.username,
                "password": self.password,
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json(),
            {"detail": "Invalid username or password."},
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_current_user_requires_authentication_but_not_a_csrf_header(self):
        anonymous_response = self.client.get(reverse("accounts:me"))

        self.assertEqual(
            anonymous_response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

        login_response, _ = self.login()
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        authenticated_response = self.client.get(reverse("accounts:me"))

        self.assertEqual(
            authenticated_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            authenticated_response.json(),
            {
                "id": self.user.id,
                "username": self.user.username,
                "is_staff": False,
            },
        )

    def test_staff_user_uses_the_same_session_login_contract(self):
        staff_user = User.objects.create_user(
            username="operator_01",
            password=self.password,
            is_staff=True,
        )

        response, _ = self.login(username=staff_user.username)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "id": staff_user.id,
                "username": staff_user.username,
                "is_staff": True,
            },
        )

    def test_logout_requires_current_csrf_token_and_preserves_rejected_session(self):
        login_response, pre_login_csrf_token = self.login()
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        missing_token_response = self.client.post(
            reverse("accounts:logout"),
            format="json",
        )
        self.assert_csrf_rejected(missing_token_response)

        stale_token_response = self.client.post(
            reverse("accounts:logout"),
            format="json",
            HTTP_X_CSRFTOKEN=pre_login_csrf_token,
        )
        self.assert_csrf_rejected(stale_token_response)

        current_user_response = self.client.get(reverse("accounts:me"))
        self.assertEqual(current_user_response.status_code, status.HTTP_200_OK)

    def test_logout_clears_session_and_is_idempotent(self):
        login_response, _ = self.login()
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        csrf_token = self.client.cookies[settings.CSRF_COOKIE_NAME].value

        logout_response = self.client.post(
            reverse("accounts:logout"),
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(logout_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.get(reverse("accounts:me")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        repeated_logout_response = self.client.post(
            reverse("accounts:logout"),
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(
            repeated_logout_response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_get_cannot_log_out_an_authenticated_user(self):
        login_response, _ = self.login()
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

        response = self.client.get(reverse("accounts:logout"))

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.get(reverse("accounts:me")).status_code,
            status.HTTP_200_OK,
        )

    def test_non_api_csrf_failures_keep_djangos_html_response(self):
        response = self.client.post(
            reverse("admin:login"),
            {
                "username": self.user.username,
                "password": self.password,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(response.headers["Content-Type"].startswith("text/html"))
