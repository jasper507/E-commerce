from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Address


User = get_user_model()


class AddressAPITests(APITestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="customer_01",
            password="S3cure-customer-pass!",
        )
        self.other_user = User.objects.create_user(
            username="customer_02",
            password="S3cure-other-pass!",
        )

    def address_payload(self, suffix="一", **overrides):
        payload = {
            "recipient_name": f"测试用户{suffix}",
            "phone": "13800138000",
            "province": "广东省",
            "city": "深圳市",
            "district": "南山区",
            "detail": f"科技园{suffix}号",
        }
        payload.update(overrides)
        return payload

    def authenticate(self, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("accounts:csrf"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def create_address(self, suffix="一", user=None, **overrides):
        csrf_token = self.authenticate(user)
        return self.client.post(
            reverse("addresses:address-list"),
            self.address_payload(suffix, **overrides),
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

    def test_address_list_requires_an_authenticated_session(self):
        response = self.client.get(reverse("addresses:address-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unsafe_address_write_requires_csrf(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("addresses:address-list"),
            self.address_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Address.objects.exists())

    def test_first_address_is_default_and_phone_is_normalized(self):
        response = self.create_address(
            phone="+86 138-0013-8000",
            is_default=False,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["phone"], "13800138000")
        self.assertTrue(response.json()["is_default"])
        address = Address.objects.get()
        self.assertEqual(address.phone, "13800138000")
        self.assertTrue(address.is_default)

    def test_new_default_demotes_the_previous_default(self):
        first_response = self.create_address()
        second_response = self.create_address("二", is_default=True)

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        first = Address.objects.get(pk=first_response.json()["id"])
        second = Address.objects.get(pk=second_response.json()["id"])
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)
        self.assertEqual(
            Address.objects.filter(user=self.user, default_marker=1).count(),
            1,
        )

    def test_list_returns_only_current_users_addresses_default_first(self):
        own_first = self.create_address().json()
        own_second = self.create_address("二", is_default=True).json()
        self.create_address("他", user=self.other_user)
        self.authenticate(self.user)

        response = self.client.get(reverse("addresses:address-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [address["id"] for address in response.json()],
            [own_second["id"], own_first["id"]],
        )

    def test_set_default_endpoint_switches_default_and_is_idempotent(self):
        first_id = self.create_address().json()["id"]
        second_id = self.create_address("二").json()["id"]
        csrf_token = self.authenticate()
        url = reverse(
            "addresses:address-set-default",
            kwargs={"address_id": second_id},
        )

        first_response = self.client.post(url, HTTP_X_CSRFTOKEN=csrf_token)
        second_response = self.client.post(url, HTTP_X_CSRFTOKEN=csrf_token)

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertFalse(Address.objects.get(pk=first_id).is_default)
        self.assertTrue(Address.objects.get(pk=second_id).is_default)

    def test_patch_updates_fields_but_cannot_unset_the_only_default(self):
        address_id = self.create_address().json()["id"]
        csrf_token = self.authenticate()
        url = reverse(
            "addresses:address-detail",
            kwargs={"address_id": address_id},
        )

        update_response = self.client.patch(
            url,
            {"detail": "新地址 88 号"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        rejected_response = self.client.patch(
            url,
            {"recipient_name": "不应保存", "is_default": False},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            rejected_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            rejected_response.json()["code"],
            "default_address_required",
        )
        address = Address.objects.get(pk=address_id)
        self.assertEqual(address.detail, "新地址 88 号")
        self.assertEqual(address.recipient_name, "测试用户一")
        self.assertTrue(address.is_default)

    def test_deleting_default_promotes_oldest_remaining_address(self):
        default_id = self.create_address().json()["id"]
        oldest_remaining_id = self.create_address("二").json()["id"]
        newest_remaining_id = self.create_address("三").json()["id"]
        csrf_token = self.authenticate()

        response = self.client.delete(
            reverse(
                "addresses:address-detail",
                kwargs={"address_id": default_id},
            ),
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(Address.objects.get(pk=oldest_remaining_id).is_default)
        self.assertFalse(Address.objects.get(pk=newest_remaining_id).is_default)

    def test_deleting_the_only_address_leaves_no_default(self):
        address_id = self.create_address().json()["id"]
        csrf_token = self.authenticate()

        response = self.client.delete(
            reverse(
                "addresses:address-detail",
                kwargs={"address_id": address_id},
            ),
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Address.objects.filter(user=self.user).exists())

    def test_user_cannot_access_another_users_address(self):
        other_id = self.create_address(user=self.other_user).json()["id"]
        csrf_token = self.authenticate(self.user)
        detail_url = reverse(
            "addresses:address-detail",
            kwargs={"address_id": other_id},
        )
        default_url = reverse(
            "addresses:address-set-default",
            kwargs={"address_id": other_id},
        )

        responses = (
            self.client.patch(
                detail_url,
                {"detail": "越权修改"},
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            ),
            self.client.delete(
                detail_url,
                HTTP_X_CSRFTOKEN=csrf_token,
            ),
            self.client.post(
                default_url,
                HTTP_X_CSRFTOKEN=csrf_token,
            ),
        )

        for response in responses:
            with self.subTest(status_code=response.status_code):
                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )
        self.assertEqual(
            Address.objects.get(pk=other_id).detail,
            "科技园一号",
        )

    def test_invalid_fields_are_rejected_without_creating_an_address(self):
        cases = (
            ({"phone": "12800138000"}, "phone"),
            ({"recipient_name": "   "}, "recipient_name"),
            ({"detail": ""}, "detail"),
        )

        for overrides, expected_field in cases:
            with self.subTest(expected_field=expected_field):
                response = self.create_address(**overrides)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertIn(expected_field, response.json())
                self.assertFalse(Address.objects.exists())
