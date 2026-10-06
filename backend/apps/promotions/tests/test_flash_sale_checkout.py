"""商品秒杀下单的最小合约；只允许在隔离 MySQL 测试数据库运行。"""

import json
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.lifecycle_services import (
    OrderLifecycleError,
    cancel_order,
    expire_due_orders,
    expire_pending_order,
)
from apps.ordering.models import AddressSnapshot, Cart, CartItem, Order, OrderItem
from apps.ordering.services import OrderRuleError, create_order_from_cart
from apps.payments.gateway import MockGateway
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.services import (
    PaymentRuleError,
    create_payment_attempt,
    receive_payment_event,
)
from apps.promotions.models import FlashSale, FlashSaleItem, FlashSalePurchase
from apps.promotions.services import create_flash_sale_order


User = get_user_model()


class FlashSaleCheckoutTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "mysql", "秒杀并发合约必须由真实 MySQL 验证")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        self.user = User.objects.create_user(
            username="flash_sale_test_customer",
            password="TEST-ONLY-flash-sale-password-123!",
        )
        self.other_user = User.objects.create_user(
            username="flash_sale_test_other_customer",
            password="TEST-ONLY-other-customer-password-123!",
        )
        self.address = self._address(self.user, "测试路 1 号")
        self.second_address = self._address(self.user, "测试路 2 号")
        self.other_address = self._address(self.other_user, "测试路 3 号")
        category = Category.objects.create(
            name="秒杀测试分类", slug="flash-sale-test", is_active=True
        )
        product = Product.objects.create(
            category=category,
            name="秒杀测试鼠标",
            slug="flash-sale-test-mouse",
            is_active=True,
        )
        self.sku = SKU.objects.create(
            product=product,
            code="TEST-FLASH-SALE-MOUSE",
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(sku=self.sku, available_stock=5)
        now = timezone.now()
        flash_sale = FlashSale.objects.create(
            name="测试秒杀活动",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
            is_active=True,
        )
        self.flash_sale_item = FlashSaleItem.objects.create(
            flash_sale=flash_sale,
            sku=self.sku,
            price=Decimal("89.00"),
            is_active=True,
        )
        self.cart = Cart.objects.create(user=self.user)
        self.cart_item = CartItem.objects.create(cart=self.cart, sku=self.sku, quantity=2)

    def _address(self, user, detail):
        return Address.objects.create(
            user=user,
            recipient_name="测试收件人",
            phone="13800000001",
            province="广东省",
            city="深圳市",
            district="南山区",
            detail=detail,
        )

    def _checkout(self, *, key, address_id=None):
        return create_flash_sale_order(
            user=self.user,
            flash_sale_item_id=self.flash_sale_item.pk,
            address_id=self.address.pk if address_id is None else address_id,
            idempotency_key=key,
        )

    def _assert_fact_counts(self, expected):
        for model in (
            Order,
            OrderItem,
            AddressSnapshot,
            InventoryReservation,
            InventoryMovement,
            FlashSalePurchase,
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.count(), expected)

    def _assert_cart_unchanged(self):
        self.assertEqual(Cart.objects.count(), 1)
        self.assertEqual(CartItem.objects.count(), 1)
        self.cart_item.refresh_from_db()
        self.assertEqual(self.cart_item.cart_id, self.cart.pk)
        self.assertEqual(self.cart_item.sku_id, self.sku.pk)
        self.assertEqual(self.cart_item.quantity, 2)

    def _assert_complete_flash_sale_order(self, *, key):
        self._assert_fact_counts(1)
        order = Order.objects.get()
        self.assertEqual(order.user_id, self.user.pk)
        self.assertEqual(order.creation_scope, Order.CreationScope.FLASH_SALE)
        self.assertEqual(order.idempotency_key, key)
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(order.currency, "CNY")
        self.assertEqual(order.subtotal, Decimal("89.00"))
        self.assertEqual(order.discount_total, Decimal("0.00"))
        self.assertEqual(order.shipping_fee, Decimal("0.00"))
        self.assertEqual(order.total, Decimal("89.00"))
        item = OrderItem.objects.get()
        self.assertEqual(item.order_id, order.pk)
        self.assertEqual(item.sku_id, self.sku.pk)
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.unit_price, Decimal("89.00"))
        self.assertEqual(item.discount_amount, Decimal("0.00"))
        self.assertEqual(item.line_total, Decimal("89.00"))
        snapshot = AddressSnapshot.objects.get()
        self.assertEqual(snapshot.order_id, order.pk)
        self.assertEqual(snapshot.detail, self.address.detail)
        reservation = InventoryReservation.objects.get()
        self.assertEqual(reservation.order_item_id, item.pk)
        self.assertEqual(reservation.inventory_id, self.inventory.pk)
        self.assertEqual(reservation.quantity, 1)
        self.assertEqual(reservation.status, InventoryReservation.Status.RESERVED)
        movement = InventoryMovement.objects.get()
        self.assertEqual(movement.inventory_id, self.inventory.pk)
        self.assertEqual(movement.reservation_id, reservation.pk)
        self.assertEqual(movement.movement_type, InventoryMovement.Type.RESERVE)
        self.assertEqual((movement.available_delta, movement.reserved_delta), (-1, 1))
        purchase = FlashSalePurchase.objects.get()
        self.assertEqual(purchase.customer_id, self.user.pk)
        self.assertEqual(purchase.flash_sale_item_id, self.flash_sale_item.pk)
        self.assertEqual(purchase.order_id, order.pk)
        self.assertEqual(purchase.status, FlashSalePurchase.Status.HELD)
        self.assertEqual(purchase.blocking_marker, 1)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (4, 1))
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.price, Decimal("129.00"))
        self.assertEqual(Address.objects.count(), 3)
        self._assert_cart_unchanged()
        return order

    def _assert_no_checkout_effects(self, *, available_stock):
        self._assert_fact_counts(0)
        self.inventory.refresh_from_db()
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (available_stock, 0),
        )
        self.assertEqual(Address.objects.count(), 3)
        self._assert_cart_unchanged()

    def test_same_customer_concurrent_different_keys_reserves_only_one_item(self):
        start = Barrier(2)
        results = Queue()

        def checkout(key):
            close_old_connections()
            connection_id = None
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
                    cursor.execute("SELECT CONNECTION_ID()")
                    connection_id = cursor.fetchone()[0]
                user = User.objects.get(pk=self.user.pk)
                start.wait(timeout=10)
                order, created = create_flash_sale_order(
                    user=user,
                    flash_sale_item_id=self.flash_sale_item.pk,
                    address_id=self.address.pk,
                    idempotency_key=key,
                )
                results.put({
                    "key": key,
                    "connection_id": connection_id,
                    "order_id": order.pk,
                    "created": created,
                })
            except OrderRuleError as exc:
                results.put({
                    "key": key,
                    "connection_id": connection_id,
                    "error_code": exc.code,
                    "http_status": exc.http_status,
                })
            except Exception as exc:
                results.put({
                    "key": key,
                    "connection_id": connection_id,
                    "unexpected_error": repr(exc),
                })
            finally:
                connections.close_all()

        threads = [
            Thread(target=checkout, args=(key,), daemon=True)
            for key in ("flash-sale-concurrent-a", "flash-sale-concurrent-b")
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        finally:
            start.abort()
            for thread in threads:
                if thread.ident is not None and thread.is_alive():
                    thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in threads]
        print("FLASH_SALE_CHECKOUT_RACE " + json.dumps(outcomes, sort_keys=True))
        self.assertTrue(all("unexpected_error" not in outcome for outcome in outcomes), outcomes)
        self.assertTrue(all(outcome["connection_id"] is not None for outcome in outcomes), outcomes)
        self.assertEqual(len({outcome["connection_id"] for outcome in outcomes}), 2)
        winners = [outcome for outcome in outcomes if "created" in outcome]
        rejected = [outcome for outcome in outcomes if "error_code" in outcome]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertIs(winners[0]["created"], True)
        self.assertEqual(len(rejected), 1, outcomes)
        self.assertEqual(rejected[0]["error_code"], "flash_sale_limit_reached")
        self.assertEqual(rejected[0]["http_status"], 409)
        order = self._assert_complete_flash_sale_order(key=winners[0]["key"])
        self.assertEqual(winners[0]["order_id"], order.pk)

    def test_same_key_replays_original_order_without_repeating_reservation(self):
        first, created = self._checkout(key="flash-sale-replay")
        replay, replay_created = self._checkout(key="flash-sale-replay")

        self.assertIs(created, True)
        self.assertIs(replay_created, False)
        self.assertEqual(replay.pk, first.pk)
        self._assert_complete_flash_sale_order(key="flash-sale-replay")

    def test_same_key_with_different_address_returns_idempotency_conflict(self):
        self._checkout(key="flash-sale-address-conflict")

        with self.assertRaises(OrderRuleError) as raised:
            self._checkout(
                key="flash-sale-address-conflict", address_id=self.second_address.pk
            )

        self.assertEqual(raised.exception.code, "idempotency_conflict")
        self.assertEqual(raised.exception.http_status, 409)
        self._assert_complete_flash_sale_order(key="flash-sale-address-conflict")

    def test_cart_and_flash_sale_same_key_create_and_replay_separate_orders(self):
        key = "shared-cart-and-flash-sale-key"
        cart_order, cart_created = create_order_from_cart(
            user=self.user, address_id=self.address.pk, idempotency_key=key
        )
        flash_order, flash_created = self._checkout(key=key)
        cart_replay, cart_replay_created = create_order_from_cart(
            user=self.user, address_id=self.address.pk, idempotency_key=key
        )
        flash_replay, flash_replay_created = self._checkout(key=key)

        self.assertIs(cart_created, True)
        self.assertIs(flash_created, True)
        self.assertNotEqual(cart_order.pk, flash_order.pk)
        self.assertEqual(cart_order.creation_scope, Order.CreationScope.CART)
        self.assertEqual(flash_order.creation_scope, Order.CreationScope.FLASH_SALE)
        self.assertEqual(cart_order.items.get().quantity, 2)
        self.assertEqual(cart_order.items.get().unit_price, Decimal("129.00"))
        self.assertEqual(cart_order.total, Decimal("258.00"))
        self.assertEqual(flash_order.items.get().quantity, 1)
        self.assertEqual(flash_order.items.get().unit_price, Decimal("89.00"))
        self.assertEqual(flash_order.total, Decimal("89.00"))
        self.assertEqual(cart_replay.pk, cart_order.pk)
        self.assertEqual(flash_replay.pk, flash_order.pk)
        self.assertIs(cart_replay_created, False)
        self.assertIs(flash_replay_created, False)
        for model in (Order, OrderItem, AddressSnapshot, InventoryReservation, InventoryMovement):
            with self.subTest(model=model.__name__):
                self.assertEqual(model.objects.count(), 2)
        self.assertEqual(FlashSalePurchase.objects.count(), 1)
        purchase = FlashSalePurchase.objects.get()
        self.assertEqual(purchase.order_id, flash_order.pk)
        self.assertEqual(purchase.status, FlashSalePurchase.Status.HELD)
        self.assertEqual(purchase.blocking_marker, 1)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (2, 3))
        self.assertFalse(CartItem.objects.filter(cart=self.cart).exists())

    def test_other_customers_address_is_rejected_without_checkout_effects(self):
        with self.assertRaises(OrderRuleError) as raised:
            self._checkout(key="flash-sale-other-address", address_id=self.other_address.pk)

        self.assertEqual(raised.exception.code, "address_not_found")
        self.assertEqual(raised.exception.http_status, 404)
        self._assert_no_checkout_effects(available_stock=5)

    def test_no_stock_rolls_back_order_and_purchase_without_touching_cart(self):
        self.inventory.available_stock = 0
        self.inventory.save(update_fields=("available_stock",))

        with self.assertRaises(OrderRuleError) as raised:
            self._checkout(key="flash-sale-no-stock")

        self.assertEqual(raised.exception.code, "insufficient_stock")
        self.assertEqual(raised.exception.http_status, 409)
        self._assert_no_checkout_effects(available_stock=0)

    def test_failure_after_movement_write_rolls_back_all_facts_and_allows_retry(self):
        class InjectedCheckoutFailure(Exception):
            pass

        real_create_movement = InventoryMovement.objects.create

        def fail_after_write(**fields):
            real_create_movement(**fields)
            # 先确认六种事实和库存变化已写入，再验证最外层事务能一起回滚。
            self._assert_fact_counts(1)
            self.inventory.refresh_from_db()
            self.assertEqual(
                (self.inventory.available_stock, self.inventory.reserved_stock), (4, 1)
            )
            raise InjectedCheckoutFailure("TEST-ONLY failure after inventory movement write")

        with patch.object(InventoryMovement.objects, "create", side_effect=fail_after_write):
            with self.assertRaises(InjectedCheckoutFailure):
                self._checkout(key="flash-sale-write-failure")

        self._assert_no_checkout_effects(available_stock=5)
        _, created = self._checkout(key="flash-sale-write-failure")
        self.assertIs(created, True)
        self._assert_complete_flash_sale_order(key="flash-sale-write-failure")

    def test_existing_lifecycle_services_do_not_process_flash_sale_order_yet(self):
        order, _ = self._checkout(key="flash-sale-lifecycle-boundary")

        with self.assertRaises(PaymentRuleError) as payment_error:
            create_payment_attempt(
                user=self.user,
                order_public_id=order.public_id,
                idempotency_key="flash-sale-payment-boundary",
            )
        self.assertEqual(payment_error.exception.code, "flash_sale_lifecycle_unavailable")
        self.assertEqual(payment_error.exception.http_status, 409)
        self.assertEqual(PaymentAttempt.objects.count(), 0)
        with self.assertRaises(OrderLifecycleError) as cancel_error:
            cancel_order(public_id=order.public_id, user=self.user)
        self.assertEqual(cancel_error.exception.code, "flash_sale_lifecycle_unavailable")
        self.assertEqual(cancel_error.exception.http_status, 409)

        # 到期数据只用于证明现有扫描也受边界保护，不表示秒杀释放流程已实现。
        as_of = timezone.now()
        InventoryReservation.objects.filter(order_item__order=order).update(
            expires_at=as_of - timedelta(seconds=1)
        )
        expired_order, changed = expire_pending_order(order_id=order.pk, as_of=as_of)
        self.assertEqual(expired_order.pk, order.pk)
        self.assertIs(changed, False)
        self.assertEqual(expire_due_orders(as_of=as_of), (0, 0))
        self.assertEqual(PaymentAttempt.objects.count(), 0)
        self._assert_complete_flash_sale_order(key="flash-sale-lifecycle-boundary")

        # 历史夹具故意绕过公共创建入口，证明处理器自身也会拒绝秒杀核销。
        attempt = PaymentAttempt.objects.create(
            order=order,
            user=self.user,
            amount=order.total,
            currency="CNY",
            attempt_number=1,
            idempotency_key="flash-sale-historical-attempt-fixture",
            request_fingerprint="f" * 64,
            active_marker=1,
        )
        gateway = MockGateway()
        payload, signature = gateway.create_payment(attempt)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertTrue(gateway.verify_event(payload, signature))
        with override_settings(PAYMENT_EVENTS_ASYNC=False):
            event = receive_payment_event(payload=payload, signature=signature)

        event.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.REJECTED)
        self.assertEqual(event.error_code, "flash_sale_lifecycle_unavailable")
        self.assertEqual(event.attempt_id, attempt.pk)
        self.assertEqual(attempt.status, PaymentAttempt.Status.CREATED)
        self.assertEqual(attempt.active_marker, 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(movement_type=InventoryMovement.Type.COMMIT).count(),
            0,
        )
        self._assert_complete_flash_sale_order(key="flash-sale-lifecycle-boundary")
