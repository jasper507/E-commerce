"""真实下单 API 的并发合约；只在 pytest 隔离 MySQL 数据库运行。"""

import json
from collections import Counter
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event, Thread
from time import perf_counter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from rest_framework.test import APIClient, APITransactionTestCase

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import AddressSnapshot, Cart, CartItem, Order, OrderItem


User = get_user_model()


class CheckoutContentionTests(APITransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "mysql", "锁合约必须由 MySQL 验证")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        self.assertTrue(connection.features.has_select_for_update_of)
        category = Category.objects.create(name="并发实验", slug="contention", is_active=True)
        product = Product.objects.create(
            category=category, name="实验鼠标", slug="contention-mouse", is_active=True
        )
        self.sku = SKU.objects.create(
            product=product, code="CONTENTION-MOUSE", price=Decimal("129.00"), is_active=True
        )
        self.inventory = Inventory.objects.create(sku=self.sku, available_stock=1)

    def _customer(self, number):
        user = User.objects.create_user(
            username=f"contention_{number}", password="S3cure-test-pass!"
        )
        address = Address.objects.create(
            user=user,
            recipient_name="并发测试顾客",
            phone="13800138000",
            province="广东省",
            city="深圳市",
            district="南山区",
            detail="测试路 1 号",
        )
        cart = Cart.objects.create(user=user)
        CartItem.objects.create(cart=cart, sku=self.sku, quantity=1)
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.get("/api/auth/csrf/")
        self.assertEqual(response.status_code, 204)
        return {
            "user": user,
            "address": address,
            "client": client,
            "csrf": client.cookies[settings.CSRF_COOKIE_NAME].value,
            "key": f"contention-{number}",
        }

    def _post(self, customer):
        return customer["client"].post(
            "/api/orders/",
            {"address_id": customer["address"].pk},
            format="json",
            HTTP_X_CSRFTOKEN=customer["csrf"],
            HTTP_IDEMPOTENCY_KEY=customer["key"],
        )

    def _assert_complete_order(self, winner, losers):
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertEqual(AddressSnapshot.objects.count(), 1)
        self.assertEqual(InventoryReservation.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        order = Order.objects.get()
        self.assertEqual(order.user_id, winner["user"].pk)
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(order.total, Decimal("129.00"))
        self.assertEqual(order.items.get().quantity, 1)
        self.assertEqual(order.address_snapshot.detail, winner["address"].detail)
        reservation = InventoryReservation.objects.get()
        self.assertEqual(reservation.order_item.order_id, order.pk)
        self.assertEqual(reservation.status, InventoryReservation.Status.RESERVED)
        self.assertEqual(reservation.quantity, 1)
        movement = InventoryMovement.objects.get()
        self.assertEqual(movement.reservation_id, reservation.pk)
        self.assertEqual(movement.movement_type, InventoryMovement.Type.RESERVE)
        self.assertEqual((movement.available_delta, movement.reserved_delta), (-1, 1))
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (0, 1))
        self.assertFalse(CartItem.objects.filter(cart__user=winner["user"]).exists())
        for loser in losers:
            self.assertFalse(Order.objects.filter(user=loser["user"]).exists())
            self.assertEqual(CartItem.objects.get(cart__user=loser["user"]).quantity, 1)

    def _held_checkout(self, *, rollback):
        first, second = self._customer(1), self._customer(2)
        ready, release, done, cart_query_started = Event(), Event(), Event(), Event()
        holder_results, contender_results = Queue(), Queue()
        traces = []

        class InjectedRollback(Exception):
            pass

        def holder():
            try:
                # 外层事务只用于故障注入：延长完整下单事务的未提交窗口。
                with transaction.atomic():
                    response = self._post(first)
                    if response.status_code != 201:
                        raise AssertionError(response.json())
                    ready.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("holder release not signaled")
                    if rollback:
                        raise InjectedRollback()
                holder_results.put("committed")
            except InjectedRollback:
                holder_results.put("rolled_back")
            except Exception as exc:
                holder_results.put(exc)
            finally:
                connections.close_all()

        def trace(execute, sql, params, many, context):
            if "FOR UPDATE" in sql and "ordering_cartitem" in sql:
                traces.append(sql)
                cart_query_started.set()
            return execute(sql, params, many, context)

        def contender():
            try:
                started = perf_counter()
                with connection.execute_wrapper(trace):
                    response = self._post(second)
                contender_results.put((response.status_code, response.json(), perf_counter() - started))
            except Exception as exc:
                contender_results.put(exc)
            finally:
                done.set()
                connections.close_all()

        holder_thread = Thread(target=holder)
        contender_thread = Thread(target=contender)
        holder_thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "首单没有进入未提交窗口")
            contender_thread.start()
            self.assertTrue(cart_query_started.wait(timeout=10))
            returned_while_locked = done.wait(timeout=1)
        finally:
            release.set()
            holder_thread.join(timeout=15)
            if contender_thread.ident is not None:
                contender_thread.join(timeout=15)
        self.assertFalse(holder_thread.is_alive())
        self.assertFalse(contender_thread.is_alive())
        self.assertEqual(holder_results.get_nowait(), "rolled_back" if rollback else "committed")
        outcome = contender_results.get_nowait()
        self.assertIsInstance(outcome, tuple, repr(outcome))
        status, payload, elapsed = outcome
        print("CHECKOUT_HELD " + json.dumps({
            "first": "rollback" if rollback else "commit",
            "returned_before_release": returned_while_locked,
            "second_status": status,
            "second_code": payload.get("code"),
            "second_ms": round(elapsed * 1000, 2),
            "cart_lock_sql": traces,
        }))
        self.assertTrue(returned_while_locked, "第二个顾客在到达库存 NOWAIT 前等待了其他锁")
        self.assertEqual((status, payload.get("code")), (409, "stock_busy"))
        if rollback:
            self.assertFalse(Order.objects.exists())
            self.assertFalse(OrderItem.objects.exists())
            self.assertFalse(AddressSnapshot.objects.exists())
            self.assertFalse(InventoryReservation.objects.exists())
            self.assertFalse(InventoryMovement.objects.exists())
            self.assertEqual(CartItem.objects.count(), 2)
            self.inventory.refresh_from_db()
            self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (1, 0))
        retry = self._post(second)
        if rollback:
            self.assertEqual(retry.status_code, 201)
            self._assert_complete_order(second, [first])
        else:
            self.assertEqual((retry.status_code, retry.json()["code"]), (409, "insufficient_stock"))
            self._assert_complete_order(first, [second])
            replay = self._post(first)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json()["public_id"], str(Order.objects.get().public_id))
            self._assert_complete_order(first, [second])

    def test_competing_checkout_returns_busy_before_first_commit_then_sold_out(self):
        self._held_checkout(rollback=False)

    def test_competing_checkout_returns_busy_and_can_retry_after_first_rollback(self):
        self._held_checkout(rollback=True)

    def test_ten_customers_contend_for_one_item_without_partial_orders(self):
        customers = [self._customer(number) for number in range(10)]
        start = Barrier(len(customers))
        results = Queue()

        def checkout(index):
            try:
                connection.ensure_connection()
                start.wait(timeout=10)
                started = perf_counter()
                response = self._post(customers[index])
                results.put((index, response.status_code, response.json(), perf_counter() - started))
            except Exception as exc:
                results.put(exc)
            finally:
                connections.close_all()

        threads = [Thread(target=checkout, args=(index,)) for index in range(len(customers))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        outcomes = [results.get_nowait() for _ in threads]
        self.assertTrue(all(isinstance(outcome, tuple) for outcome in outcomes), repr(outcomes))
        winners = [index for index, status, _, _ in outcomes if status == 201]
        self.assertEqual(len(winners), 1, repr(outcomes))
        for index, status, payload, _ in outcomes:
            if index not in winners:
                self.assertEqual(status, 409)
                self.assertIn(payload["code"], {"stock_busy", "insufficient_stock"})
        print("CHECKOUT_BURST " + json.dumps({
            "customers": len(customers),
            "results": dict(Counter("created" if status == 201 else payload["code"] for _, status, payload, _ in outcomes)),
            "request_ms": sorted(round(elapsed * 1000, 2) for _, _, _, elapsed in outcomes),
        }))
        self._assert_complete_order(customers[winners[0]], [customer for index, customer in enumerate(customers) if index not in winners])
