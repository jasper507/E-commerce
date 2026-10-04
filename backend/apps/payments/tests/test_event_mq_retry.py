"""真实 Worker 的自动 ETA 任务；测试连接真实制造 MySQL 锁等待超时。"""

import os
import re
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import skipUnless

import pytest
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.payments.dispatch import publish_payment_event
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.services import receive_payment_event
from apps.payments.tests.test_event_mq import MQ_ENABLED, ROOT, PaymentMQIntegrationFixture


@pytest.mark.rabbitmq_integration
@skipUnless(MQ_ENABLED and os.getenv("PROJECT_ONE_TEST_MQ_STAGE") == "retry",
            "Requires explicit isolated retry experiment Worker.")
@override_settings(PAYMENT_EVENTS_ASYNC=True)
class PaymentMQRetryIntegrationTests(PaymentMQIntegrationFixture, TransactionTestCase):
    evidence_name = "phase-5c2-payment-mq-retry-data.json"

    def setUp(self):
        super().setUp()
        self.observation_started_at = timezone.now().isoformat()

    @contextmanager
    def lock_inventory(self):
        blocker = connection.copy(alias="mq-retry-blocker")
        blocker.set_autocommit(False)
        try:
            with blocker.cursor() as cursor:
                cursor.execute("SELECT id FROM inventory_inventory WHERE id=%s FOR UPDATE", [self.inventory.pk])
                self.assertEqual(cursor.fetchone()[0], self.inventory.pk)
            yield
        finally:
            blocker.rollback()
            blocker.close()

    def wait_for_attempt(self, event, count, kind):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            event.refresh_from_db()
            if (event.processing_status, event.processing_attempt_count, event.failure_kind) == ("ERROR", count, kind):
                return {"count": count, "failure_kind": kind,
                        "failed_at": event.processed_at.isoformat(),
                        "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None}
            time.sleep(0.05)
        self.fail("Real Worker did not reach the expected persistent retry state.")

    def accept(self, attempt, external_id):
        payload = self.payload(attempt, external_id)
        event = receive_payment_event(payload=payload, signature=sign_payload(payload))
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("RECEIVED", 0))
        return event

    def assert_unpaid(self, order, attempt):
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("PENDING_PAYMENT", "CREATED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 1))
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def experiment_logs(self):
        result = subprocess.run(
            ["docker", "compose", "-f", "compose.yaml", "-f", "compose.payment-mq-retry-test.yaml",
             "--profile", "payment-mq-test", "logs", "--no-color", "--since",
             self.observation_started_at, "payment-worker-test"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, "Unable to read isolated experiment logs.")
        return result.stdout

    def observed_execution(self, event):
        # 限本场景日志；只保存测试观察器的整数错误号、内部 ID 和资格时刻。
        logs = self.experiment_logs()
        claims = [
            {"count": int(count), "as_of": instant}
            for inbox_id, count, instant in re.findall(
                r"Payment retry experiment claim: inbox_id=(\d+) count=(\d+) as_of=(\S+)", logs
            ) if int(inbox_id) == event.pk
        ]
        return logs.count("Payment retry experiment MySQL errno=1205"), claims

    def wait_for_early_deferral(self, event):
        deadline = time.monotonic() + 4
        expected = f"Payment retry experiment deferred: inbox_id={event.pk} count=1"
        while time.monotonic() < deadline:
            if expected in self.experiment_logs():
                return
            time.sleep(0.1)
        self.fail("Early duplicate did not defer without a new processing claim.")

    def test_transient_failure_and_early_duplicate_automatically_finish_original_event(self):
        order, attempt = self.new_attempt("mq-auto-retry")
        event = self.accept(attempt, "inbox-mq-auto-retry")
        # 两条立即消息先入队，保证重复任务不排在首条 ETA 任务之后。
        self.assertTrue(publish_payment_event(event_id=event.pk))
        with self.lock_inventory():
            self.start_worker()
            first = self.wait_for_attempt(event, 1, "TRANSIENT")
            retry_at = event.next_retry_at
            self.assertEqual(retry_at - event.processed_at, timedelta(seconds=5))
            self.assert_unpaid(order, attempt)
            self.assertLess(timezone.now(), retry_at)
            # 第一条失败后，已排队的重复消息不新增预算，仍保留到期机会。
            self.wait_for_early_deferral(event)
        # 不运行人工恢复、不查单/重新付款；只释放受控锁等待后台任务。
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(4)
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(event.processing_attempt_count, 2)
        self.assertGreaterEqual(event.processed_at, retry_at)
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 0))
        self.assertEqual((event.failure_kind, event.next_retry_at), ("NONE", None))
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        errors, claims = self.observed_execution(event)
        self.assertEqual(errors, 1)
        self.assertEqual([claim["count"] for claim in claims], [1, 2])
        self.assertGreaterEqual(datetime.fromisoformat(claims[1]["as_of"]), retry_at)
        self.record("transient_then_automatic_success_with_early_duplicate", failures=[first],
                    final_processed_at=event.processed_at.isoformat(), processing_attempts=2,
                    order_state=order.status, payment_state=attempt.status, inventory=[19, 0],
                    event_count=1, payment_attempt_count=1, commit_movements=1, mysql_1205_observations=errors,
                    claims=claims,
                    queue=queue)

    def test_persistent_lock_failure_stops_at_three_attempts_and_repost_does_not_reset_it(self):
        order, attempt = self.new_attempt("mq-exhausted")
        event = self.accept(attempt, "inbox-mq-exhausted")
        with self.lock_inventory():
            self.start_worker()
            first = self.wait_for_attempt(event, 1, "TRANSIENT")
            first_due = event.next_retry_at
            self.assertEqual(event.next_retry_at - event.processed_at, timedelta(seconds=5))
            second = self.wait_for_attempt(event, 2, "TRANSIENT")
            second_due = event.next_retry_at
            self.assertGreaterEqual(event.processed_at, first_due)
            self.assertEqual(event.next_retry_at - event.processed_at, timedelta(seconds=10))
            third = self.wait_for_attempt(event, 3, "EXHAUSTED")
            self.assertGreaterEqual(event.processed_at, second_due)
            self.assertIsNone(event.next_retry_at)
            self.assert_unpaid(order, attempt)
        self.assertTrue(publish_payment_event(event_id=event.pk))
        queue = self.wait_for_confirmed_tasks(4)
        event.refresh_from_db()
        self.assertEqual((event.failure_kind, event.processing_attempt_count), ("EXHAUSTED", 3))
        self.assertEqual(event.error_code, "database_transient_error")
        self.assert_unpaid(order, attempt)
        errors, claims = self.observed_execution(event)
        self.assertEqual(errors, 3)
        self.assertEqual([claim["count"] for claim in claims], [1, 2, 3])
        self.assertGreaterEqual(datetime.fromisoformat(claims[1]["as_of"]), first_due)
        self.assertGreaterEqual(datetime.fromisoformat(claims[2]["as_of"]), second_due)
        self.record("three_real_timeouts_then_exhausted", failures=[first, second, third],
                    processing_attempts=3, failure_kind="EXHAUSTED", next_retry_at=None,
                    order_state=order.status, payment_state=attempt.status, inventory=[19, 1],
                    commit_movements=0, mysql_1205_observations=errors, claims=claims, queue=queue)

    def test_invariant_error_stays_blocked_without_background_retry(self):
        order, attempt = self.new_attempt("mq-blocked")
        # 故意破坏测试预占记录来触发未知库存不变量，不是正常取消流程。
        InventoryReservation.objects.filter(order_item__order=order).update(status="RELEASED")
        event = self.accept(attempt, "inbox-mq-blocked")
        self.start_worker()
        self.wait_for_attempt(event, 1, "BLOCKED")
        queue = self.wait_for_confirmed_tasks(1)
        self.assertIsNone(event.next_retry_at)
        self.assertEqual(event.error_code, "payment_invariant_error")
        self.assert_unpaid(order, attempt)
        self.record("invariant_error_not_retried", processing_attempts=1, failure_kind="BLOCKED",
                    next_retry_at=None, order_state=order.status, payment_state=attempt.status,
                    inventory=[19, 1], commit_movements=0, queue=queue)
