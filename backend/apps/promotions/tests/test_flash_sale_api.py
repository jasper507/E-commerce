"""秒杀的公开查询、Session 写入与既有下单事实边界。"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APIClient

from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Order
from apps.promotions.models import FlashSalePurchase
from apps.promotions.tests.test_flash_sale_checkout import FlashSaleCheckoutFixture


MAX_DATABASE_ID = 2**63 - 1


class FlashSaleAPITests(FlashSaleCheckoutFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.items_url = reverse("promotions:flash-sale-items")
        self.orders_url = reverse("promotions:flash-sale-orders")

    def _authenticate(self, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("accounts:csrf"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def _post_order(
        self,
        *,
        key="flash-sale-api-order",
        user=None,
        flash_sale_item_id=None,
        address_id=None,
        extra_data=None,
    ):
        csrf_token = self._authenticate(user)
        payload = {
            "flash_sale_item_id": (
                self.flash_sale_item.pk
                if flash_sale_item_id is None
                else flash_sale_item_id
            ),
            "address_id": self.address.pk if address_id is None else address_id,
        }
        if extra_data:
            payload.update(extra_data)
        headers = {"HTTP_X_CSRFTOKEN": csrf_token}
        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        return self.client.post(self.orders_url, payload, format="json", **headers)

    def test_guest_receives_only_public_activity_and_item_fields(self):
        response = self.client.get(self.items_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        item = payload[0]
        sale = self.flash_sale_item.flash_sale
        self.assertEqual(parse_datetime(item.pop("starts_at")), sale.starts_at)
        self.assertEqual(parse_datetime(item.pop("ends_at")), sale.ends_at)
        self.assertEqual(
            item,
            {
                "id": self.flash_sale_item.pk,
                "sale_name": sale.name,
                "product_slug": self.sku.product.slug,
                "product_name": self.sku.product.name,
                "sku_code": self.sku.code,
                "color": self.sku.color,
                "attributes": self.sku.attributes,
                "price": "89.00",
                "currency": "CNY",
                "quantity": 1,
                "in_stock": True,
            },
        )
        self._assert_no_checkout_effects(available_stock=5)

    def test_public_query_requires_all_active_flags_and_current_time_window(self):
        sale = self.flash_sale_item.flash_sale
        for label, model in (
            ("sale", sale),
            ("item", self.flash_sale_item),
            ("sku", self.sku),
            ("product", self.sku.product),
            ("category", self.sku.product.category),
        ):
            with self.subTest(inactive=label):
                model.is_active = False
                model.save(update_fields=("is_active",))
                try:
                    response = self.client.get(self.items_url)
                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.assertEqual(response.json(), [])
                finally:
                    model.is_active = True
                    model.save(update_fields=("is_active",))

        now = timezone.now()
        for label, starts_at, ends_at, visible in (
            ("before_start", now + timedelta(hours=1), now + timedelta(hours=2), False),
            ("at_start", now, now + timedelta(hours=1), True),
            ("at_end", now - timedelta(hours=1), now, False),
        ):
            with self.subTest(window=label):
                sale.starts_at, sale.ends_at = starts_at, ends_at
                sale.save(update_fields=("starts_at", "ends_at"))
                with patch("django.utils.timezone.now", return_value=now) as clock:
                    response = self.client.get(self.items_url)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    [item["id"] for item in response.json()],
                    [self.flash_sale_item.pk] if visible else [],
                )
                self.assertEqual(clock.call_count, 1)
        self._assert_no_checkout_effects(available_stock=5)

    def test_zero_or_missing_inventory_is_visible_but_checkout_rolls_back(self):
        self.inventory.available_stock = 0
        self.inventory.save(update_fields=("available_stock",))
        for label in ("zero_stock", "missing_inventory"):
            with self.subTest(inventory=label):
                if label == "missing_inventory":
                    self.inventory.delete()
                listing = self.client.get(self.items_url)
                self.assertEqual(listing.status_code, status.HTTP_200_OK)
                self.assertEqual(len(listing.json()), 1)
                self.assertIs(listing.json()[0]["in_stock"], False)

                response = self._post_order(key=f"flash-sale-{label}")
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(response.json()["code"], "insufficient_stock")
                self._assert_fact_counts(0)
                self._assert_cart_unchanged()
                if label == "zero_stock":
                    self._assert_no_checkout_effects(available_stock=0)
                else:
                    self.assertFalse(Inventory.objects.exists())

    def test_public_items_are_read_only_and_order_creation_has_no_get(self):
        guest_write = self.client.post(self.items_url, {}, format="json")
        self.assertEqual(guest_write.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self._authenticate()
        session_write = self.client.post(self.items_url, {}, format="json")
        order_get = self.client.get(self.orders_url)
        self.assertEqual(session_write.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(order_get.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self._assert_no_checkout_effects(available_stock=5)

    def test_checkout_requires_session_and_a_valid_csrf_token(self):
        payload = {
            "flash_sale_item_id": self.flash_sale_item.pk,
            "address_id": self.address.pk,
        }
        anonymous = self.client.post(
            self.orders_url, payload, format="json", HTTP_IDEMPOTENCY_KEY="guest-order"
        )
        self.assertEqual(anonymous.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_login(self.user)
        for token in (None, "invalid-csrf-token"):
            with self.subTest(csrf=token):
                headers = {"HTTP_IDEMPOTENCY_KEY": "csrf-order"}
                if token is not None:
                    headers["HTTP_X_CSRFTOKEN"] = token
                response = self.client.post(
                    self.orders_url, payload, format="json", **headers
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self._assert_no_checkout_effects(available_stock=5)

    def test_item_and_address_inputs_are_required_and_bounded_database_ids(self):
        csrf_token = self._authenticate()
        valid = {
            "flash_sale_item_id": self.flash_sale_item.pk,
            "address_id": self.address.pk,
        }
        for field in valid:
            invalid_inputs = [{key: value for key, value in valid.items() if key != field}]
            invalid_inputs.extend(
                {**valid, field: value}
                for value in (None, 0, -1, "invalid", True, MAX_DATABASE_ID + 1)
            )
            for payload in invalid_inputs:
                with self.subTest(field=field, payload=payload):
                    response = self.client.post(
                        self.orders_url,
                        payload,
                        format="json",
                        HTTP_X_CSRFTOKEN=csrf_token,
                        HTTP_IDEMPOTENCY_KEY="invalid-input",
                    )
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertIn(field, response.json())

        for field, code in (
            ("flash_sale_item_id", "flash_sale_item_not_found"),
            ("address_id", "address_not_found"),
        ):
            with self.subTest(valid_upper_bound=field):
                response = self._post_order(extra_data={field: MAX_DATABASE_ID})
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(response.json()["code"], code)
        self._assert_no_checkout_effects(available_stock=5)

    def test_checkout_requires_a_nonempty_printable_idempotency_key(self):
        for key, expected_code in (
            (None, "idempotency_key_missing"),
            ("   ", "idempotency_key_missing"),
            ("x" * 129, "idempotency_key_invalid"),
            ("invalid\tkey", "idempotency_key_invalid"),
        ):
            with self.subTest(key=key):
                response = self._post_order(key=key)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.json()["code"], expected_code)
        self._assert_no_checkout_effects(available_stock=5)

    def test_create_uses_server_price_fixed_quantity_and_session_owner(self):
        response = self._post_order(
            key="  flash-sale-authoritative  ",
            extra_data={
                "price": "0.01",
                "total": "0.01",
                "quantity": 99,
                "user_id": self.other_user.pk,
                "creation_scope": Order.CreationScope.CART,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                "public_id", "status", "currency", "subtotal", "discount_total",
                "shipping_fee", "total", "address", "items", "created_at",
            },
        )
        self.assertEqual(payload["status"], Order.Status.PENDING_PAYMENT)
        self.assertEqual(payload["currency"], "CNY")
        self.assertEqual(payload["total"], "89.00")
        self.assertEqual(payload["items"][0]["unit_price"], "89.00")
        self.assertEqual(payload["items"][0]["quantity"], 1)
        self.assertEqual(payload["address"]["detail"], self.address.detail)
        order = self._assert_complete_flash_sale_order(key="flash-sale-authoritative")
        self.assertEqual(payload["public_id"], str(order.public_id))

    def test_replay_precedes_activity_checks_and_conflicts_do_not_duplicate_facts(self):
        key = "flash-sale-api-replay"
        first = self._post_order(key=key)
        replay = self._post_order(key=key)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["public_id"], replay.json()["public_id"])

        for label, request_fields, code in (
            ("changed_address", {"key": key, "address_id": self.second_address.pk}, "idempotency_conflict"),
            ("changed_item", {"key": key, "flash_sale_item_id": MAX_DATABASE_ID}, "idempotency_conflict"),
            ("new_key", {"key": "flash-sale-second-intent"}, "flash_sale_limit_reached"),
        ):
            with self.subTest(conflict=label):
                response = self._post_order(**request_fields)
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(response.json()["code"], code)

        sale = self.flash_sale_item.flash_sale
        sale.ends_at = timezone.now() - timedelta(minutes=1)
        sale.save(update_fields=("ends_at",))
        self.flash_sale_item.price = Decimal("999.00")
        self.flash_sale_item.is_active = False
        self.flash_sale_item.save(update_fields=("price", "is_active"))
        after_change = self._post_order(key=key)
        self.assertEqual(after_change.status_code, status.HTTP_200_OK)
        self.assertEqual(after_change.json()["public_id"], first.json()["public_id"])
        self.assertEqual(after_change.json()["total"], "89.00")
        self._assert_complete_flash_sale_order(key=key)

    def test_unavailable_items_and_wrong_owner_addresses_have_no_checkout_effects(self):
        sale = self.flash_sale_item.flash_sale
        for label, model, expected_code in (
            ("sale", sale, "flash_sale_unavailable"),
            ("item", self.flash_sale_item, "flash_sale_unavailable"),
            ("category", self.sku.product.category, "category_inactive"),
            ("product", self.sku.product, "product_inactive"),
            ("sku", self.sku, "sku_inactive"),
        ):
            with self.subTest(inactive=label):
                model.is_active = False
                model.save(update_fields=("is_active",))
                try:
                    response = self._post_order(key=f"flash-sale-inactive-{label}")
                    self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                    self.assertEqual(response.json()["code"], expected_code)
                    self._assert_no_checkout_effects(available_stock=5)
                finally:
                    model.is_active = True
                    model.save(update_fields=("is_active",))

        now = timezone.now()
        for label, starts_at, ends_at in (
            ("future", now + timedelta(hours=1), now + timedelta(hours=2)),
            ("ended", now - timedelta(hours=2), now - timedelta(hours=1)),
        ):
            with self.subTest(window=label):
                sale.starts_at, sale.ends_at = starts_at, ends_at
                sale.save(update_fields=("starts_at", "ends_at"))
                response = self._post_order(key=f"flash-sale-{label}")
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(response.json()["code"], "flash_sale_unavailable")
                self._assert_no_checkout_effects(available_stock=5)

        response = self._post_order(key="flash-sale-other-address", address_id=self.other_address.pk)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["code"], "address_not_found")
        self._assert_no_checkout_effects(available_stock=5)

    def test_existing_order_queries_return_only_the_session_owners_flash_sale_order(self):
        created = self._post_order(key="flash-sale-order-owner")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        public_id = created.json()["public_id"]
        detail_url = reverse("orders:order-detail", kwargs={"public_id": public_id})
        own_list = self.client.get(reverse("orders:order-list"))
        own_detail = self.client.get(detail_url)
        self.assertEqual(own_list.status_code, status.HTTP_200_OK)
        self.assertEqual([order["public_id"] for order in own_list.json()], [public_id])
        self.assertEqual(own_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(own_detail.json(), created.json())

        self._authenticate(self.other_user)
        other_list = self.client.get(reverse("orders:order-list"))
        other_detail = self.client.get(detail_url)
        self.assertEqual(other_list.status_code, status.HTTP_200_OK)
        self.assertEqual(other_list.json(), [])
        self.assertEqual(other_detail.status_code, status.HTTP_404_NOT_FOUND)
        self._assert_complete_flash_sale_order(key="flash-sale-order-owner")

    def test_cart_scope_and_other_customer_can_use_the_same_idempotency_key(self):
        key = "shared-http-checkout-key"
        csrf_token = self._authenticate()
        cart_response = self.client.post(
            reverse("orders:order-list"),
            {"address_id": self.address.pk},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY=key,
        )
        flash_response = self._post_order(key=key)
        replay = self._post_order(key=key)
        other_response = self._post_order(
            key=key, user=self.other_user, address_id=self.other_address.pk
        )

        self.assertEqual(cart_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(flash_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(other_response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(cart_response.json()["public_id"], flash_response.json()["public_id"])
        self.assertEqual(replay.json()["public_id"], flash_response.json()["public_id"])
        self.assertNotEqual(other_response.json()["public_id"], flash_response.json()["public_id"])
        self.assertEqual(Order.objects.count(), 3)
        self.assertEqual(FlashSalePurchase.objects.count(), 2)
        self.assertEqual(InventoryReservation.objects.count(), 3)
        self.assertEqual(InventoryMovement.objects.count(), 3)
        self.assertEqual(
            Order.objects.filter(user=self.user, creation_scope=Order.CreationScope.CART).count(),
            1,
        )
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (1, 4))
        other_order = Order.objects.get(public_id=other_response.json()["public_id"])
        self.assertEqual(other_order.user_id, self.other_user.pk)
