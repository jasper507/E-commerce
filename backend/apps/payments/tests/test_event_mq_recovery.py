"""真实 Broker 停机后的初次与重试漏投：人工补投原 Inbox ID。"""

import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from io import StringIO
from unittest import skipUnless

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.recovery import republish_payment_events
from apps.payments.services import receive_payment_event
from apps.payments.tests.test_event_mq import MQ_ENABLED, ROOT, PaymentMQIntegrationFixture
from config.celery import app


@pytest.mark.rabbitmq_integration
@skipUnless(MQ_ENABLED and os.getenv("PROJECT_ONE_TEST_MQ_STAGE") == "recovery",
            "Requires explicit isolated Broker outage experiment configuration.")
@override_settings(PAYMENT_EVENTS_ASYNC=True)
class PaymentMQRecoveryIntegrationTests(PaymentMQIntegrationFixture, TransactionTestCase):
    evidence_name = "phase-5c3-payment-publication-recovery-data.json"

    def setUp(self):
        super().setUp()
        self.observation_started_at = timezone.now().isoformat()

    def broker_action(self, action):
        result = subprocess.run(
            ["docker", "compose", "--profile", "payment-mq-test", *action, "rabbitmq-test"],
            cwd=ROOT, capture_output=True, text=True, timeout=55,
        )
        if result.returncode:
            raise RuntimeError(f"Isolated Broker action failed: {action[0]}")

    def restore_broker(self):
        self.broker_action(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "45"])

    def ready_message_count(self):
        # 管理 API 的统计可能滞后；同步 AMQP 被动声明确认队列里的真实条数。
        with app.connection_for_write() as broker_connection:
            state = broker_connection.channel().queue_declare(
                queue=settings.CELERY_TASK_DEFAULT_QUEUE, passive=True
            )
            self.assertEqual(state.consumer_count, 0)
            return state.message_count

    def tearDown(self):
        # 即使故障断言失败，也先停止执行者、恢复 Broker，再由父类清理队列。
        self.docker_worker(["stop"])
        self.restore_broker()
        super().tearDown()

    def snapshot(self, event):
        event.refresh_from_db()
        return {"status": event.processing_status, "failure_kind": event.failure_kind,
                "count": event.processing_attempt_count,
                "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None,
                "processed_at": event.processed_at.isoformat() if event.processed_at else None}

    def assert_unpaid(self, order, attempt):
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("PENDING_PAYMENT", "CREATED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 1))
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def assert_paid_once(self, event, order, attempt, count):
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("PROCESSED", count))
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 0))
        self.assertEqual(InventoryReservation.objects.get(order_item__order=order).status, "COMMITTED")
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)

    def publish_command(self, event, *, dry_run=False, new_process=False):
        if new_process:
            expected_environment = {
                "MYSQL_DATABASE": "test_project_one", "MYSQL_TEST_DATABASE": "test_project_one",
                "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": "3307", "DJANGO_ENV": "test",
                "PAYMENT_EVENTS_ASYNC": "true", "CELERY_TASK_DEFAULT_QUEUE": "payment-events-test",
                "CELERY_BROKER_URL": settings.CELERY_BROKER_URL,
            }
            if any(os.getenv(key) != value for key, value in expected_environment.items()):
                raise RuntimeError("Recovery subprocess refuses a non-test environment.")
            # 实际 CLI 每次新进程；不复用测试进程停机前的 Broker 连接池。
            command = [sys.executable, "-X", "utf8", "backend/manage.py", "recover_payment_events",
                       "--publish", "--event-id", str(event.pk)]
            if dry_run:
                command.append("--dry-run")
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, "Isolated recovery CLI failed.")
            return result.stdout.strip()
        output = StringIO()
        call_command("recover_payment_events", publish=True, event_id=event.pk,
                     dry_run=dry_run, stdout=output, stderr=StringIO())
        return output.getvalue().strip()

    def test_initial_publication_outage_preserves_receipt_and_duplicate_recovery_pays_once(self):
        order, attempt = self.new_attempt("mq-missed-initial")
        payload = self.payload(attempt, "inbox-mq-missed-initial")
        self.broker_action(["stop"])
        response = APIClient().post(
            "/api/payments/mock/webhook/", payload, format="json",
            HTTP_X_MOCK_SIGNATURE=sign_payload(payload), REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual((response.status_code, response.data["processing_status"]), (202, "RECEIVED"))
        event = PaymentEvent.objects.get(event_id=payload["event_id"])
        before = self.snapshot(event)
        self.assertEqual((before["status"], before["count"]), ("RECEIVED", 0))
        self.assertEqual(event.raw_payload, payload)
        self.assert_unpaid(order, attempt)
        preview = self.publish_command(event, dry_run=True)
        self.assertIn("selected=1", preview)
        with self.assertRaises(CommandError):
            call_command("recover_payment_events", publish=True, event_id=event.pk,
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self.snapshot(event), before)
        self.restore_broker()
        self.assertEqual(self.ready_message_count(), 0)
        first = self.publish_command(event)
        second = self.publish_command(event)
        self.assertIn("published=1", first)
        self.assertIn("published=1", second)
        self.assertEqual(self.snapshot(event), before)
        self.assert_unpaid(order, attempt)
        self.start_worker()
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(2)
        self.assert_paid_once(event, order, attempt, 1)
        self.assertEqual(republish_payment_events(dry_run=True).selected_ids, ())
        self.record("initial_publish_outage_then_duplicate_manual_republication",
                    http_status=202, before_republication=before, preview=preview,
                    publication_outputs=[first, second], broker_down_publication_failed=True,
                    processing_attempts=1, inventory=[19, 0], commit_movements=1,
                    event_count=1, payment_attempt_count=1, queue=queue)

    @contextmanager
    def lock_inventory(self):
        blocker = connection.copy(alias="mq-recovery-blocker")
        blocker.set_autocommit(False)
        try:
            with blocker.cursor() as cursor:
                cursor.execute("SELECT id FROM inventory_inventory WHERE id=%s FOR UPDATE", [self.inventory.pk])
                self.assertEqual(cursor.fetchone()[0], self.inventory.pk)
            yield
        finally:
            blocker.rollback()
            blocker.close()

    def logs(self):
        result = subprocess.run(
            ["docker", "compose", "--profile", "payment-mq-test", "logs", "--no-color",
             "--since", self.observation_started_at, "payment-worker-test"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, "Unable to read isolated recovery experiment logs.")
        return result.stdout

    def wait_for_log(self, expected):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if expected in self.logs():
                return
            time.sleep(0.1)
        self.fail("Isolated Worker did not reach the expected real publication window.")

    def test_retry_publication_outage_keeps_budget_and_manual_republication_finishes_original(self):
        order, attempt = self.new_attempt("mq-missed-retry")
        payload = self.payload(attempt, "inbox-mq-missed-retry")
        event = receive_payment_event(payload=payload, signature=sign_payload(payload))
        with self.lock_inventory():
            self.start_worker()
            self.wait_for_log(f"Payment recovery experiment waiting: inbox_id={event.pk}")
            before = self.snapshot(event)
            self.assertEqual((before["status"], before["failure_kind"], before["count"]),
                             ("ERROR", "TRANSIENT", 1))
            retry_at = event.next_retry_at
            self.assertLess(timezone.now(), retry_at)
            early_preview = self.publish_command(event, dry_run=True)
            self.assertIn("selected=0", early_preview)
            self.assert_unpaid(order, attempt)
            self.broker_action(["stop"])
            self.wait_for_log(f"Payment retry publication failed: inbox_id={event.pk}")
            self.docker_worker(["stop"])
            self.assertEqual(self.snapshot(event), before)
        self.restore_broker()
        # 刻意移除原未确认消息，隔离人工补投的贡献；只触及专用测试队列。
        removed = self.ready_message_count()
        self.api("/contents", method="DELETE")
        self.assertEqual(self.ready_message_count(), 0)
        self.before_ack = self.api().get("message_stats", {}).get("ack", 0)
        self.assertGreaterEqual(timezone.now(), retry_at)
        due_preview = self.publish_command(event, dry_run=True)
        self.assertIn("selected=1", due_preview)
        publication = self.publish_command(event, new_process=True)
        self.assertIn("published=1", publication)
        self.assertEqual(self.snapshot(event), before)
        self.assert_unpaid(order, attempt)
        self.start_worker()
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(1)
        self.assert_paid_once(event, order, attempt, 2)
        logs = self.logs()
        self.assertEqual(logs.count("Payment retry experiment MySQL errno=1205"), 1)
        claims = [{"count": int(count), "as_of": instant} for inbox_id, count, instant in re.findall(
            r"Payment retry experiment claim: inbox_id=(\d+) count=(\d+) as_of=(\S+)", logs
        ) if int(inbox_id) == event.pk]
        self.assertEqual([item["count"] for item in claims], [1, 2])
        self.assertGreaterEqual(datetime.fromisoformat(claims[1]["as_of"]), retry_at)
        self.record("retry_publish_outage_then_manual_republication",
                    before_republication=before, early_preview=early_preview, due_preview=due_preview,
                    publication_output=publication, broker_down_retry_publication_failed=True,
                    final_republication_used_new_cli_process=True,
                    deliberately_removed_old_ready_messages=removed, processing_attempts=2,
                    mysql_1205_observations=1, claims=claims, event_count=1, payment_attempt_count=1,
                    inventory=[19, 0], commit_movements=1, queue=queue)
