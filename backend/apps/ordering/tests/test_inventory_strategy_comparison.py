"""在相同锁竞争条件下比较两种库存预占策略；不接入正式下单。"""

from queue import Queue
from threading import Event, Thread
from time import perf_counter

from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.models import F
from django.test import TransactionTestCase

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


MYSQL_LOCK_NOWAIT_ERROR = 3572


class InventoryStrategyComparisonTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "mysql":
            self.skipTest("行锁行为必须在隔离 MySQL 测试数据库中比较")

        category = Category.objects.create(name="实验商品", slug="strategy-category")
        product = Product.objects.create(
            category=category, name="实验鼠标", slug="strategy-mouse"
        )
        sku = SKU.objects.create(product=product, code="STRATEGY-MOUSE", price="129.00")
        self.inventory = Inventory.objects.create(sku=sku, available_stock=1)

    def _row_lock_reserve(self):
        inventory = Inventory.objects.select_for_update(nowait=True).get(
            pk=self.inventory.pk
        )
        if inventory.available_stock < 1:
            return "sold_out"
        inventory.available_stock -= 1
        inventory.reserved_stock += 1
        inventory.save(update_fields=("available_stock", "reserved_stock"))
        return "reserved"

    def _conditional_reserve(self):
        updated = Inventory.objects.filter(
            pk=self.inventory.pk, available_stock__gte=1
        ).update(
            available_stock=F("available_stock") - 1,
            reserved_stock=F("reserved_stock") + 1,
        )
        return "reserved" if updated == 1 else "sold_out"

    def _compare_under_held_lock(
        self, reserve, *, expect_immediate_busy, rollback_first=False
    ):
        locked = Event()
        release = Event()
        contender_query_started = Event()
        contender_done = Event()
        results = Queue()

        def first_buyer():
            close_old_connections()
            try:
                with transaction.atomic():
                    result = reserve()
                    locked.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("First buyer held the transaction too long")
                    if rollback_first:
                        transaction.set_rollback(True)
                results.put(("first", "rolled_back" if rollback_first else result))
            except Exception as exc:
                results.put(("first_error", exc))
            finally:
                locked.set()
                close_old_connections()

        def second_buyer():
            close_old_connections()
            try:
                started_at = perf_counter()

                def mark_inventory_query(execute, sql, params, many, context):
                    contender_query_started.set()
                    return execute(sql, params, many, context)

                with transaction.atomic():
                    with connection.execute_wrapper(mark_inventory_query):
                        result = reserve()
                results.put(("second", result, perf_counter() - started_at))
            except DatabaseError as exc:
                if exc.args and exc.args[0] == MYSQL_LOCK_NOWAIT_ERROR:
                    results.put(("second", "busy", perf_counter() - started_at))
                else:
                    results.put(("second_error", exc))
            except Exception as exc:
                results.put(("second_error", exc))
            finally:
                contender_done.set()
                close_old_connections()

        first = Thread(target=first_buyer)
        second = Thread(target=second_buyer)
        first.start()
        try:
            self.assertTrue(locked.wait(timeout=10))
            second.start()
            self.assertTrue(contender_query_started.wait(timeout=10))
            if expect_immediate_busy:
                self.assertTrue(contender_done.wait(timeout=3))
            else:
                # 第一笔事务未提交时，条件 UPDATE 还不能判断最终是否有货。
                self.assertFalse(contender_done.wait(timeout=0.2))
        finally:
            release.set()
            first.join(timeout=10)
            if second.ident is not None:
                second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        outcomes = [results.get_nowait() for _ in range(2)]
        self.assertFalse(
            any(outcome[0].endswith("_error") for outcome in outcomes), outcomes
        )
        self.inventory.refresh_from_db()
        print(f"{reserve.__name__}: {outcomes}")
        return {outcome[0]: outcome[1] for outcome in outcomes}

    def test_row_lock_nowait_returns_busy_while_first_transaction_is_open(self):
        outcomes = self._compare_under_held_lock(
            self._row_lock_reserve, expect_immediate_busy=True
        )
        self.assertEqual(outcomes, {"first": "reserved", "second": "busy"})
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (0, 1)
        )

    def test_conditional_update_waits_then_reports_sold_out(self):
        outcomes = self._compare_under_held_lock(
            self._conditional_reserve, expect_immediate_busy=False
        )
        self.assertEqual(outcomes, {"first": "reserved", "second": "sold_out"})
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (0, 1)
        )

    def test_row_lock_nowait_rejects_second_buyer_even_if_first_rolls_back(self):
        outcomes = self._compare_under_held_lock(
            self._row_lock_reserve, expect_immediate_busy=True, rollback_first=True
        )
        self.assertEqual(outcomes, {"first": "rolled_back", "second": "busy"})
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (1, 0)
        )

    def test_conditional_update_can_reserve_after_first_buyer_rolls_back(self):
        outcomes = self._compare_under_held_lock(
            self._conditional_reserve, expect_immediate_busy=False, rollback_first=True
        )
        self.assertEqual(outcomes, {"first": "rolled_back", "second": "reserved"})
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (0, 1)
        )
