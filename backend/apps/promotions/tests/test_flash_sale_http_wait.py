"""HTTP 会话等待、整单失败与连接复用；真实超时和注入故障分别记录。"""

import json
from queue import Queue
from threading import Event, Thread
from time import monotonic
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, OperationalError, connection, connections, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import AddressSnapshot, Order, OrderItem
from apps.promotions import http_services
from apps.promotions.models import FlashSalePurchase
from apps.promotions.tests.test_flash_sale_checkout import FlashSaleCheckoutFixture


FACT_MODELS = (Order, OrderItem, AddressSnapshot, FlashSalePurchase, InventoryReservation, InventoryMovement)
PENDING_FACTS = dict(zip((model.__name__ for model in FACT_MODELS), (1, 1, 1, 1, 0, 0)))


class FlashSaleHTTPWaitTests(FlashSaleCheckoutFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:csrf")).status_code, 204)
        self.csrf = self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def _post(self, key="http-wait-original-key"):
        return self.client.post(
            reverse("promotions:flash-sale-orders"),
            {"flash_sale_item_id": self.flash_sale_item.pk, "address_id": self.address.pk},
            format="json",
            HTTP_X_CSRFTOKEN=self.csrf,
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def _direct(self, key="http-wait-direct"):
        return http_services.create_flash_sale_order_for_http(
            user=self.user,
            flash_sale_item_id=self.flash_sale_item.pk,
            address_id=self.address.pk,
            idempotency_key=key,
        )

    def _session(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT CONNECTION_ID(), @@SESSION.innodb_lock_wait_timeout")
            cid, wait = cursor.fetchone()
        return {"connection_id": cid, "wait_seconds": wait}

    def _set_wait(self, seconds):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = %s", (seconds,))

    def _facts(self):
        return {model.__name__: model.objects.count() for model in FACT_MODELS}

    def _snapshot(self):
        return {
            "facts": self._facts(),
            "inventory": Inventory.objects.values("available_stock", "reserved_stock").get(pk=self.inventory.pk),
            "orders": list(Order.objects.values("id", "idempotency_key", "status", "total")),
        }

    def _emit(self, scenario, **evidence):
        print("FLASH_SALE_HTTP_WAIT " + json.dumps({"scenario": scenario, **evidence}, default=str, sort_keys=True))

    @staticmethod
    def _inventory_update(sql):
        return sql.lstrip().upper().startswith("UPDATE ") and "`inventory_inventory`" in sql

    @staticmethod
    def _user_lock(sql):
        return "`accounts_user`" in sql and "FOR UPDATE" in sql.upper()

    def test_success_replay_and_business_refusal_restore_the_actual_original_value(self):
        ambient = self._session()
        self._set_wait(17)
        before = self._session()
        settings, inventory_steps = [], []

        def observe(execute, sql, params, many, context):
            if sql.startswith("SET SESSION innodb_lock_wait_timeout"):
                settings.append({"value": params[0], "in_atomic": connection.in_atomic_block})
            if self._inventory_update(sql):
                inventory_steps.append({"session": self._session(), "in_atomic": connection.in_atomic_block})
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(observe):
                created = self._post()
                replay = self._post()
                refusal = self._post("another-intent")
            after = self._session()
            self.assertEqual([created.status_code, replay.status_code, refusal.status_code], [201, 200, 409])
            self.assertEqual(refusal.json()["code"], "flash_sale_limit_reached")
            self.assertEqual(created.json()["public_id"], replay.json()["public_id"])
            self.assertEqual(after, before)
            self.assertEqual([row["value"] for row in settings], [5, 17, 5, 17, 5, 17])
            self.assertTrue(all(not row["in_atomic"] for row in settings))
            self.assertEqual(len(inventory_steps), 1)
            self.assertEqual(inventory_steps[0]["session"], {**before, "wait_seconds": 5})
            self.assertTrue(inventory_steps[0]["in_atomic"])
            self._assert_complete_flash_sale_order(key="http-wait-original-key")
            self._emit("success_replay_refusal_restore", before=before, after=after,
                       settings=settings, inventory_steps=inventory_steps, final=self._snapshot())
        finally:
            self._set_wait(ambient["wait_seconds"])

    def test_injected_inventory_other_sql_and_unknown_errors_restore_and_roll_back(self):
        ambient = self._session()
        try:
            for stage, number, expected_code in (
                ("inventory", 1205, "stock_busy"),
                ("movement_written", 1213, "checkout_busy"),
                ("user", 2006, None),
            ):
                with self.subTest(stage=stage):
                    self._set_wait(13)
                    before = self._session()
                    error = OperationalError(number, "TEST-ONLY injected SQL failure")
                    observations = []

                    def inject(execute, sql, params, many, context):
                        matches = (
                            stage == "inventory" and self._inventory_update(sql)
                            or stage == "user" and self._user_lock(sql)
                            or stage == "movement_written" and sql.startswith("INSERT INTO `inventory_inventorymovement`")
                        )
                        if not matches:
                            return execute(sql, params, many, context)
                        if stage == "movement_written":
                            execute(sql, params, many, context)
                        observations.append({"session": self._session(), "facts": self._facts(),
                                             "actual_sql_executed": stage == "movement_written"})
                        raise error

                    caught, response = None, None
                    with connection.execute_wrapper(inject):
                        try:
                            response = self._post("injected-" + stage)
                        except OperationalError as exc:
                            caught = exc
                    self.assertEqual(len(observations), 1)
                    self.assertEqual(observations[0]["session"], {**before, "wait_seconds": 5})
                    expected_facts = (
                        PENDING_FACTS if stage == "inventory"
                        else {name: int(stage == "movement_written") for name in PENDING_FACTS}
                    )
                    self.assertEqual(observations[0]["facts"], expected_facts)
                    if expected_code:
                        self.assertIsNone(caught)
                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(response.json()["code"], expected_code)
                    else:
                        self.assertIs(caught, error)
                    self.assertEqual(self._session(), before)
                    self.assertFalse(connection.in_atomic_block)
                    self.assertFalse(connection.needs_rollback)
                    self._assert_no_checkout_effects(available_stock=5)
                    self._emit("injected_" + stage, evidence_kind="injected_error_branch_only",
                               number=number, code=expected_code, before=before, after=self._session(),
                               observations=observations, final=self._snapshot())
        finally:
            self._set_wait(ambient["wait_seconds"])

    def test_read_or_applied_setup_failure_discards_connection_before_any_order(self):
        for stage in ("read", "applied_set"):
            with self.subTest(stage=stage):
                before = self._session()
                original = connection.connection
                error = OperationalError(2006, "TEST-ONLY setup acknowledgement failure")

                def fail(execute, sql, params, many, context):
                    if stage == "read" and sql == "SELECT @@SESSION.innodb_lock_wait_timeout":
                        raise error
                    if stage == "applied_set" and sql.startswith("SET SESSION innodb_lock_wait_timeout"):
                        execute(sql, params, many, context)
                        raise error
                    return execute(sql, params, many, context)

                caught = None
                with connection.execute_wrapper(fail):
                    try:
                        self._post("setup-" + stage)
                    except OperationalError as exc:
                        caught = exc
                self.assertIs(caught, error)
                self.assertIsNone(connection.connection)
                after = self._session()
                self.assertIsNot(connection.connection, original)
                self.assertNotEqual(after["connection_id"], before["connection_id"])
                self.assertEqual(after["wait_seconds"], before["wait_seconds"])
                self._assert_no_checkout_effects(available_stock=5)
                self._emit("setup_failure_" + stage, evidence_kind="injected_error_branch_only",
                           before=before, reconnected=after, final=self._snapshot())

    def test_restore_failure_keeps_committed_success_even_if_physical_close_raises(self):
        before = self._session()
        db = connections["default"]
        physical = db.connection
        close_called = []

        def fail_restore(execute, sql, params, many, context):
            if sql.startswith("SET SESSION innodb_lock_wait_timeout") and params[0] == before["wait_seconds"]:
                self.assertFalse(connection.in_atomic_block)
                self.assertEqual(self._facts(), {name: 1 for name in PENDING_FACTS})
                raise OperationalError(2006, "TEST-ONLY restore failure")
            return execute(sql, params, many, context)

        def close_then_fail():
            close_called.append(True)
            physical.close()
            raise OperationalError(2013, "TEST-ONLY close acknowledgement failure")

        with self.assertLogs(http_services.logger, level="WARNING") as logs:
            with connection.execute_wrapper(fail_restore), patch.object(db, "_close", new=close_then_fail):
                response = self._post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(close_called, [True])
        self.assertIsNone(connection.connection)
        self.assertEqual(len(logs.records), 2)
        self.assertTrue(all("TEST-ONLY" not in row.getMessage() for row in logs.records))
        after = self._session()
        self.assertNotEqual(after["connection_id"], before["connection_id"])
        self.assertEqual(after["wait_seconds"], before["wait_seconds"])
        self._assert_complete_flash_sale_order(key="http-wait-original-key")
        replay = self._post()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["public_id"], response.json()["public_id"])
        self._emit("restore_and_close_failure_keeps_success", evidence_kind="injected_cleanup_failure",
                   before=before, reconnected=after, response_status=response.status_code,
                   replay_status=replay.status_code, final=self._snapshot())

    def test_restore_failure_preserves_business_refusal_and_original_unknown_error(self):
        for kind in ("business", "unknown"):
            with self.subTest(kind=kind):
                before = self._session()
                Inventory.objects.filter(pk=self.inventory.pk).update(available_stock=0 if kind == "business" else 5)
                primary = OperationalError(2006, "TEST-ONLY primary database failure")

                def fail(execute, sql, params, many, context):
                    if kind == "unknown" and self._user_lock(sql):
                        raise primary
                    if sql.startswith("SET SESSION innodb_lock_wait_timeout") and params[0] == before["wait_seconds"]:
                        raise OperationalError(2013, "TEST-ONLY cleanup database failure")
                    return execute(sql, params, many, context)

                caught, response = None, None
                with self.assertLogs(http_services.logger, level="WARNING"):
                    with connection.execute_wrapper(fail):
                        try:
                            response = self._post("cleanup-" + kind)
                        except OperationalError as exc:
                            caught = exc
                self.assertIsNone(connection.connection)
                if kind == "business":
                    self.assertIsNone(caught)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()["code"], "insufficient_stock")
                else:
                    self.assertIs(caught, primary)
                after = self._session()
                self.assertNotEqual(after["connection_id"], before["connection_id"])
                self.assertEqual(after["wait_seconds"], before["wait_seconds"])
                self._assert_no_checkout_effects(available_stock=0 if kind == "business" else 5)
                self._emit("restore_failure_keeps_" + kind, evidence_kind="injected_cleanup_failure",
                           before=before, reconnected=after, final=self._snapshot())

    def test_replaced_connection_does_not_receive_the_old_session_value(self):
        ambient = self._session()
        self._set_wait(17)
        before = self._session()
        original_service = http_services.create_flash_sale_order

        def replace_after_commit(**kwargs):
            result = original_service(**kwargs)
            self.assertFalse(connection.in_atomic_block)
            connection.close()
            self._set_wait(19)
            return result

        try:
            with patch.object(http_services, "create_flash_sale_order", new=replace_after_commit):
                response = self._post()
            after = self._session()
            self.assertEqual(response.status_code, 201)
            self.assertNotEqual(after["connection_id"], before["connection_id"])
            self.assertEqual(after["wait_seconds"], 19)
            self._assert_complete_flash_sale_order(key="http-wait-original-key")
            self._emit("replaced_connection_keeps_its_value", evidence_kind="injected_connection_replacement",
                       before=before, after=after, final=self._snapshot())
        finally:
            self._set_wait(ambient["wait_seconds"])

    def test_wait_wrapper_requires_its_own_outer_transaction_boundary(self):
        before = self._session()
        with transaction.atomic():
            with self.assertRaises(RuntimeError):
                self._direct()
        connection.set_autocommit(False)
        try:
            with self.assertRaises(RuntimeError):
                self._direct()
        finally:
            connection.rollback()
            connection.set_autocommit(True)
        self.assertEqual(self._session(), before)
        self._assert_no_checkout_effects(available_stock=5)

    def _real_http_timeout(self, *, stage, expected_code):
        ready, release, started, done = Event(), Event(), Event(), Event()
        holder_results, contender_results = Queue(), Queue()
        queries, identities = [], {}
        observer = self._session()

        def prepare_connection():
            db = connections["default"]
            db.settings_dict = dict(db.settings_dict)
            db.settings_dict["OPTIONS"] = dict(db.settings_dict.get("OPTIONS", {}))
            db.settings_dict["OPTIONS"].update(connect_timeout=5, read_timeout=10, write_timeout=10)

        def holder():
            outcome = {"business_writes": False}
            try:
                prepare_connection()
                outcome.update(self._session())
                with transaction.atomic():
                    model, pk = (Inventory, self.inventory.pk) if stage == "inventory" else (type(self.user), self.user.pk)
                    model.objects.select_for_update().get(pk=pk)
                    ready.set()
                    if not release.wait(timeout=30):
                        raise TimeoutError("HTTP timeout holder was not released")
                outcome["committed_without_change"] = True
            except Exception as error:
                outcome["unexpected_error"] = repr(error)
            finally:
                holder_results.put(outcome)
                connections.close_all()

        def matches(sql):
            return self._inventory_update(sql) if stage == "inventory" else self._user_lock(sql)

        def observe(execute, sql, params, many, context):
            if not matches(sql):
                return execute(sql, params, many, context)
            row = {"sql": sql, "params": params, "session": self._session(),
                   "facts_before": self._facts(), "in_atomic": connection.in_atomic_block}
            queries.append(row)
            started.set()
            clock = monotonic()
            try:
                return execute(sql, params, many, context)
            except DatabaseError as error:
                row.update(mysql_error_number=error.args[0], elapsed_seconds=monotonic() - clock)
                raise

        def contender():
            outcome = {}
            try:
                prepare_connection()
                outcome["before"] = self._session()
                identities["contender"] = outcome["before"]["connection_id"]
                with connection.execute_wrapper(observe):
                    response = self._post()
                outcome.update(status=response.status_code, payload=response.json(),
                               after=self._session(), returned_before_release=not release.is_set(),
                               in_atomic=connection.in_atomic_block, autocommit=connection.get_autocommit(),
                               snapshot=self._snapshot())
            except Exception as error:
                outcome["unexpected_error"] = repr(error)
            finally:
                contender_results.put(outcome)
                connections.close_all()
                done.set()

        holder_thread = Thread(target=holder, daemon=True)
        contender_thread = Thread(target=contender, daemon=True)
        server_update = None
        holder_thread.start()
        try:
            self.assertTrue(ready.wait(timeout=10))
            contender_thread.start()
            self.assertTrue(started.wait(timeout=10))
            deadline = monotonic() + 3
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT ID, COMMAND, STATE, INFO FROM information_schema.processlist WHERE ID=%s AND DB=%s",
                        (identities["contender"], connection.settings_dict["NAME"]),
                    )
                    row = cursor.fetchone()
                if row and row[1] == "Query" and row[3] and matches(row[3]):
                    self.assertFalse(done.is_set())
                    self.assertFalse(release.is_set())
                    server_update = {"connection_id": row[0], "command": row[1], "state": row[2],
                                     "sql": row[3], "observed_before_release": True}
                    break
                if done.wait(timeout=0.01):
                    break
            self.assertIsNotNone(server_update, "必须观察实际未完成SQL，不能只根据耗时推断")
            self.assertTrue(done.wait(timeout=10))
            self.assertFalse(release.is_set())
            self._assert_no_checkout_effects(available_stock=5)
        finally:
            release.set()
            holder_thread.join(timeout=15)
            if contender_thread.ident is not None:
                contender_thread.join(timeout=15)
        self.assertFalse(holder_thread.is_alive())
        self.assertFalse(contender_thread.is_alive())
        held, attempted = holder_results.get_nowait(), contender_results.get_nowait()
        self.assertNotIn("unexpected_error", held, held)
        self.assertNotIn("unexpected_error", attempted, attempted)
        self.assertEqual(len({observer["connection_id"], held["connection_id"], attempted["before"]["connection_id"]}), 3)
        self.assertTrue(held["committed_without_change"])
        self.assertEqual(attempted["status"], 409)
        self.assertEqual(attempted["payload"]["code"], expected_code)
        self.assertTrue(attempted["returned_before_release"])
        self.assertFalse(attempted["in_atomic"])
        self.assertTrue(attempted["autocommit"])
        self.assertEqual(attempted["after"], attempted["before"])
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0]["mysql_error_number"], 1205)
        self.assertEqual(queries[0]["session"]["wait_seconds"], 5)
        self.assertTrue(queries[0]["in_atomic"])
        self.assertEqual(queries[0]["facts_before"], PENDING_FACTS if stage == "inventory" else {name: 0 for name in PENDING_FACTS})
        self.assertTrue(all(value == 0 for value in attempted["snapshot"]["facts"].values()))
        self.assertEqual(attempted["snapshot"]["inventory"], {"available_stock": 5, "reserved_stock": 0})
        retry, replay = self._post(), self._post()
        self.assertEqual((retry.status_code, replay.status_code), (201, 200))
        self.assertEqual(retry.json()["public_id"], replay.json()["public_id"])
        self._assert_complete_flash_sale_order(key="http-wait-original-key")
        self.assertEqual(self._session(), observer)
        self._emit("real_" + stage + "_http_timeout", evidence_kind="real_isolated_mysql_1205",
                   observer=observer, holder=held, contender=attempted, queries=queries, server_sql=server_update,
                   retry_status=retry.status_code, replay_status=replay.status_code, final=self._snapshot())

    def test_real_inventory_timeout_returns_stock_busy_then_original_key_retries(self):
        self._real_http_timeout(stage="inventory", expected_code="stock_busy")

    def test_real_user_timeout_returns_checkout_busy_then_original_key_retries(self):
        self._real_http_timeout(stage="user", expected_code="checkout_busy")
