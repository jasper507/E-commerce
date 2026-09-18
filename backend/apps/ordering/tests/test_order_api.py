from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import (
    Inventory,
    InventoryMovement,
    InventoryReservation,
)
from apps.ordering.models import AddressSnapshot, Cart, CartItem, Order, OrderItem


User = get_user_model()


class OrderAPITests(APITestCase):
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
        self.address = Address.objects.create(
            user=self.user,
            recipient_name="测试用户",
            phone="13800138000",
            province="广东省",
            city="深圳市",
            district="南山区",
            detail="科技园 1 号",
            default_marker=1,
        )
        self.other_address = Address.objects.create(
            user=self.other_user,
            recipient_name="其他用户",
            phone="13900139000",
            province="上海市",
            city="上海市",
            district="浦东新区",
            detail="测试路 2 号",
            default_marker=1,
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
            attributes={"connection": "2.4G"},
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(
            sku=self.sku,
            available_stock=20,
        )
        self.cart = Cart.objects.create(user=self.user)
        self.cart_item = CartItem.objects.create(
            cart=self.cart,
            sku=self.sku,
            quantity=2,
        )

    def authenticate(self, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get(reverse("accounts:csrf"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def post_order(self, *, key="checkout-001", address_id=None, user=None):
        csrf_token = self.authenticate(user)
        return self.client.post(
            reverse("orders:order-list"),
            {"address_id": address_id or self.address.pk},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_order_collection_requires_session_and_writes_require_csrf(self):
        get_response = self.client.get(reverse("orders:order-list"))
        self.client.force_login(self.user)
        post_response = self.client.post(
            reverse("orders:order-list"),
            {"address_id": self.address.pk},
            format="json",
            HTTP_IDEMPOTENCY_KEY="checkout-001",
        )

        self.assertEqual(get_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(post_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Order.objects.exists())

    def test_create_requires_a_valid_idempotency_key(self):
        csrf_token = self.authenticate()
        missing_response = self.client.post(
            reverse("orders:order-list"),
            {"address_id": self.address.pk},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        long_response = self.client.post(
            reverse("orders:order-list"),
            {"address_id": self.address.pk},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY="x" * 129,
        )

        self.assertEqual(missing_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing_response.json()["code"], "idempotency_key_missing")
        self.assertEqual(long_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(long_response.json()["code"], "idempotency_key_invalid")
        self.assertFalse(Order.objects.exists())

    def test_create_recalculates_money_and_commits_the_complete_slice(self):
        csrf_token = self.authenticate()
        response = self.client.post(
            reverse("orders:order-list"),
            {"address_id": self.address.pk, "total": "0.01"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY="checkout-money",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        payload = response.json()
        self.assertEqual(payload["status"], Order.Status.PENDING_PAYMENT)
        self.assertEqual(payload["currency"], "CNY")
        self.assertEqual(payload["subtotal"], "258.00")
        self.assertEqual(payload["discount_total"], "0.00")
        self.assertEqual(payload["shipping_fee"], "0.00")
        self.assertEqual(payload["total"], "258.00")
        self.assertEqual(payload["items"][0]["unit_price"], "129.00")
        self.assertEqual(payload["items"][0]["quantity"], 2)
        self.assertEqual(payload["items"][0]["discount_amount"], "0.00")
        self.assertEqual(payload["items"][0]["line_total"], "258.00")

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.available_stock, 18)
        self.assertEqual(self.inventory.reserved_stock, 2)
        self.assertFalse(CartItem.objects.filter(cart=self.cart).exists())
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertEqual(AddressSnapshot.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.count(), 1)
        movement = InventoryMovement.objects.get()
        self.assertEqual(movement.movement_type, InventoryMovement.Type.RESERVE)
        self.assertEqual(movement.available_delta, -2)
        self.assertEqual(movement.reserved_delta, 2)

    def test_order_snapshots_do_not_follow_later_address_or_product_changes(self):
        response = self.post_order(key="checkout-snapshot")
        public_id = response.json()["public_id"]

        self.address.recipient_name = "后来修改的名字"
        self.address.detail = "后来修改的地址"
        self.address.save(update_fields=("recipient_name", "detail", "updated_at"))
        self.address.delete()
        self.product.name = "后来修改的商品名"
        self.product.save(update_fields=("name",))
        self.sku.price = Decimal("999.00")
        self.sku.save(update_fields=("price",))

        detail_response = self.client.get(
            reverse("orders:order-detail", kwargs={"public_id": public_id})
        )
        payload = detail_response.json()
        self.assertEqual(payload["address"]["recipient_name"], "测试用户")
        self.assertEqual(payload["address"]["detail"], "科技园 1 号")
        self.assertEqual(payload["items"][0]["product_name_snapshot"], "无线鼠标 M1")
        self.assertEqual(payload["items"][0]["unit_price"], "129.00")

    def test_same_key_and_request_replays_without_duplicate_side_effects(self):
        first = self.post_order(key="checkout-replay")
        second = self.post_order(key="checkout-replay")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["public_id"], second.json()["public_id"])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (18, 2))

    def test_same_key_with_a_different_request_returns_conflict(self):
        second_address = Address.objects.create(
            user=self.user,
            recipient_name="第二地址",
            phone="13700137000",
            province="广东省",
            city="广州市",
            district="天河区",
            detail="测试路 8 号",
        )
        first = self.post_order(key="checkout-conflict")
        second = self.post_order(
            key="checkout-conflict",
            address_id=second_address.pk,
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.json()["code"], "idempotency_conflict")
        self.assertEqual(Order.objects.count(), 1)

    def test_another_users_address_is_not_visible_to_checkout(self):
        response = self.post_order(
            key="checkout-wrong-owner",
            address_id=self.other_address.pk,
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.json()["code"], "address_not_found")
        self.assertTrue(CartItem.objects.filter(cart=self.cart).exists())
        self.assertFalse(Order.objects.exists())

    def test_empty_cart_is_rejected(self):
        self.cart_item.delete()

        response = self.post_order(key="checkout-empty")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["code"], "cart_empty")
        self.assertFalse(Order.objects.exists())

    def test_insufficient_second_sku_rolls_back_everything_and_keeps_cart(self):
        second_product = Product.objects.create(
            category=self.category,
            name="机械键盘 K1",
            slug="mechanical-keyboard-k1",
            is_active=True,
        )
        second_sku = SKU.objects.create(
            product=second_product,
            code="K1-WHITE",
            price=Decimal("299.00"),
            is_active=True,
        )
        second_inventory = Inventory.objects.create(
            sku=second_sku,
            available_stock=1,
        )
        CartItem.objects.create(cart=self.cart, sku=second_sku, quantity=2)

        response = self.post_order(key="checkout-shortage")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.json()["code"], "insufficient_stock")
        self.inventory.refresh_from_db()
        second_inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (20, 0))
        self.assertEqual((second_inventory.available_stock, second_inventory.reserved_stock), (1, 0))
        self.assertEqual(CartItem.objects.filter(cart=self.cart).count(), 2)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(InventoryReservation.objects.exists())
        self.assertFalse(InventoryMovement.objects.exists())

    def test_order_list_and_detail_are_scoped_to_the_current_user(self):
        create_response = self.post_order(key="checkout-owner")
        public_id = create_response.json()["public_id"]
        self.authenticate(self.other_user)

        list_response = self.client.get(reverse("orders:order-list"))
        detail_response = self.client.get(
            reverse("orders:order-detail", kwargs={"public_id": public_id})
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.json(), [])
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
