"""相同完整秒杀事务的库存策略实验；条件更新候选仅存在于本测试。"""

import json
from contextlib import nullcontext
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event, Thread
from time import monotonic
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.db.models import F

from apps.inventory.models import Inventory, InventoryMovement
from apps.ordering.models import Order
from apps.ordering.services import OrderRuleError
from apps.promotions.models import FlashSalePurchase
from apps.promotions.tests.test_flash_sale_contention import FlashSaleContentionFixture


def _conditional_reserve_inventory(sku):
    """不增加正式策略开关：只替换同一个下单事务的库存预占步骤。"""
    if not connection.in_atomic_block:
        raise AssertionError("条件更新实验必须处于完整下单事务内")
    updated = Inventory.objects.filter(sku=sku, available_stock__gte=1).update(
        available_stock=F("available_stock") - 1,
        reserved_stock=F("reserved_stock") + 1,
    )
    if updated != 1:
        raise OrderRuleError("insufficient_stock", "活动商品库存不足。", 409)
    # UPDATE 已持有该行的写锁，后续预占和流水继续使用原服务实现。
    return Inventory.objects.get(sku=sku)


class FlashSaleInventoryComparisonTests(FlashSaleContentionFixture):
    def _strategy(self, strategy):
        if strategy == "nowait":
            return nullcontext()
        self.assertEqual(strategy, "conditional")
        return patch(
            "apps.promotions.services._reserve_flash_sale_inventory",
            new=_conditional_reserve_inventory,
        )

    def _inventory_trace(self, *, strategy, observations, started=None):
        def observe(execute, sql, params, many, context):
            statement = sql.lstrip().upper()
            matching = "inventory_inventory" in sql and (
                (strategy == "nowait" and "FOR UPDATE" in statement)
                or (strategy == "conditional" and statement.startswith("UPDATE "))
            )
            if not matching:
                return execute(sql, params, many, context)
            observation = {"sql": sql, "params": params, "nowait": "NOWAIT" in statement}
            observations.append(observation)
            if started is not None:
                started.set()
            try:
                result = execute(sql, params, many, context)
            except DatabaseError as exc:
                observation["mysql_error_number"] = exc.args[0] if exc.args else None
                raise
            if strategy == "conditional":
                observation["affected_rows"] = context["cursor"].rowcount
            observation["completed"] = True
            return result
        return observe

    def _server_inventory_update(self, *, contender_id, done, release):
        """只读同应用账号的本场景连接；不把计时或 processlist 状态当锁等待图。"""
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT ID, COMMAND, STATE, INFO FROM information_schema.processlist "
                    "WHERE ID = %s AND DB = %s",
                    (contender_id, connection.settings_dict["NAME"]),
                )
                row = cursor.fetchone()
            if row is not None and row[1] == "Query" and row[3] is not None:
                sql = row[3]
                if sql.lstrip().upper().startswith("UPDATE ") and "inventory_inventory" in sql:
                    self.assertFalse(release.is_set())
                    self.assertFalse(done.is_set())
                    return {
                        "connection_id": row[0], "command": row[1], "state": row[2],
                        "sql": sql, "observed_before_release": True,
                        "contender_done_at_observation": False,
                    }
            if done.wait(timeout=0.01):
                break
        self.fail("未在首单持锁窗口内观测到第二连接的真实库存 UPDATE；不能仅凭未返回推断")

    def _emit_comparison(self, scenario, **observations):
        print("FLASH_SALE_COMPARISON " + json.dumps(
            {"scenario": scenario, **observations}, default=str, sort_keys=True
        ))

    def _compare_held_checkout(self, *, strategy, rollback):
        observer_id = self._connection_id()
        ready, release, query_started, done = Event(), Event(), Event(), Event()
        holder_results, contender_results = Queue(), Queue()
        identities, inventory_queries = {}, []

        class InjectedRollback(Exception):
            pass

        def holder():
            close_old_connections()
            outcome = {"user_id": self.first["user_id"], "key": self.first["key"]}
            try:
                outcome["connection_id"] = self._connection_id()
                identities["holder"] = outcome["connection_id"]
                with transaction.atomic():
                    order, created = self._create(self.first)
                    outcome.update(order_id=order.pk, created=created)
                    self.assertIs(created, True)
                    self._assert_complete_order(self.first, self.second)
                    outcome["uncommitted_snapshot"] = self._snapshot()
                    ready.set()
                    if not release.wait(timeout=30):
                        raise TimeoutError("完整首单事务未收到放行信号")
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

        def contender():
            close_old_connections()
            outcome = {"user_id": self.second["user_id"], "key": self.second["key"]}
            try:
                outcome["connection_id"] = self._connection_id()
                identities["contender"] = outcome["connection_id"]
                trace = self._inventory_trace(
                    strategy=strategy, observations=inventory_queries, started=query_started
                )
                with connection.execute_wrapper(trace):
                    # 冻结的业务异常须在观察器内、服务 atomic 已退出之后捕获。
                    try:
                        order, created = self._create(self.second)
                    except OrderRuleError as exc:
                        outcome.update(error_code=exc.code, http_status=exc.http_status)
                    else:
                        outcome.update(order_id=order.pk, created=created)
                outcome["returned_before_release"] = not release.is_set()
                outcome["snapshot_after_return"] = self._snapshot()
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                contender_results.put(outcome)
                connections.close_all()
                done.set()

        first_thread, second_thread = Thread(target=holder, daemon=True), Thread(target=contender, daemon=True)
        before_release, server_update = None, None
        with self._strategy(strategy):
            first_thread.start()
            try:
                self.assertTrue(ready.wait(timeout=10), "首单未进入完整下单后的未提交窗口")
                second_thread.start()
                self.assertTrue(query_started.wait(timeout=10), "第二个顾客尚未发起库存 SQL")
                if strategy == "nowait":
                    self.assertTrue(done.wait(timeout=10), "NOWAIT 未在首单放行前返回")
                else:
                    server_update = self._server_inventory_update(
                        contender_id=identities["contender"], done=done, release=release
                    )
                    self.assertNotIn("completed", inventory_queries[0])
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
            self.assertEqual((holder_results.qsize(), contender_results.qsize()), (1, 1))
            first_outcome, second_outcome = holder_results.get_nowait(), contender_results.get_nowait()
            self.assertNotIn("unexpected_error", first_outcome, first_outcome)
            self.assertNotIn("unexpected_error", second_outcome, second_outcome)
            self.assertEqual(len({observer_id, first_outcome["connection_id"], second_outcome["connection_id"]}), 3)
            self.assertEqual(first_outcome["transaction_result"], "rolled_back" if rollback else "committed")
            self.assertEqual(len(inventory_queries), 1, inventory_queries)
            if strategy == "nowait":
                self.assertEqual((second_outcome["error_code"], second_outcome["http_status"]), ("stock_busy", 409))
                self.assertIs(second_outcome["returned_before_release"], True)
                self.assertIs(inventory_queries[0]["nowait"], True)
                self.assertEqual(inventory_queries[0]["mysql_error_number"], 3572)
                self.assertNotIn("completed", inventory_queries[0])
            else:
                self.assertIs(second_outcome["returned_before_release"], False)
                self.assertIs(inventory_queries[0]["nowait"], False)
                self.assertIs(inventory_queries[0]["completed"], True)
                self.assertEqual(inventory_queries[0]["affected_rows"], 1 if rollback else 0)
                if rollback:
                    self.assertIs(second_outcome["created"], True)
                else:
                    self.assertEqual((second_outcome["error_code"], second_outcome["http_status"]), ("insufficient_stock", 409))
            after_release = self._snapshot()
            if rollback and strategy == "nowait":
                self._assert_empty()
                order, created = self._create(self.second)
                self.assertIs(created, True)
                retry = {"order_id": order.pk, "created": created}
            elif rollback:
                order = Order.objects.get(pk=second_outcome["order_id"])
                retry = {"needed_to_create_order": False, "first_waiting_request_created": True}
            else:
                order = Order.objects.get(pk=first_outcome["order_id"])
                with self.assertRaises(OrderRuleError) as raised:
                    self._create(self.second)
                self.assertEqual((raised.exception.code, raised.exception.http_status), ("insufficient_stock", 409))
                retry = {"error_code": raised.exception.code, "http_status": raised.exception.http_status}
            winner, loser = (self.second, self.first) if rollback else (self.first, self.second)
            self.assertEqual(self._assert_complete_order(winner, loser).pk, order.pk)
            replay, created = self._create(winner)
            self.assertIs(created, False)
            self.assertEqual(replay.pk, order.pk)
            self._assert_complete_order(winner, loser)
            final = self._snapshot()
        self._emit_comparison(
            strategy + ("_holder_rollback" if rollback else "_holder_commit"),
            strategy=strategy, observer_connection_id=observer_id,
            holder=first_outcome, contender=second_outcome, inventory_queries=inventory_queries,
            server_update=server_update, committed_view_before_release=before_release,
            after_release=after_release, retry=retry,
            winner_replay={"order_id": replay.pk, "created": created}, final=final,
        )

    def test_nowait_busy_before_holder_commit_then_original_key_sold_out(self):
        self._compare_held_checkout(strategy="nowait", rollback=False)

    def test_nowait_busy_before_holder_rollback_then_original_key_creates(self):
        self._compare_held_checkout(strategy="nowait", rollback=True)

    def test_conditional_inflight_before_holder_commit_then_zero_rows_sold_out(self):
        self._compare_held_checkout(strategy="conditional", rollback=False)

    def test_conditional_inflight_before_holder_rollback_then_one_row_creates(self):
        self._compare_held_checkout(strategy="conditional", rollback=True)

    def test_conditional_same_customer_different_keys_still_limits_purchase(self):
        self.inventory.available_stock = 5
        self.inventory.save(update_fields=("available_stock",))
        observer_id = self._connection_id()
        start, results = Barrier(2), Queue()
        candidates = [dict(self.first, key="conditional-limit-a"), dict(self.first, key="conditional-limit-b")]

        def checkout(customer):
            close_old_connections()
            outcome = {"key": customer["key"], "inventory_queries": []}
            try:
                outcome["connection_id"] = self._connection_id()
                start.wait(timeout=10)
                trace = self._inventory_trace(strategy="conditional", observations=outcome["inventory_queries"])
                with connection.execute_wrapper(trace):
                    try:
                        order, created = self._create(customer)
                    except OrderRuleError as exc:
                        outcome.update(error_code=exc.code, http_status=exc.http_status)
                    else:
                        outcome.update(order_id=order.pk, created=created)
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                results.put(outcome)
                connections.close_all()

        threads = [Thread(target=checkout, args=(customer,), daemon=True) for customer in candidates]
        with self._strategy("conditional"):
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
            self.assertEqual(len({observer_id, *(outcome["connection_id"] for outcome in outcomes)}), 3)
            winners = [outcome for outcome in outcomes if "created" in outcome]
            losers = [outcome for outcome in outcomes if "error_code" in outcome]
            self.assertEqual((len(winners), len(losers)), (1, 1), outcomes)
            self.assertIs(winners[0]["created"], True)
            self.assertEqual((losers[0]["error_code"], losers[0]["http_status"]), ("flash_sale_limit_reached", 409))
            self.assertEqual(len(winners[0]["inventory_queries"]), 1)
            self.assertEqual(winners[0]["inventory_queries"][0]["affected_rows"], 1)
            self.assertEqual(losers[0]["inventory_queries"], [])
            self._assert_fact_counts(1)
            self.assertTrue(all(count == 1 for count in self._owned_fact_counts(self.user.pk).values()))
            self._assert_no_customer_facts(self.second)
            order = Order.objects.get()
            self.assertEqual((order.pk, order.user_id, order.idempotency_key), (winners[0]["order_id"], self.user.pk, winners[0]["key"]))
            self.assertEqual((order.status, order.total), (Order.Status.PENDING_PAYMENT, Decimal("89.00")))
            purchase = FlashSalePurchase.objects.get()
            self.assertEqual((purchase.order_id, purchase.status, purchase.blocking_marker), (order.pk, FlashSalePurchase.Status.HELD, 1))
            self.assertFalse(Order.objects.filter(user=self.user, idempotency_key=losers[0]["key"]).exists())
            self.inventory.refresh_from_db()
            self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (4, 1))
            self.sku.refresh_from_db()
            self.assertEqual(self.sku.price, Decimal("129.00"))
            self._assert_carts_unchanged()
            winner = next(customer for customer in candidates if customer["key"] == winners[0]["key"])
            replay, created = self._create(winner)
            self.assertEqual(replay.pk, order.pk)
            self.assertIs(created, False)
            self._assert_fact_counts(1)
            final = self._snapshot()
        self._emit_comparison("conditional_same_customer_stock_five", strategy="conditional",
                              observer_connection_id=observer_id, outcomes=outcomes,
                              winner_replay={"order_id": replay.pk, "created": created}, final=final)

    def test_conditional_failure_after_real_movement_write_rolls_back_and_retries(self):
        class InjectedCheckoutFailure(Exception):
            pass

        uncommitted = []
        inventory_queries = []
        real_create_movement = InventoryMovement.objects.create

        def fail_after_write(**fields):
            real_create_movement(**fields)
            self._assert_complete_order(self.first, self.second)
            uncommitted.append(self._snapshot())
            raise InjectedCheckoutFailure("TEST-ONLY failure after real inventory movement write")

        with self._strategy("conditional"):
            trace = self._inventory_trace(strategy="conditional", observations=inventory_queries)
            with connection.execute_wrapper(trace):
                with patch.object(InventoryMovement.objects, "create", side_effect=fail_after_write):
                    with self.assertRaises(InjectedCheckoutFailure):
                        self._create(self.first)
                self._assert_empty()
                rolled_back = self._snapshot()
                order, created = self._create(self.first)
                self.assertIs(created, True)
                self.assertEqual(self._assert_complete_order(self.first, self.second).pk, order.pk)
                replay, replay_created = self._create(self.first)
                self.assertIs(replay_created, False)
                self.assertEqual(replay.pk, order.pk)
                self._assert_complete_order(self.first, self.second)
                final = self._snapshot()
        self.assertEqual(len(uncommitted), 1)
        self.assertEqual([query["affected_rows"] for query in inventory_queries], [1, 1])
        self._emit_comparison("conditional_failure_after_movement_write", strategy="conditional",
                              uncommitted_snapshot=uncommitted[0], rolled_back=rolled_back,
                              inventory_queries=inventory_queries,
                              retry={"order_id": order.pk, "created": created},
                              winner_replay={"order_id": replay.pk, "created": replay_created}, final=final)
