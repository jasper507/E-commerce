from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory
from apps.ordering.models import Cart, CartItem


User = get_user_model()


class CartAPITests(APITestCase):
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
        self.category = Category.objects.create(
            name="鼠标",
            slug="mice",
            is_active=True,
        )
        self.product = Product.objects.create(
            category=self.category,
            name="无线鼠标 M1",
            slug="wireless-mouse-m1",
            is_active=True,
        )
        self.sku = SKU.objects.create(
            product=self.product,
            code="M1-BLACK",
            color="黑色",
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(
            sku=self.sku,
            available_stock=20,
        )

    def authenticate(self, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("accounts:csrf"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def post_item(self, payload, user=None):
        csrf_token = self.authenticate(user)
        return self.client.post(
            reverse("ordering:cart-item-add"),
            payload,
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

    def test_cart_requires_an_authenticated_session(self):
        response = self.client.get(reverse("ordering:cart-detail"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_cart_read_does_not_create_a_database_row(self):
        self.authenticate()

        response = self.client.get(reverse("ordering:cart-detail"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "items": [],
                "item_count": 0,
                "total_quantity": 0,
                "subtotal": "0.00",
            },
        )
        self.assertFalse(Cart.objects.filter(user=self.user).exists())

    def test_unsafe_cart_write_requires_csrf(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("ordering:cart-item-add"),
            {"sku_code": self.sku.code, "quantity": 1},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Cart.objects.filter(user=self.user).exists())

    def test_add_uses_server_price_and_returns_cart_summary(self):
        response = self.post_item(
            {
                "sku_code": self.sku.code,
                "quantity": 2,
                "unit_price": "0.01",
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payload = response.json()
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(payload["total_quantity"], 2)
        self.assertEqual(payload["subtotal"], "258.00")
        self.assertEqual(payload["items"][0]["unit_price"], "129.00")
        self.assertEqual(payload["items"][0]["line_total"], "258.00")

    def test_duplicate_sku_merges_quantity_instead_of_creating_a_row(self):
        first_response = self.post_item(
            {"sku_code": self.sku.code, "quantity": 2}
        )
        second_response = self.post_item(
            {"sku_code": self.sku.code, "quantity": 3}
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(CartItem.objects.count(), 1)
        self.assertEqual(CartItem.objects.get().quantity, 5)

    def test_failed_merge_keeps_the_previous_quantity(self):
        self.inventory.available_stock = 200
        self.inventory.save(update_fields=("available_stock",))
        self.post_item({"sku_code": self.sku.code, "quantity": 98})

        response = self.post_item(
            {"sku_code": self.sku.code, "quantity": 2}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "quantity_limit_exceeded")
        self.assertEqual(CartItem.objects.get().quantity, 98)

    def test_inactive_or_out_of_stock_sku_cannot_be_added(self):
        cases = (
            ("sku_inactive", lambda: setattr(self.sku, "is_active", False)),
            (
                "out_of_stock",
                lambda: setattr(self.inventory, "available_stock", 0),
            ),
        )

        for expected_code, mutate in cases:
            self.sku.is_active = True
            self.sku.save(update_fields=("is_active",))
            self.inventory.available_stock = 20
            self.inventory.save(update_fields=("available_stock",))
            mutate()
            self.sku.save(update_fields=("is_active",))
            self.inventory.save(update_fields=("available_stock",))

            with self.subTest(expected_code=expected_code):
                response = self.post_item(
                    {"sku_code": self.sku.code, "quantity": 1}
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(response.json()["code"], expected_code)

    def test_quantity_cannot_exceed_current_stock(self):
        response = self.post_item(
            {"sku_code": self.sku.code, "quantity": 21}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "insufficient_stock")

    def test_patch_updates_quantity_and_delete_removes_item(self):
        self.post_item({"sku_code": self.sku.code, "quantity": 1})
        csrf_token = self.authenticate()
        item_url = reverse(
            "ordering:cart-item-detail",
            kwargs={"sku_code": self.sku.code},
        )

        patch_response = self.client.patch(
            item_url,
            {"quantity": 4},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.json()["items"][0]["quantity"], 4)

        delete_response = self.client.delete(
            item_url,
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(
            delete_response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertFalse(CartItem.objects.exists())

    def test_user_cannot_change_or_delete_another_users_item(self):
        self.post_item(
            {"sku_code": self.sku.code, "quantity": 2},
            user=self.other_user,
        )
        csrf_token = self.authenticate(self.user)
        item_url = reverse(
            "ordering:cart-item-detail",
            kwargs={"sku_code": self.sku.code},
        )

        patch_response = self.client.patch(
            item_url,
            {"quantity": 3},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        delete_response = self.client.delete(
            item_url,
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(
            patch_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            patch_response.json()["code"],
            "cart_item_not_found",
        )
        self.assertEqual(
            delete_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            CartItem.objects.get(cart__user=self.other_user).quantity,
            2,
        )

    def test_existing_item_becomes_unavailable_but_can_still_be_removed(self):
        self.post_item({"sku_code": self.sku.code, "quantity": 2})
        self.inventory.available_stock = 1
        self.inventory.save(update_fields=("available_stock",))
        csrf_token = self.authenticate()

        get_response = self.client.get(reverse("ordering:cart-detail"))

        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        item = get_response.json()["items"][0]
        self.assertFalse(item["is_available"])
        self.assertEqual(item["unavailable_reason"], "insufficient_stock")

        delete_response = self.client.delete(
            reverse(
                "ordering:cart-item-detail",
                kwargs={"sku_code": self.sku.code},
            ),
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(
            delete_response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
