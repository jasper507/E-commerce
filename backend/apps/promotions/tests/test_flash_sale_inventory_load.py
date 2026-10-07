"""显式启用的完整秒杀事务负载实验；默认跳过，只使用隔离 MySQL。"""

import json
import os
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from queue import Empty, Queue
from threading import Barrier, BrokenBarrierError, Event, Thread
from time import monotonic, perf_counter_ns, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import AddressSnapshot, Cart, CartItem, Order, OrderItem
from apps.ordering.services import OrderRuleError
from apps.promotions.models import FlashSale, FlashSaleItem, FlashSalePurchase
from apps.promotions.services import create_flash_sale_order
from apps.promotions.tests.test_flash_sale_contention import (
    _nowait_reserve_inventory,
)


User = get_user_model()
LOAD_ENABLED = os.getenv("PROJECT_ONE_TEST_FLASH_SALE_LOAD", "").lower() == "true"
CONNECTION_TIMEOUTS = {"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10}
LOCK_WAIT_TIMEOUT_SECONDS = 5
RETRY_BACKOFF_NS = (5_000_000, 10_000_000)


def _mysql_error_number(exc):
    """业务繁忙异常保留原数据库异常；只读取真正的整数错误码。"""
    seen = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current.args and isinstance(current.args[0], int):
            return current.args[0]
        pending.extend(error for error in (current.__cause__, current.__context__) if error)
    return None


@skipUnless(LOAD_ENABLED, "Requires PROJECT_ONE_TEST_FLASH_SALE_LOAD=true and isolated MySQL.")
class FlashSaleInventoryLoadTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "mysql")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        self.assertTrue(connection.features.has_select_for_update_nowait)

    def _fixture(self, *, name, stock, jobs):
        category = Category.objects.create(name=name, slug=name, is_active=True)
        product = Product.objects.create(
            category=category, name="负载实验鼠标", slug=name, is_active=True,
        )
        sku = SKU.objects.create(
            product=product, code=name, price=Decimal("129.00"), is_active=True,
        )
        inventory = Inventory.objects.create(sku=sku, available_stock=stock)
        now = timezone.now()
        sale = FlashSale.objects.create(
            name=name, starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1), is_active=True,
        )
        item = FlashSaleItem.objects.create(
            flash_sale=sale, sku=sku, price=Decimal("89.00"), is_active=True,
        )
        customers = []
        for index in range(jobs):
            # 只测服务层；这些顾客不能登录，也不把密码哈希混入准备工作。
            user = User.objects.create(username=f"{name.replace('-', '_')}_{index}", password="!")
            detail = f"负载实验路 {index + 1} 号"
            address = Address.objects.create(
                user=user, recipient_name="测试顾客", phone="13800000001",
                province="广东省", city="深圳市", district="南山区", detail=detail,
            )
            cart = Cart.objects.create(user=user)
            quantity = 2 if index % 2 == 0 else 3
            cart_item = CartItem.objects.create(cart=cart, sku=sku, quantity=quantity)
            customers.append({
                "job_id": index, "user": user, "user_id": user.pk,
                "address_id": address.pk, "address_detail": detail,
                "cart_id": cart.pk, "cart_item_id": cart_item.pk,
                "cart_quantity": quantity, "idempotency_key": f"{name}-{index}",
            })
        return {"sku_id": sku.pk, "inventory_id": inventory.pk,
                "flash_sale_item_id": item.pk, "customers": customers, "initial_stock": stock}

    def _strategy(self, strategy):
        if strategy == "nowait":
            return patch("apps.promotions.services._reserve_flash_sale_inventory",
                         new=_nowait_reserve_inventory)
        self.assertEqual(strategy, "conditional")
        return nullcontext()

    def _job(self, *, customer, fixture, worker_id, connection_id, max_attempts, clock, stop):
        stamp = lambda: perf_counter_ns() - clock["origin_ns"]
        outcome = {key: customer[key] for key in (
            "job_id", "user_id", "address_id", "idempotency_key",
        )}
        outcome.update(worker_id=worker_id, connection_id=connection_id,
                       submitted_ns=0, started_ns=stamp(), attempts=[], backoffs=[])
        for number in range(1, max_attempts + 1):
            if stop.is_set():
                outcome["terminal_code"] = "aborted"
                break
            attempt = {"number": number, "started_ns": stamp()}
            try:
                order, created = create_flash_sale_order(
                    user=customer["user"], flash_sale_item_id=fixture["flash_sale_item_id"],
                    address_id=customer["address_id"], idempotency_key=customer["idempotency_key"],
                )
            except OrderRuleError as exc:
                attempt["ended_ns"] = stamp()
                attempt.update(code=exc.code, http_status=exc.http_status,
                               mysql_error_number=_mysql_error_number(exc))
            except Exception as exc:
                attempt["ended_ns"] = stamp()
                attempt.update(code="unexpected_error", error_type=type(exc).__name__,
                               error=repr(exc), mysql_error_number=_mysql_error_number(exc))
                stop.set()
            else:
                attempt["ended_ns"] = stamp()
                attempt.update(code="created", created=created, order_id=order.pk)
            outcome["attempts"].append(attempt)
            outcome["terminal_code"] = attempt["code"]
            if attempt["code"] == "created":
                outcome.update(order_id=attempt["order_id"], created=attempt["created"])
                break
            if attempt["code"] != "stock_busy" or number == max_attempts:
                break
            backoff = {"after_attempt": number, "planned_ns": RETRY_BACKOFF_NS[number - 1],
                       "started_ns": stamp()}
            sleep(backoff["planned_ns"] / 1_000_000_000)
            backoff["ended_ns"] = stamp()
            outcome["backoffs"].append(backoff)
        outcome["terminal_ns"] = stamp()
        return outcome

    def _run(self, *, fixture, strategy, profile, repeat, max_attempts, workers, warmup=False):
        jobs, ready, results, worker_results = Queue(), Queue(), Queue(), Queue()
        for customer in fixture["customers"]:
            jobs.put(customer)
        for _ in range(workers):
            jobs.put(None)
        barrier, stop, clock = Barrier(workers + 1), Event(), {}
        diagnostics = []

        def worker(worker_id):
            record = {"worker_id": worker_id, "setup_started_ns": perf_counter_ns()}
            db = connections["default"]
            try:
                # DatabaseWrapper 属于此线程；复制后才加实验超时，不改变共享配置。
                db.settings_dict = {**db.settings_dict,
                                    "OPTIONS": {**db.settings_dict["OPTIONS"], **CONNECTION_TIMEOUTS}}
                db.ensure_connection()
                record["session_open_ns"] = perf_counter_ns()
                with db.cursor() as cursor:
                    cursor.execute(f"SET SESSION innodb_lock_wait_timeout = {LOCK_WAIT_TIMEOUT_SECONDS}")
                    cursor.execute("SELECT CONNECTION_ID()")
                    record["connection_id"] = cursor.fetchone()[0]
                record["setup_finished_ns"] = perf_counter_ns()
                ready.put(record)
                barrier.wait(timeout=15)
                while not stop.is_set():
                    customer = jobs.get_nowait()
                    if customer is None:
                        break
                    results.put(self._job(
                        customer=customer, fixture=fixture, worker_id=worker_id,
                        connection_id=record["connection_id"], max_attempts=max_attempts,
                        clock=clock, stop=stop,
                    ))
            except (BrokenBarrierError, Empty) as exc:
                record["worker_error"] = f"{type(exc).__name__}: {exc}"
                stop.set()
            except Exception as exc:
                record.update(worker_error=repr(exc), error_type=type(exc).__name__,
                              mysql_error_number=_mysql_error_number(exc))
                stop.set()
            finally:
                if "setup_finished_ns" not in record:
                    ready.put(record)
                record["close_started_ns"] = perf_counter_ns()
                try:
                    connections.close_all()
                except Exception as exc:
                    record["close_error"] = repr(exc)
                record["closed_ns"] = perf_counter_ns()
                record["connection_closed"] = db.connection is None
                worker_results.put(record)

        threads = [Thread(target=worker, args=(index,), daemon=True) for index in range(workers)]
        observed_connections = None
        strategy_context = self._strategy(strategy)
        strategy_context.__enter__()
        try:
            try:
                for thread in threads:
                    thread.start()
                setup_records = [ready.get(timeout=15) for _ in threads]
                if stop.is_set():
                    raise AssertionError("worker connection setup failed")
                ids = [record["connection_id"] for record in setup_records]
                with connection.cursor() as cursor:
                    placeholders = ", ".join("%s" for _ in ids)
                    cursor.execute(
                        f"SELECT ID FROM information_schema.processlist WHERE ID IN ({placeholders}) AND DB = %s",
                        (*ids, connection.settings_dict["NAME"]),
                    )
                    observed_connections = sorted(row[0] for row in cursor.fetchall())
                self.assertEqual(observed_connections, sorted(ids))
                clock["origin_ns"] = perf_counter_ns()
                barrier.wait(timeout=15)
                deadline = monotonic() + 45
                for thread in threads:
                    thread.join(timeout=max(0, deadline - monotonic()))
            except Exception as exc:
                diagnostics.append({"stage": "batch", "error_type": type(exc).__name__, "error": repr(exc)})
            finally:
                stop.set()
                barrier.abort()
                deadline = monotonic() + 15
                for thread in threads:
                    if thread.ident is not None and thread.is_alive():
                        thread.join(timeout=max(0, deadline - monotonic()))
                alive = [index for index, thread in enumerate(threads) if thread.is_alive()]
                if alive:
                    diagnostics.append({"stage": "cleanup", "alive_worker_ids": alive})
        finally:
            # 无法清理的实验必须失败；仍在运行的调用不能中途切换库存算法。
            if not any(thread.is_alive() for thread in threads):
                strategy_context.__exit__(None, None, None)
        # 测量窗口结束后才查询业务事实；不在计时中增加观察器、轮询或人为持锁。
        origin = clock.get("origin_ns", perf_counter_ns())
        outcomes = sorted(list(results.queue), key=lambda row: row["job_id"])
        records = sorted(list(worker_results.queue), key=lambda row: row["worker_id"])
        for record in records:
            for name in ("setup_started_ns", "session_open_ns", "setup_finished_ns", "close_started_ns", "closed_ns"):
                if name in record:
                    record[name] -= origin
        completed_ids = {outcome["job_id"] for outcome in outcomes}
        unstarted = [{"job_id": customer["job_id"], "user_id": customer["user_id"],
                      "idempotency_key": customer["idempotency_key"], "submitted_ns": 0,
                      "terminal_code": "not_started"}
                     for customer in fixture["customers"] if customer["job_id"] not in completed_ids]
        observation = {
            "strategy": strategy, "profile": profile, "repeat": repeat, "warmup": warmup,
            "initial_stock": fixture["initial_stock"], "jobs_count": len(fixture["customers"]),
            "worker_count": workers, "max_attempts": max_attempts,
            "retry_backoff_ns": list(RETRY_BACKOFF_NS),
            "connection_timeouts_seconds": CONNECTION_TIMEOUTS,
            "innodb_lock_wait_timeout_seconds": LOCK_WAIT_TIMEOUT_SECONDS,
            "observed_worker_connection_ids_before_release": observed_connections,
            "threads_all_joined": not any(thread.is_alive() for thread in threads),
            "workers": records, "jobs": outcomes, "not_started_jobs": unstarted,
            "first_service_end_ns": min((attempt["ended_ns"] for outcome in outcomes for attempt in outcome["attempts"]), default=None),
            "last_terminal_ns": max((outcome["terminal_ns"] for outcome in outcomes), default=None),
            "diagnostics": diagnostics, "valid": False,
        }
        try:
            ids = [record["connection_id"] for record in records if "connection_id" in record]
            with connection.cursor() as cursor:
                placeholders = ", ".join("%s" for _ in ids)
                if ids:
                    cursor.execute(
                        f"SELECT ID FROM information_schema.processlist WHERE ID IN ({placeholders}) AND DB = %s",
                        (*ids, connection.settings_dict["NAME"]),
                    )
                    observation["observed_worker_connection_ids_after_close"] = sorted(row[0] for row in cursor.fetchall())
                else:
                    observation["observed_worker_connection_ids_after_close"] = []
            observation["final"] = self._snapshot(fixture)
            self._assert_run(observation, fixture)
        except Exception as exc:
            diagnostics.append({"stage": "oracle", "error_type": type(exc).__name__, "error": repr(exc)})
        else:
            observation["valid"] = True
        prefix = "FLASH_SALE_LOAD_WARMUP " if warmup else "FLASH_SALE_LOAD_RUN "
        print(prefix + json.dumps(observation, default=str, sort_keys=True))
        self.assertIs(observation["valid"], True, diagnostics)
        return observation

    def _snapshot(self, fixture):
        user_ids = [customer["user_id"] for customer in fixture["customers"]]
        orders = Order.objects.filter(user_id__in=user_ids)
        items = OrderItem.objects.filter(order__user_id__in=user_ids)
        snapshots = AddressSnapshot.objects.filter(order__user_id__in=user_ids)
        purchases = FlashSalePurchase.objects.filter(customer_id__in=user_ids)
        reservations = InventoryReservation.objects.filter(order_item__order__user_id__in=user_ids)
        movements = InventoryMovement.objects.filter(inventory_id=fixture["inventory_id"])
        return {
            "fact_counts": {"Order": orders.count(), "OrderItem": items.count(),
                            "AddressSnapshot": snapshots.count(), "FlashSalePurchase": purchases.count(),
                            "InventoryReservation": reservations.count(), "InventoryMovement": movements.count()},
            "orders": list(orders.order_by("pk").values(
                "id", "user_id", "idempotency_key", "creation_scope", "status", "currency",
                "subtotal", "discount_total", "shipping_fee", "total",
            )),
            "items": list(items.order_by("pk").values("id", "order_id", "sku_id", "quantity", "unit_price", "line_total")),
            "address_snapshots": list(snapshots.order_by("pk").values("order_id", "detail")),
            "purchases": list(purchases.order_by("pk").values(
                "customer_id", "order_id", "flash_sale_item_id", "status", "blocking_marker",
            )),
            "reservations": list(reservations.order_by("pk").values(
                "id", "order_item_id", "inventory_id", "quantity", "status",
            )),
            "movements": list(movements.order_by("pk").values(
                "reservation_id", "inventory_id", "movement_type", "available_delta", "reserved_delta",
            )),
            "inventory": Inventory.objects.values("available_stock", "reserved_stock").get(pk=fixture["inventory_id"]),
            "ordinary_sku_price": SKU.objects.values_list("price", flat=True).get(pk=fixture["sku_id"]),
            "carts": list(CartItem.objects.filter(cart__user_id__in=user_ids).order_by("cart__user_id").values(
                "id", "cart_id", "cart__user_id", "sku_id", "quantity",
            )),
            "addresses": list(Address.objects.filter(user_id__in=user_ids).order_by("user_id").values("id", "user_id", "detail")),
        }

    def _assert_run(self, observation, fixture):
        self.assertEqual(observation["diagnostics"], [])
        self.assertIs(observation["threads_all_joined"], True)
        self.assertEqual(observation["observed_worker_connection_ids_after_close"], [])
        self.assertEqual(observation["not_started_jobs"], [])
        self.assertEqual(len(observation["jobs"]), observation["jobs_count"])
        self.assertEqual(len(observation["workers"]), observation["worker_count"])
        worker_ids = {worker["connection_id"] for worker in observation["workers"]}
        self.assertEqual(len(worker_ids), observation["worker_count"])
        self.assertEqual(set(observation["observed_worker_connection_ids_before_release"]), worker_ids)
        for worker in observation["workers"]:
            self.assertNotIn("worker_error", worker, worker)
            self.assertNotIn("close_error", worker, worker)
            self.assertIs(worker["connection_closed"], True)
            self.assertLessEqual(worker["session_open_ns"], worker["setup_finished_ns"])
            self.assertLessEqual(worker["setup_finished_ns"], 0)
            self.assertLessEqual(worker["close_started_ns"], worker["closed_ns"])
        for outcome in observation["jobs"]:
            self.assertIn(outcome["terminal_code"], {"created", "stock_busy", "insufficient_stock"})
            self.assertIn(outcome["connection_id"], worker_ids)
            self.assertLessEqual(0, outcome["started_ns"])
            self.assertLessEqual(outcome["started_ns"], outcome["terminal_ns"])
            self.assertTrue(1 <= len(outcome["attempts"]) <= observation["max_attempts"])
            self.assertEqual(len(outcome["backoffs"]), len(outcome["attempts"]) - 1)
            for index, attempt in enumerate(outcome["attempts"]):
                self.assertEqual(attempt["number"], index + 1)
                self.assertLessEqual(outcome["started_ns"], attempt["started_ns"])
                self.assertLessEqual(attempt["started_ns"], attempt["ended_ns"])
                self.assertLessEqual(attempt["ended_ns"], outcome["terminal_ns"])
                self.assertIn(attempt["code"], {"created", "stock_busy", "insufficient_stock"})
                if attempt["code"] == "stock_busy":
                    self.assertEqual(attempt["mysql_error_number"], 3572)
                    self.assertEqual(attempt["http_status"], 409)
                elif attempt["code"] == "insufficient_stock":
                    self.assertEqual(attempt["http_status"], 409)
                    self.assertIsNone(attempt["mysql_error_number"])
                else:
                    self.assertIs(attempt["created"], True)
                if index < len(outcome["attempts"]) - 1:
                    self.assertEqual(attempt["code"], "stock_busy")
                    backoff = outcome["backoffs"][index]
                    self.assertEqual(backoff["planned_ns"], RETRY_BACKOFF_NS[index])
                    self.assertLessEqual(attempt["ended_ns"], backoff["started_ns"])
                    self.assertGreaterEqual(backoff["ended_ns"] - backoff["started_ns"], backoff["planned_ns"])
                    self.assertLessEqual(backoff["ended_ns"], outcome["attempts"][index + 1]["started_ns"])
        final = observation["final"]
        winners = [outcome for outcome in observation["jobs"] if outcome["terminal_code"] == "created"]
        count = len(winners)
        self.assertLessEqual(count, min(fixture["initial_stock"], observation["jobs_count"]))
        if observation["strategy"] == "conditional" or observation["warmup"]:
            self.assertEqual(count, min(fixture["initial_stock"], observation["jobs_count"]))
            self.assertTrue(all(len(outcome["attempts"]) == 1 for outcome in observation["jobs"]))
            self.assertTrue(all(outcome["terminal_code"] != "stock_busy" for outcome in observation["jobs"]))
        self.assertTrue(all(number == count for number in final["fact_counts"].values()), final["fact_counts"])
        self.assertEqual(final["inventory"], {"available_stock": fixture["initial_stock"] - count, "reserved_stock": count})
        self.assertEqual(final["ordinary_sku_price"], Decimal("129.00"))
        customers = {customer["user_id"]: customer for customer in fixture["customers"]}
        winner_ids = {outcome["user_id"] for outcome in winners}
        self.assertEqual({order["user_id"] for order in final["orders"]}, winner_ids)
        self.assertEqual({purchase["customer_id"] for purchase in final["purchases"]}, winner_ids)
        self.assertEqual(len(winner_ids), count)
        orders = {order["id"]: order for order in final["orders"]}
        winner_orders = {outcome["order_id"]: outcome for outcome in winners}
        self.assertEqual(set(orders), set(winner_orders))
        items = {item["order_id"]: item for item in final["items"]}
        snapshots = {snapshot["order_id"]: snapshot for snapshot in final["address_snapshots"]}
        purchases = {purchase["order_id"]: purchase for purchase in final["purchases"]}
        reservations = {reservation["order_item_id"]: reservation for reservation in final["reservations"]}
        movements = {movement["reservation_id"]: movement for movement in final["movements"]}
        self.assertEqual(set(items), set(orders))
        self.assertEqual(set(snapshots), set(orders))
        self.assertEqual(set(purchases), set(orders))
        self.assertEqual(len(reservations), count)
        self.assertEqual(len(movements), count)
        for order_id, order in orders.items():
            customer, item, purchase = customers[order["user_id"]], items[order_id], purchases[order_id]
            self.assertEqual(order["idempotency_key"], customer["idempotency_key"])
            self.assertEqual(order["user_id"], winner_orders[order_id]["user_id"])
            self.assertEqual((order["creation_scope"], order["status"], order["currency"]),
                             (Order.CreationScope.FLASH_SALE, Order.Status.PENDING_PAYMENT, "CNY"))
            self.assertEqual((order["subtotal"], order["total"], order["discount_total"], order["shipping_fee"]),
                             (Decimal("89.00"), Decimal("89.00"), Decimal("0.00"), Decimal("0.00")))
            self.assertEqual((item["sku_id"], item["quantity"], item["unit_price"], item["line_total"]),
                             (fixture["sku_id"], 1, Decimal("89.00"), Decimal("89.00")))
            self.assertEqual(snapshots[order_id]["detail"], customer["address_detail"])
            self.assertEqual((purchase["customer_id"], purchase["flash_sale_item_id"], purchase["status"], purchase["blocking_marker"]),
                             (order["user_id"], fixture["flash_sale_item_id"], FlashSalePurchase.Status.HELD, 1))
            reservation = reservations[item["id"]]
            self.assertEqual((reservation["inventory_id"], reservation["quantity"], reservation["status"]),
                             (fixture["inventory_id"], 1, InventoryReservation.Status.RESERVED))
            movement = movements[reservation["id"]]
            self.assertEqual((movement["inventory_id"], movement["movement_type"], movement["available_delta"], movement["reserved_delta"]),
                             (fixture["inventory_id"], InventoryMovement.Type.RESERVE, -1, 1))
        self.assertEqual(sum(movement["available_delta"] for movement in final["movements"]), -count)
        self.assertEqual(sum(movement["reserved_delta"] for movement in final["movements"]), count)
        self.assertEqual(final["carts"], [
            {"id": customer["cart_item_id"], "cart_id": customer["cart_id"],
             "cart__user_id": customer["user_id"], "sku_id": fixture["sku_id"], "quantity": customer["cart_quantity"]}
            for customer in sorted(fixture["customers"], key=lambda row: row["user_id"])
        ])
        self.assertEqual(final["addresses"], [
            {"id": customer["address_id"], "user_id": customer["user_id"], "detail": customer["address_detail"]}
            for customer in sorted(fixture["customers"], key=lambda row: row["user_id"])
        ])

    def test_equal_bounded_batches_record_retry_latency_and_connection_holding(self):
        for strategy in ("nowait", "conditional"):
            fixture = self._fixture(name=f"load-warmup-{strategy}", stock=1, jobs=1)
            self._run(fixture=fixture, strategy=strategy, profile="warmup", repeat=0,
                      max_attempts=1, workers=1, warmup=True)
        profiles = [("ample", 24, 1), ("ample", 24, 3), ("scarce", 6, 1), ("scarce", 6, 3)]
        run_number = 0
        for repeat in range(3):
            ordered = profiles[repeat:] + profiles[:repeat]
            for pair_number, (profile, stock, max_attempts) in enumerate(ordered):
                strategies = ("nowait", "conditional") if (repeat + pair_number) % 2 == 0 else ("conditional", "nowait")
                for strategy in strategies:
                    run_number += 1
                    fixture = self._fixture(name=f"load-run-{run_number}-{strategy}", stock=stock, jobs=24)
                    self._run(fixture=fixture, strategy=strategy, profile=profile, repeat=repeat + 1,
                              max_attempts=max_attempts, workers=8)
        self.assertEqual(run_number, 24)
