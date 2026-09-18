from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Cart, CartItem, Order
from apps.ordering.services import create_order_from_cart


User = get_user_model()


class OrderConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            username="customer_01",
            password="S3cure-customer-pass!",
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
        category = Category.objects.create(
            name="鼠标",
            slug="mice",
            is_active=True,
        )
        product = Product.objects.create(
            category=category,
            name="无线鼠标 M1",
            slug="wireless-mouse-m1",
            is_active=True,
        )
        self.sku = SKU.objects.create(
            product=product,
            code="M1-BLACK",
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(
            sku=self.sku,
            available_stock=10,
        )
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, sku=self.sku, quantity=2)

    def test_concurrent_same_key_creates_one_order_and_one_reservation(self):
        barrier = Barrier(2)
        results = Queue()

        def checkout():
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                barrier.wait(timeout=10)
                order, created = create_order_from_cart(
                    user=user,
                    address_id=self.address.pk,
                    idempotency_key="same-concurrent-key",
                )
                results.put((str(order.public_id), created, None))
            except Exception as exc:
                results.put((None, None, exc))
            finally:
                close_old_connections()

        threads = [Thread(target=checkout), Thread(target=checkout)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        outcomes = [results.get_nowait(), results.get_nowait()]
        errors = [error for _, _, error in outcomes if error is not None]
        self.assertEqual(errors, [])
        self.assertEqual(len({public_id for public_id, _, _ in outcomes}), 1)
        self.assertEqual(sorted(created for _, created, _ in outcomes), [False, True])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (8, 2))
