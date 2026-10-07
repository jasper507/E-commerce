"""完整秒杀事务的两顾客竞争基线；只在隔离 MySQL 测试库运行。"""

import json
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event, Thread

from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, connection, connections, transaction

from apps.accounts.models import Address
from apps.catalog.models import SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import AddressSnapshot, Cart, CartItem, Order, OrderItem
from apps.ordering.services import OrderRuleError
from apps.promotions.models import FlashSalePurchase
from apps.promotions.services import create_flash_sale_order
from apps.promotions.tests.test_flash_sale_checkout import FlashSaleCheckoutFixture


User = get_user_model()


class FlashSaleContentionFixture(FlashSaleCheckoutFixture):
    def setUp(self):
        super().setUp()
        self.assertTrue(connection.features.has_select_for_update_nowait)
        self.inventory.available_stock = 1
        self.inventory.save(update_fields=("available_stock",))
        self.other_cart = Cart.objects.create(user=self.other_user)
        self.other_cart_item = CartItem.objects.create(
            cart=self.other_cart, sku=self.sku, quantity=3
        )
        self.first = {
            "user_id": self.user.pk,
            "address_id": self.address.pk,
            "detail": self.address.detail,
            "key": "flash-contention-first",
        }
        self.second = {
            "user_id": self.other_user.pk,
            "address_id": self.other_address.pk,
            "detail": self.other_address.detail,
            "key": "flash-contention-second",
        }

    def _connection_id(self):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
            cursor.execute("SELECT CONNECTION_ID()")
            return cursor.fetchone()[0]

    def _create(self, customer):
        return create_flash_sale_order(
            user=User.objects.get(pk=customer["user_id"]),
            flash_sale_item_id=self.flash_sale_item.pk,
            address_id=customer["address_id"],
            idempotency_key=customer["key"],
        )

    def _owned_fact_counts(self, user_id):
        return {
            "Order": Order.objects.filter(user_id=user_id).count(),
            "OrderItem": OrderItem.objects.filter(order__user_id=user_id).count(),
            "AddressSnapshot": AddressSnapshot.objects.filter(order__user_id=user_id).count(),
            "FlashSalePurchase": FlashSalePurchase.objects.filter(customer_id=user_id).count(),
            "InventoryReservation": InventoryReservation.objects.filter(
                order_item__order__user_id=user_id
            ).count(),
            "InventoryMovement": InventoryMovement.objects.filter(
                reservation__order_item__order__user_id=user_id
            ).count(),
        }

    def _snapshot(self):
        # 所有字段均来自当前连接的实际查询，包含未提交窗口的可见性差异。
        models = (
            Order, OrderItem, AddressSnapshot, FlashSalePurchase,
            InventoryReservation, InventoryMovement,
        )
        return {
            "fact_counts": {model.__name__: model.objects.count() for model in models},
            "owned_fact_counts": {
                str(customer["user_id"]): self._owned_fact_counts(customer["user_id"])
                for customer in (self.first, self.second)
            },
            "orders": list(Order.objects.order_by("pk").values(
                "id", "user_id", "idempotency_key", "creation_scope", "status", "total"
            )),
            "items": list(OrderItem.objects.order_by("pk").values(
                "order_id", "sku_id", "quantity", "unit_price", "line_total"
            )),
            "address_snapshots": list(AddressSnapshot.objects.order_by("pk").values(
                "order_id", "detail"
            )),
            "purchases": list(FlashSalePurchase.objects.order_by("pk").values(
                "customer_id", "order_id", "flash_sale_item_id", "status", "blocking_marker"
            )),
            "reservations": list(InventoryReservation.objects.order_by("pk").values(
                "id", "order_item_id", "inventory_id", "quantity", "status"
            )),
            "movements": list(InventoryMovement.objects.order_by("pk").values(
                "reservation_id", "inventory_id", "movement_type",
                "available_delta", "reserved_delta"
            )),
            "inventory": Inventory.objects.values(
                "available_stock", "reserved_stock"
            ).get(pk=self.inventory.pk),
            "ordinary_sku_price": SKU.objects.values_list(
                "price", flat=True
            ).get(pk=self.sku.pk),
            "carts": list(CartItem.objects.order_by("cart__user_id").values(
                "cart_id", "cart__user_id", "sku_id", "quantity"
            )),
        }

    def _assert_carts_unchanged(self):
        self.assertEqual(Cart.objects.count(), 2)
        self.assertEqual(CartItem.objects.count(), 2)
        for cart, cart_item, customer, quantity in (
            (self.cart, self.cart_item, self.first, 2),
            (self.other_cart, self.other_cart_item, self.second, 3),
        ):
            cart.refresh_from_db()
            cart_item.refresh_from_db()
            self.assertEqual(cart.user_id, customer["user_id"])
            self.assertEqual(
                (cart_item.cart_id, cart_item.sku_id, cart_item.quantity),
                (cart.pk, self.sku.pk, quantity),
            )

    def _assert_no_customer_facts(self, customer):
        self.assertTrue(all(
            count == 0 for count in self._owned_fact_counts(customer["user_id"]).values()
        ))
        self.assertFalse(Order.objects.filter(
            user_id=customer["user_id"],
            creation_scope=Order.CreationScope.FLASH_SALE,
            idempotency_key=customer["key"],
        ).exists())

    def _assert_empty(self):
        self._assert_fact_counts(0)
        for customer in (self.first, self.second):
            self._assert_no_customer_facts(customer)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (1, 0))
        self._assert_carts_unchanged()

    def _assert_complete_order(self, winner, loser):
        self._assert_fact_counts(1)
        self._assert_no_customer_facts(loser)
        self.assertTrue(all(
            count == 1 for count in self._owned_fact_counts(winner["user_id"]).values()
        ))
        order = Order.objects.get()
        self.assertEqual(order.user_id, winner["user_id"])
        self.assertEqual(order.idempotency_key, winner["key"])
        self.assertEqual(order.creation_scope, Order.CreationScope.FLASH_SALE)
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(order.currency, "CNY")
        self.assertEqual((order.subtotal, order.total), (Decimal("89.00"), Decimal("89.00")))
        self.assertEqual((order.discount_total, order.shipping_fee), (Decimal("0.00"), Decimal("0.00")))
        item = OrderItem.objects.get()
        self.assertEqual((item.order_id, item.sku_id, item.quantity), (order.pk, self.sku.pk, 1))
        self.assertEqual((item.unit_price, item.line_total), (Decimal("89.00"), Decimal("89.00")))
        self.assertEqual(item.discount_amount, Decimal("0.00"))
        snapshot = AddressSnapshot.objects.get()
        self.assertEqual((snapshot.order_id, snapshot.detail), (order.pk, winner["detail"]))
        purchase = FlashSalePurchase.objects.get()
        self.assertEqual(
            (purchase.customer_id, purchase.order_id, purchase.flash_sale_item_id),
            (winner["user_id"], order.pk, self.flash_sale_item.pk),
        )
        self.assertEqual((purchase.status, purchase.blocking_marker), (FlashSalePurchase.Status.HELD, 1))
        reservation = InventoryReservation.objects.get()
        self.assertEqual(
            (reservation.order_item_id, reservation.inventory_id, reservation.quantity),
            (item.pk, self.inventory.pk, 1),
        )
        self.assertEqual(reservation.status, InventoryReservation.Status.RESERVED)
        movement = InventoryMovement.objects.get()
        self.assertEqual((movement.reservation_id, movement.inventory_id), (reservation.pk, self.inventory.pk))
        self.assertEqual(movement.movement_type, InventoryMovement.Type.RESERVE)
        self.assertEqual((movement.available_delta, movement.reserved_delta), (-1, 1))
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (0, 1))
        self.sku.refresh_from_db()
        self.assertEqual(self.sku.price, Decimal("129.00"))
        self.assertEqual(Address.objects.count(), 3)
        self._assert_carts_unchanged()
        return order

    def _emit(self, scenario, **observations):
        print("FLASH_SALE_CONTENTION " + json.dumps(
            {"scenario": scenario, **observations}, default=str, sort_keys=True
        ))

class FlashSaleContentionTests(FlashSaleContentionFixture):
    def test_two_customers_contend_for_one_item_without_partial_orders(self):
        observer_connection_id = self._connection_id()
        start, results = Barrier(2), Queue()

        def checkout(customer):
            close_old_connections()
            outcome = {"user_id": customer["user_id"], "key": customer["key"]}
            try:
                outcome["connection_id"] = self._connection_id()
                start.wait(timeout=10)
                order, created = self._create(customer)
                outcome.update(order_id=order.pk, created=created)
            except OrderRuleError as exc:
                outcome.update(error_code=exc.code, http_status=exc.http_status)
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                results.put(outcome)
                connections.close_all()

        threads = [Thread(target=checkout, args=(customer,), daemon=True)
                   for customer in (self.first, self.second)]
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
        self.assertTrue(all("unexpected_error" not in outcome for outcome in outcomes), outcomes)
        self.assertEqual(len({observer_connection_id, *(o["connection_id"] for o in outcomes)}), 3)
        winners = [outcome for outcome in outcomes if "created" in outcome]
        losers = [outcome for outcome in outcomes if "error_code" in outcome]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertEqual(len(losers), 1, outcomes)
        self.assertIs(winners[0]["created"], True)
        self.assertEqual(losers[0]["http_status"], 409)
        self.assertIn(losers[0]["error_code"], {"stock_busy", "insufficient_stock"})
        winner, loser = (self.first, self.second) if winners[0]["user_id"] == self.first["user_id"] else (self.second, self.first)
        order = self._assert_complete_order(winner, loser)
        self.assertEqual(winners[0]["order_id"], order.pk)
        self._emit("two_customers_stock_one", observer_connection_id=observer_connection_id,
                   outcomes=outcomes, final=self._snapshot())

    def _held_checkout(self, *, rollback):
        observer_connection_id = self._connection_id()
        ready, release, inventory_query_started, done = Event(), Event(), Event(), Event()
        holder_results, contender_results = Queue(), Queue()
        inventory_queries = []

        class InjectedRollback(Exception):
            pass

        def holder():
            close_old_connections()
            outcome = {"user_id": self.first["user_id"], "key": self.first["key"]}
            try:
                outcome["connection_id"] = self._connection_id()
                # 延长真实完整下单的未提交窗口，不替换任何业务写入或锁算法。
                with transaction.atomic():
                    order, created = self._create(self.first)
                    outcome.update(order_id=order.pk, created=created)
                    outcome["uncommitted_snapshot"] = self._snapshot()
                    self.assertIs(created, True)
                    self.assertTrue(all(count == 1 for count in outcome["uncommitted_snapshot"]["fact_counts"].values()))
                    self.assertEqual(outcome["uncommitted_snapshot"]["inventory"], {"available_stock": 0, "reserved_stock": 1})
                    ready.set()
                    if not release.wait(timeout=30):
                        raise TimeoutError("holder release was not signaled")
                    if rollback:
                        raise InjectedRollback()
                outcome["transaction_result"] = "committed"
            except InjectedRollback:
                outcome["transaction_result"] = "rolled_back"
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                holder_results.put(outcome)
                connections.close_all()

        def observe_inventory_query(execute, sql, params, many, context):
            if "inventory_inventory" not in sql or "FOR UPDATE" not in sql.upper():
                return execute(sql, params, many, context)
            observation = {"sql": sql, "nowait": "NOWAIT" in sql.upper()}
            inventory_queries.append(observation)
            inventory_query_started.set()
            try:
                return execute(sql, params, many, context)
            except DatabaseError as exc:
                observation["mysql_error_number"] = exc.args[0] if exc.args else None
                raise

        def contender():
            close_old_connections()
            outcome = {"user_id": self.second["user_id"], "key": self.second["key"]}
            try:
                outcome["connection_id"] = self._connection_id()
                with connection.execute_wrapper(observe_inventory_query):
                    # 服务已退出 atomic 后捕获；避免冻结异常穿过观察器的生成器上下文。
                    try:
                        order, created = self._create(self.second)
                    except OrderRuleError as exc:
                        outcome.update(error_code=exc.code, http_status=exc.http_status)
                    else:
                        outcome.update(order_id=order.pk, created=created)
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                try:
                    outcome["snapshot_after_return"] = self._snapshot()
                    outcome["returned_before_release"] = not release.is_set()
                except Exception as exc:
                    outcome["unexpected_error"] = repr(exc)
                contender_results.put(outcome)
                connections.close_all()
                done.set()

        first_thread = Thread(target=holder, daemon=True)
        second_thread = Thread(target=contender, daemon=True)
        before_release = None
        first_thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "首单未完成真实下单并进入未提交窗口")
            second_thread.start()
            self.assertTrue(inventory_query_started.wait(timeout=10), "第二个顾客尚未到达库存锁")
            self.assertTrue(done.wait(timeout=10), "第二个顾客没有在首单释放锁之前完成返回")
            self.assertFalse(release.is_set())
            self._assert_empty()
            before_release = self._snapshot()
        finally:
            release.set()
            first_thread.join(timeout=15)
            if second_thread.ident is not None:
                second_thread.join(timeout=15)
        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(holder_results.qsize(), 1)
        self.assertEqual(contender_results.qsize(), 1)
        first_outcome, second_outcome = holder_results.get_nowait(), contender_results.get_nowait()
        self.assertNotIn("unexpected_error", first_outcome, first_outcome)
        self.assertNotIn("unexpected_error", second_outcome, second_outcome)
        self.assertEqual(len({observer_connection_id, first_outcome["connection_id"], second_outcome["connection_id"]}), 3)
        self.assertEqual(first_outcome["transaction_result"], "rolled_back" if rollback else "committed")
        self.assertEqual((second_outcome["error_code"], second_outcome["http_status"]), ("stock_busy", 409))
        self.assertIs(second_outcome["returned_before_release"], True)
        self.assertEqual(len(inventory_queries), 1, inventory_queries)
        self.assertIs(inventory_queries[0]["nowait"], True)
        self.assertEqual(inventory_queries[0]["mysql_error_number"], 3572)
        after_release = self._snapshot()
        if rollback:
            self._assert_empty()
            order, created = self._create(self.second)
            self.assertIs(created, True)
            self.assertEqual(order.pk, self._assert_complete_order(self.second, self.first).pk)
            retry = {"order_id": order.pk, "created": created}
        else:
            with self.assertRaises(OrderRuleError) as raised:
                self._create(self.second)
            self.assertEqual((raised.exception.code, raised.exception.http_status), ("insufficient_stock", 409))
            retry = {"error_code": raised.exception.code, "http_status": raised.exception.http_status}
            order = self._assert_complete_order(self.first, self.second)
            replay, created = self._create(self.first)
            self.assertIs(created, False)
            self.assertEqual(replay.pk, order.pk)
            self._assert_complete_order(self.first, self.second)
            retry["winner_replay"] = {"order_id": replay.pk, "created": created}
        self._emit("held_first_rollback" if rollback else "held_first_commit",
                   observer_connection_id=observer_connection_id, holder=first_outcome,
                   contender=second_outcome, inventory_queries=inventory_queries,
                   committed_view_before_release=before_release,
                   after_release=after_release, retry=retry, final=self._snapshot())

    def test_second_customer_returns_busy_before_first_commit_then_sold_out(self):
        self._held_checkout(rollback=False)

    def test_second_customer_can_retry_original_key_after_first_rollback(self):
        self._held_checkout(rollback=True)
