"""真实 SIGKILL 与 Broker 重投；仅清理专用测试队列，不清理场景中的原任务。"""

import os
import re
import subprocess
import time
from io import StringIO
from unittest import skipUnless

import pytest
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.ordering.lifecycle_services import cancel_order
from apps.payments.dispatch import publish_payment_event
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent, RefundSimulation
from apps.payments.recovery import recover_stale_payment_events
from apps.payments.services import receive_payment_event
from apps.payments.tests.test_event_mq import MQ_ENABLED, ROOT, PaymentMQIntegrationFixture
from config.celery import app


@pytest.mark.rabbitmq_integration
@skipUnless(MQ_ENABLED and os.getenv("PROJECT_ONE_TEST_MQ_STAGE") == "crash",
            "Requires explicit isolated crash experiment Worker.")
@override_settings(PAYMENT_EVENTS_ASYNC=True, PAYMENT_EVENT_PROCESSING_LEASE_SECONDS=8)
class PaymentMQCrashIntegrationTests(PaymentMQIntegrationFixture, TransactionTestCase):
    evidence_name = "phase-5c4-payment-worker-recovery-data.json"

    def setUp(self):
        super().setUp()
        self.observation_started_at = timezone.now().isoformat()

    def accept(self, attempt, event_id):
        payload = self.payload(attempt, event_id)
        event = receive_payment_event(payload=payload, signature=sign_payload(payload))
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("RECEIVED", 0))
        return event

    def logs(self):
        result = subprocess.run(
            ["docker", "compose", "--profile", "payment-mq-test", "logs", "--no-color",
             "--since", self.observation_started_at, "payment-worker-test"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, "Unable to read isolated crash experiment logs.")
        return result.stdout

    def wait_for_window(self, event, window, atomic):
        deadline = time.monotonic() + 20
        marker = f"Payment crash experiment paused: inbox_id={event.pk} window={window} atomic={atomic}"
        while time.monotonic() < deadline:
            if marker in self.logs():
                return
            time.sleep(0.1)
        self.fail("Real Worker did not pause in the expected transaction window.")

    def snapshot(self, event):
        event.refresh_from_db()
        return {"status": event.processing_status, "failure_kind": event.failure_kind,
                "count": event.processing_attempt_count,
                "started_at": event.processing_started_at.isoformat() if event.processing_started_at else None,
                "lease_until": event.processing_lease_until.isoformat() if event.processing_lease_until else None}

    def wait_until_due(self, event):
        event.refresh_from_db()
        self.assertIsNotNone(event.processing_lease_until)
        deadline = time.monotonic() + 12
        while timezone.now() < event.processing_lease_until:
            if time.monotonic() >= deadline:
                self.fail("The real test lease did not expire.")
            time.sleep(0.1)

    def kill_and_wait_for_original_message(self):
        self.docker_worker(["kill", "-s", "SIGKILL"])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with app.connection_for_write() as broker_connection:
                state = broker_connection.channel().queue_declare(queue="payment-events-test", passive=True)
            if state.message_count >= 1 and state.consumer_count == 0:
                return {"ready_after_kill": state.message_count, "consumers_after_kill": 0}
            time.sleep(0.1)
        self.fail("Original unacknowledged message did not return to the isolated queue.")

    def deliveries(self, event):
        deliveries = [{"task_id": task_id, "redelivered": redelivered == "True"}
                      for event_id, task_id, redelivered in re.findall(
                          r"Payment crash experiment delivery: inbox_id=(\d+) task_id=(\S+) redelivered=(True|False)",
                          self.logs()) if int(event_id) == event.pk]
        originals = {item["task_id"] for item in deliveries if not item["redelivered"]}
        self.assertTrue(any(item["redelivered"] and item["task_id"] in originals for item in deliveries))
        return deliveries

    def assert_unpaid(self, order, attempt):
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("PENDING_PAYMENT", "CREATED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 1))
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def repair_and_publish(self, event):
        # kill已终止连接；极短的连接关闭/回滚延迟仍以skip_locked保护，不硬等待行锁。
        deadline = time.monotonic() + 10
        while True:
            result = recover_stale_payment_events(event_id=event.pk)
            if result.retryable_ids == [event.pk]:
                break
            if time.monotonic() >= deadline:
                self.fail("Expired original claim did not become retryable after real Worker loss.")
            time.sleep(0.1)
        event.refresh_from_db()
        self.assertEqual((event.processing_status, event.failure_kind, event.processing_attempt_count),
                         ("ERROR", "TRANSIENT", 1))
        self.assertIsNone(event.processing_lease_until)
        output = StringIO()
        call_command("recover_payment_events", publish=True, event_id=event.pk,
                     stdout=output, stderr=StringIO())
        self.assertIn("published=1", output.getvalue())
        self.assertEqual(event.processing_attempt_count, 1)
        return {"repair_at": event.processed_at.isoformat(), "publication_output": output.getvalue().strip()}

    def assert_paid_once(self, event, order, attempt, count):
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("PROCESSED", count))
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 0))
        self.assertEqual(InventoryReservation.objects.get(order_item__order=order).status, "COMMITTED")
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertIsNone(event.processing_lease_until)

    def test_kill_after_claim_commit_preserves_budget_then_repairs_original(self):
        order, attempt = self.new_attempt("mq-crash-claim")
        event = self.accept(attempt, "crash-claim-original")
        self.start_worker()
        self.wait_for_window(event, "claim", False)
        before = self.snapshot(event)
        self.assertEqual((before["status"], before["count"]), ("PROCESSING", 1))
        self.assertLess(timezone.now(), event.processing_lease_until)
        self.assertEqual(recover_stale_payment_events(event_id=event.pk).selected_ids, ())
        self.assert_unpaid(order, attempt)
        requeue = self.kill_and_wait_for_original_message()
        self.assertEqual(self.snapshot(event), before)
        self.wait_until_due(event)
        repair = self.repair_and_publish(event)
        self.assert_unpaid(order, attempt)
        self.start_worker()
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(2)
        self.assert_paid_once(event, order, attempt, 2)
        self.record("kill_after_claim_commit_then_manual_repair", window="claim", atomic=False,
                    before_kill=before, **requeue, **repair, processing_attempts=2,
                    inventory=[19, 0], commit_movements=1, deliveries=self.deliveries(event), queue=queue)

    def test_alive_expired_transaction_is_skipped_then_kill_rolls_back_business(self):
        order, attempt = self.new_attempt("mq-crash-transaction")
        event = self.accept(attempt, "crash-transaction-original")
        self.start_worker()
        self.wait_for_window(event, "transaction", True)
        before = self.snapshot(event)
        self.assert_unpaid(order, attempt)
        self.wait_until_due(event)
        started = time.monotonic()
        skipped = recover_stale_payment_events(event_id=event.pk)
        skip_seconds = time.monotonic() - started
        self.assertEqual((skipped.selected_ids, skipped.skipped_ids, skipped.retryable_ids),
                         ((event.pk,), [event.pk], []))
        self.assertLess(skip_seconds, 2)
        self.assertEqual(self.snapshot(event), before)
        requeue = self.kill_and_wait_for_original_message()
        self.assert_unpaid(order, attempt)
        self.assertEqual(self.snapshot(event), before)
        repair = self.repair_and_publish(event)
        self.start_worker()
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(2)
        self.assert_paid_once(event, order, attempt, 2)
        self.record("alive_expired_T2_skipped_then_kill_rolls_back", window="transaction", atomic=True,
                    before_kill=before, skip_seconds=round(skip_seconds, 6), **requeue, **repair,
                    processing_attempts=2, inventory=[19, 0], commit_movements=1,
                    deliveries=self.deliveries(event), queue=queue)

    def test_commit_before_ack_kill_redelivery_does_not_commit_stock_again(self):
        order, attempt = self.new_attempt("mq-crash-paid-commit")
        event = self.accept(attempt, "crash-commit-paid")
        self.start_worker()
        self.wait_for_window(event, "commit", False)
        self.assert_paid_once(event, order, attempt, 1)
        before = self.snapshot(event)
        requeue = self.kill_and_wait_for_original_message()
        self.assertEqual(recover_stale_payment_events(event_id=event.pk).selected_ids, ())
        self.assertTrue(publish_payment_event(event_id=event.pk))
        self.start_worker()
        queue = self.wait_for_confirmed_tasks(2)
        self.assert_paid_once(event, order, attempt, 1)
        self.assertEqual(self.snapshot(event), before)
        self.record("kill_after_paid_commit_before_ack", window="commit", atomic=False,
                    before_kill=before, **requeue, processing_attempts=1, inventory=[19, 0],
                    commit_movements=1, deliveries=self.deliveries(event), queue=queue)

    def test_late_success_refund_commit_before_ack_kill_creates_one_simulation(self):
        order, attempt = self.new_attempt("mq-crash-refund-commit")
        # 已有规则禁止关闭结果未定的活跃付款；先经原处理器确认失败再取消。
        failed_payload = {**self.payload(attempt, "crash-setup-failed-refund"), "status": "FAILED"}
        with override_settings(PAYMENT_EVENTS_ASYNC=False):
            failed = receive_payment_event(payload=failed_payload, signature=sign_payload(failed_payload))
        self.assertEqual(failed.processing_status, "PROCESSED")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "FAILED")
        cancel_order(public_id=order.public_id, user=self.user)
        event = self.accept(attempt, "crash-commit-late-refund")
        self.start_worker()
        self.wait_for_window(event, "commit", False)
        before = self.snapshot(event)
        self.assertEqual((before["status"], before["count"]), ("PROCESSED", 1))
        self.assertEqual(RefundSimulation.objects.count(), 1)
        refund_id = RefundSimulation.objects.get().pk
        requeue = self.kill_and_wait_for_original_message()
        self.assertEqual(recover_stale_payment_events(event_id=event.pk).selected_ids, ())
        self.assertTrue(publish_payment_event(event_id=event.pk))
        self.start_worker()
        queue = self.wait_for_confirmed_tasks(2)
        self.assertEqual(self.snapshot(event), before)
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("CANCELED", "SUCCEEDED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (20, 0))
        self.assertEqual(list(RefundSimulation.objects.values_list("pk", flat=True)), [refund_id])
        self.assertEqual(InventoryMovement.objects.filter(movement_type="RELEASE").count(), 1)
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.record("kill_after_late_refund_commit_before_ack", window="commit", atomic=False,
                    before_kill=before, **requeue, processing_attempts=1, inventory=[20, 0],
                    refund_simulations=1, release_movements=1, commit_movements=0,
                    event_count=2, payment_attempt_count=1, setup_failure_processed_synchronously=True,
                    deliveries=self.deliveries(event), queue=queue)
