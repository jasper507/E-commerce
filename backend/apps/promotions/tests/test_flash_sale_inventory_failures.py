"""正式条件 UPDATE 的失败合约；真实 1205 与注入异常证据分开记录。"""

import json
from queue import Queue
from threading import Event, Thread
from time import monotonic

from django.db import (
    DatabaseError,
    OperationalError,
    close_old_connections,
    connection,
    connections,
    transaction,
)

from apps.inventory.models import Inventory
from apps.ordering.services import OrderRuleError
from apps.promotions.tests.test_flash_sale_contention import FlashSaleContentionFixture


TEST_CONNECTION_TIMEOUTS = {
    "connect_timeout": 5,
    "read_timeout": 10,
    "write_timeout": 10,
}
PENDING_FACTS = {
    "Order": 1,
    "OrderItem": 1,
    "AddressSnapshot": 1,
    "FlashSalePurchase": 1,
    "InventoryReservation": 0,
    "InventoryMovement": 0,
}


class FlashSaleInventoryFailureTests(FlashSaleContentionFixture):
    def _emit_failure(self, scenario, **observations):
        print("FLASH_SALE_INVENTORY_FAILURE " + json.dumps(
            {"scenario": scenario, **observations}, default=str, sort_keys=True
        ))

    def _session_identity(self, *, lock_wait_timeout=None):
        with connection.cursor() as cursor:
            if lock_wait_timeout is not None:
                cursor.execute(
                    "SET SESSION innodb_lock_wait_timeout = %s", (lock_wait_timeout,)
                )
            cursor.execute("SELECT CONNECTION_ID(), @@SESSION.innodb_lock_wait_timeout")
            connection_id, wait_seconds = cursor.fetchone()
        return {"connection_id": connection_id, "lock_wait_timeout_seconds": wait_seconds}

    def _experimental_thread_connection(self):
        close_old_connections()
        db = connections["default"]
        # 线程 wrapper 单独复制配置，实验退出有界，不修改全局 OPTIONS。
        db.settings_dict = dict(db.settings_dict)
        db.settings_dict["OPTIONS"] = dict(db.settings_dict.get("OPTIONS", {}))
        db.settings_dict["OPTIONS"].update(TEST_CONNECTION_TIMEOUTS)
        return db

    def _inventory_update(self, sql):
        return sql.lstrip().upper().startswith("UPDATE ") and "inventory_inventory" in sql

    def _observe_waiting_update(self, *, contender_id, done, release):
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
                if self._inventory_update(row[3]):
                    self.assertFalse(done.is_set())
                    self.assertFalse(release.is_set())
                    return {
                        "connection_id": row[0],
                        "command": row[1],
                        "state": row[2],
                        "sql": row[3],
                        "observed_before_release": True,
                        "contender_done_at_observation": False,
                    }
            if done.wait(timeout=0.01):
                break
        self.fail("未观测到持锁期间第二连接实际执行的库存 UPDATE，不能仅凭耗时推断")

    def test_real_inventory_lock_timeout_rolls_back_full_order_then_original_key_retries(self):
        observer_session = self._session_identity()
        observer_options = dict(connection.settings_dict["OPTIONS"])
        ready, release, update_started, done = Event(), Event(), Event(), Event()
        holder_results, contender_results = Queue(), Queue()
        identities, inventory_queries = {}, []

        def holder():
            outcome = {"role": "inventory_only_holder", "business_writes": False}
            try:
                self._experimental_thread_connection()
                outcome.update(self._session_identity())
                identities["holder"] = outcome["connection_id"]
                with transaction.atomic():
                    inventory = Inventory.objects.select_for_update().get(pk=self.inventory.pk)
                    outcome["locked_inventory"] = {
                        "available_stock": inventory.available_stock,
                        "reserved_stock": inventory.reserved_stock,
                    }
                    ready.set()
                    if not release.wait(timeout=30):
                        raise TimeoutError("库存持锁连接未收到放行信号")
                outcome["transaction_result"] = "committed_without_inventory_change"
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                holder_results.put(outcome)
                connections.close_all()

        def observe_update(execute, sql, params, many, context):
            if not self._inventory_update(sql):
                return execute(sql, params, many, context)
            observation = {
                "sql": sql,
                "params": params,
                "actual_sql_attempted": True,
                "in_atomic_block_before_update": connection.in_atomic_block,
                "owned_facts_before_update": self._owned_fact_counts(self.second["user_id"]),
            }
            inventory_queries.append(observation)
            self.assertTrue(observation["in_atomic_block_before_update"])
            self.assertEqual(observation["owned_facts_before_update"], PENDING_FACTS)
            update_started.set()
            started = monotonic()
            try:
                result = execute(sql, params, many, context)
            except DatabaseError as exc:
                observation.update(
                    mysql_error_number=exc.args[0] if exc.args else None,
                    database_error_type=type(exc).__name__,
                    elapsed_seconds=monotonic() - started,
                )
                raise
            else:
                observation["affected_rows"] = context["cursor"].rowcount
                return result

        def contender():
            outcome = {"user_id": self.second["user_id"], "key": self.second["key"]}
            try:
                self._experimental_thread_connection()
                outcome.update(self._session_identity(lock_wait_timeout=1))
                identities["contender"] = outcome["connection_id"]
                with connection.execute_wrapper(observe_update):
                    # 服务 atomic 已退出；冻结业务异常在生成器观察器内部捕获。
                    try:
                        order, created = self._create(self.second)
                    except OrderRuleError as exc:
                        cause = exc.__cause__
                        outcome.update(
                            error_code=exc.code,
                            detail=exc.detail,
                            http_status=exc.http_status,
                            cause_type=type(cause).__name__,
                            cause_mysql_error_number=(
                                cause.args[0] if cause is not None and cause.args else None
                            ),
                        )
                    else:
                        outcome.update(order_id=order.pk, created=created)
                outcome.update(
                    in_atomic_block_after_return=connection.in_atomic_block,
                    needs_rollback_after_return=connection.needs_rollback,
                    autocommit_after_return=connection.get_autocommit(),
                    returned_before_release=not release.is_set(),
                    snapshot_after_return=self._snapshot(),
                )
                self._assert_no_customer_facts(self.second)
            except Exception as exc:
                outcome["unexpected_error"] = repr(exc)
            finally:
                contender_results.put(outcome)
                connections.close_all()
                done.set()

        first_thread = Thread(target=holder, daemon=True)
        second_thread = Thread(target=contender, daemon=True)
        server_update, before_release = None, None
        first_thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10), "持锁连接未取得库存写锁")
            second_thread.start()
            self.assertTrue(update_started.wait(timeout=10), "完整下单未到达库存 UPDATE")
            server_update = self._observe_waiting_update(
                contender_id=identities["contender"], done=done, release=release
            )
            self.assertTrue(done.wait(timeout=10), "下单连接未在测试会话等待预算内退出")
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
        holder_outcome, contender_outcome = holder_results.get_nowait(), contender_results.get_nowait()
        self.assertNotIn("unexpected_error", holder_outcome, holder_outcome)
        self.assertNotIn("unexpected_error", contender_outcome, contender_outcome)
        self.assertEqual(len({
            observer_session["connection_id"],
            holder_outcome["connection_id"],
            contender_outcome["connection_id"],
        }), 3)
        self.assertEqual(holder_outcome["transaction_result"], "committed_without_inventory_change")
        self.assertEqual(holder_outcome["locked_inventory"], {"available_stock": 1, "reserved_stock": 0})
        self.assertEqual(contender_outcome["lock_wait_timeout_seconds"], 1)
        self.assertEqual(
            (contender_outcome["error_code"], contender_outcome["http_status"]),
            ("stock_busy", 409),
        )
        self.assertEqual(contender_outcome["detail"], "库存正在更新，请重试原请求。")
        self.assertEqual(contender_outcome["cause_mysql_error_number"], 1205)
        self.assertIs(contender_outcome["returned_before_release"], True)
        self.assertIs(contender_outcome["in_atomic_block_after_return"], False)
        self.assertIs(contender_outcome["needs_rollback_after_return"], False)
        self.assertIs(contender_outcome["autocommit_after_return"], True)
        self.assertEqual(len(inventory_queries), 1, inventory_queries)
        self.assertEqual(inventory_queries[0]["mysql_error_number"], 1205)
        self.assertNotIn("affected_rows", inventory_queries[0])
        self.assertTrue(all(
            count == 0 for count in contender_outcome["snapshot_after_return"]["fact_counts"].values()
        ))
        self.assertEqual(
            contender_outcome["snapshot_after_return"]["inventory"],
            {"available_stock": 1, "reserved_stock": 0},
        )
        self._assert_empty()
        order, created = self._create(self.second)
        self.assertIs(created, True)
        self.assertEqual(self._assert_complete_order(self.second, self.first).pk, order.pk)
        replay, replay_created = self._create(self.second)
        self.assertIs(replay_created, False)
        self.assertEqual(replay.pk, order.pk)
        self._assert_complete_order(self.second, self.first)
        observer_after = self._session_identity()
        self.assertEqual(observer_after, observer_session)
        self.assertEqual(connection.settings_dict["OPTIONS"], observer_options)
        self._emit_failure(
            "real_inventory_1205_full_rollback_and_original_key_retry",
            evidence_kind="real_isolated_mysql_lock_timeout",
            observer_session=observer_session,
            observer_session_after=observer_after,
            holder=holder_outcome,
            contender=contender_outcome,
            inventory_queries=inventory_queries,
            server_update=server_update,
            committed_view_before_release=before_release,
            retry={"order_id": order.pk, "created": created},
            replay={"order_id": replay.pk, "created": replay_created},
            final=self._snapshot(),
        )

    def _injected_inventory_error(self, *, error_number, mapped):
        injected = OperationalError(error_number, "TEST-ONLY injected inventory UPDATE failure")
        inventory_queries = []
        observed_error = None

        def inject_before_update(execute, sql, params, many, context):
            if not self._inventory_update(sql):
                return execute(sql, params, many, context)
            observation = {
                "sql": sql,
                "params": params,
                "actual_sql_attempted": False,
                "injected_mysql_error_number": error_number,
                "in_atomic_block_before_update": connection.in_atomic_block,
                "owned_facts_before_update": self._owned_fact_counts(self.second["user_id"]),
            }
            inventory_queries.append(observation)
            self.assertTrue(observation["in_atomic_block_before_update"])
            self.assertEqual(observation["owned_facts_before_update"], PENDING_FACTS)
            raise injected

        with connection.execute_wrapper(inject_before_update):
            try:
                self._create(self.second)
            except Exception as exc:
                observed_error = exc
        self.assertEqual(len(inventory_queries), 1, inventory_queries)
        self.assertIsNotNone(observed_error)
        if mapped:
            self.assertIsInstance(observed_error, OrderRuleError)
            self.assertEqual((observed_error.code, observed_error.http_status), ("stock_busy", 409))
            self.assertEqual(observed_error.detail, "库存正在更新，请重试原请求。")
            self.assertIs(observed_error.__cause__, injected)
        else:
            self.assertIs(observed_error, injected)
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assertTrue(connection.get_autocommit())
        self._assert_empty()
        after_failure = self._snapshot()
        order, created = self._create(self.second)
        self.assertIs(created, True)
        self.assertEqual(self._assert_complete_order(self.second, self.first).pk, order.pk)
        replay, replay_created = self._create(self.second)
        self.assertIs(replay_created, False)
        self.assertEqual(replay.pk, order.pk)
        self._assert_complete_order(self.second, self.first)
        self._emit_failure(
            "injected_inventory_error_" + str(error_number),
            evidence_kind="injected_error_conversion_only",
            real_deadlock_or_disconnect_verified=False,
            injected_mysql_error_number=error_number,
            exception_type=type(observed_error).__name__,
            mapped_business_error=observed_error.code if mapped else None,
            preserves_injected_exception=(
                observed_error.__cause__ is injected if mapped else observed_error is injected
            ),
            inventory_queries=inventory_queries,
            after_failure=after_failure,
            retry={"order_id": order.pk, "created": created},
            replay={"order_id": replay.pk, "created": replay_created},
            final=self._snapshot(),
        )

    def test_injected_inventory_deadlock_maps_busy_and_rolls_back(self):
        self._injected_inventory_error(error_number=1213, mapped=True)

    def test_injected_inventory_connection_error_is_not_masked_as_stock_busy(self):
        self._injected_inventory_error(error_number=2006, mapped=False)
