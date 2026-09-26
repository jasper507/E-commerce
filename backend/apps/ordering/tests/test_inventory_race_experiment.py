"""隔离 MySQL 对照实验：错误的先读后写与正式下单流程。"""

from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Cart, CartItem, Order, OrderItem
from apps.ordering.services import OrderRuleError, create_order_from_cart


User = get_user_model()


class InventoryRaceExperimentTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "mysql":
            self.skipTest("库存并发对照实验必须在 MySQL 上运行")

        category = Category.objects.create(
            name="鼠标", slug="race-mice", is_active=True
        )
        product = Product.objects.create(
            category=category,
            name="无线鼠标",
            slug="race-mouse",
            is_active=True,
        )
        self.sku = SKU.objects.create(
            product=product,
            code="RACE-MOUSE",
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(
            sku=self.sku,
            available_stock=1,
        )
        self.buyers = []
        for number in (1, 2):
            user = User.objects.create_user(
                username=f"race_buyer_{number}",
                password="S3cure-customer-pass!",
            )
            address = Address.objects.create(
                user=user,
                recipient_name=f"顾客{number}",
                phone=f"1380013800{number}",
                province="广东省",
                city="深圳市",
                district="南山区",
                detail="科技园 1 号",
                default_marker=1,
            )
            cart = Cart.objects.create(user=user)
            CartItem.objects.create(cart=cart, sku=self.sku, quantity=1)
            self.buyers.append((user.pk, address.pk))

    def _run_two_buyers(self, checkout):
        start = Barrier(2)
        results = Queue()

        def worker(user_id, address_id):
            close_old_connections()
            try:
                results.put(checkout(user_id, address_id, start))
            except Exception as exc:
                results.put(("unexpected_error", exc))
            finally:
                close_old_connections()

        threads = [
            Thread(target=worker, args=buyer)
            for buyer in self.buyers
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        outcomes = [results.get_nowait() for _ in threads]
        self.assertFalse(
            any(outcome[0] == "unexpected_error" for outcome in outcomes),
            outcomes,
        )
        return outcomes

    def test_naive_read_then_write_accepts_two_orders_for_one_item(self):
        def naive_checkout(user_id, _address_id, start):
            # 错误示范仅存在于测试中：两个请求都先读到 1，再用旧值写回。
            stock = Inventory.objects.get(pk=self.inventory.pk)
            observed_available = stock.available_stock
            start.wait(timeout=10)
            if observed_available < 1:
                return ("sold_out", user_id)

            with transaction.atomic():
                order = Order.objects.create(
                    user_id=user_id,
                    subtotal=self.sku.price,
                    total=self.sku.price,
                    idempotency_key=f"naive-{user_id}",
                    request_fingerprint="naive-experiment",
                )
                item = OrderItem.objects.create(
                    order=order,
                    sku=self.sku,
                    product_name_snapshot=self.sku.product.name,
                    sku_code_snapshot=self.sku.code,
                    unit_price=self.sku.price,
                    quantity=1,
                    line_total=self.sku.price,
                )
                stock.available_stock = observed_available - 1
                stock.reserved_stock += 1
                stock.save(update_fields=("available_stock", "reserved_stock"))
                InventoryReservation.objects.create(
                    order_item=item,
                    inventory=stock,
                    quantity=1,
                    expires_at=timezone.now() + timedelta(minutes=15),
                )
            return ("accepted", user_id, observed_available)

        outcomes = self._run_two_buyers(naive_checkout)

        self.assertEqual([outcome[0] for outcome in outcomes], ["accepted", "accepted"])
        self.assertEqual([outcome[2] for outcome in outcomes], [1, 1])
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(OrderItem.objects.count(), 2)
        self.assertEqual(
            sum(InventoryReservation.objects.values_list("quantity", flat=True)),
            2,
        )
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (0, 1))

    def test_real_checkout_accepts_at_most_one_order_and_keeps_losers_cart(self):
        def protected_checkout(user_id, address_id, start):
            user = User.objects.get(pk=user_id)
            start.wait(timeout=10)
            try:
                order, created = create_order_from_cart(
                    user=user,
                    address_id=address_id,
                    idempotency_key=f"protected-{user_id}",
                )
            except OrderRuleError as exc:
                return ("rejected", user_id, exc.code)
            return ("accepted", user_id, order.pk, created)

        outcomes = self._run_two_buyers(protected_checkout)
        accepted = [outcome for outcome in outcomes if outcome[0] == "accepted"]
        rejected = [outcome for outcome in outcomes if outcome[0] == "rejected"]

        self.assertEqual(len(accepted), 1, outcomes)
        self.assertEqual(len(rejected), 1, outcomes)
        self.assertTrue(accepted[0][3])
        self.assertIn(rejected[0][2], {"stock_busy", "insufficient_stock"})
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.get().quantity, 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (0, 1))
        self.assertFalse(CartItem.objects.filter(cart__user_id=accepted[0][1]).exists())
        self.assertTrue(CartItem.objects.filter(cart__user_id=rejected[0][1]).exists())

        with self.assertRaises(OrderRuleError) as retry:
            create_order_from_cart(
                user=User.objects.get(pk=rejected[0][1]),
                address_id=dict(self.buyers)[rejected[0][1]],
                idempotency_key=f"protected-{rejected[0][1]}",
            )
        self.assertEqual(retry.exception.code, "insufficient_stock")
