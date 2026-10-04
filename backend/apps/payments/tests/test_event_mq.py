"""真实隔离 RabbitMQ + Linux Worker；不会启动连接开发库的 Worker。"""

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from unittest import skipUnless
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.services import receive_payment_event
from apps.payments.tasks import process_payment_event_task
from apps.payments.tests.test_event_recovery import PaymentEventRecoveryFixture
from config.celery import app


ROOT = Path(__file__).resolve().parents[4]
MQ_ENABLED = bool(os.getenv("PROJECT_ONE_TEST_MQ_BROKER_URL"))


class PaymentMQIntegrationFixture(PaymentEventRecoveryFixture):
    evidence_name = "phase-5c1-payment-mq-data.json"
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        broker = urlsplit(os.environ["PROJECT_ONE_TEST_MQ_BROKER_URL"])
        # 防止测试清空业务队列，或让后台进程碰开发库。
        if (
            connection.settings_dict["NAME"] != "test_project_one"
            or connection.settings_dict["HOST"] != "127.0.0.1"
            or str(connection.settings_dict["PORT"]) != "3307"
            or broker.scheme != "amqp"
            or broker.hostname != "127.0.0.1"
            or broker.port != 5673
            or unquote(broker.path) != "/project_one_test"
            or settings.CELERY_BROKER_URL != os.environ["PROJECT_ONE_TEST_MQ_BROKER_URL"]
            or settings.CELERY_TASK_DEFAULT_QUEUE != "payment-events-test"
            or app.conf.task_always_eager
        ):
            raise RuntimeError("MQ tests require the isolated test database, broker and queue.")
        cls.authorization = "Basic " + base64.b64encode(
            f"{unquote(broker.username or '')}:{unquote(broker.password or '')}".encode()
        ).decode()
        cls.queue_url = "http://127.0.0.1:15673/api/queues/project_one_test/payment-events-test"
        cls.evidence = []

    def api(self, path="", *, method="GET"):
        request = Request(self.queue_url + path, method=method, headers={"Authorization": self.authorization})
        with urlopen(request, timeout=5) as response:
            body = response.read()
        return json.loads(body) if body else None

    def docker_worker(self, action):
        compose_args = ["docker", "compose"]
        if os.getenv("PROJECT_ONE_TEST_MQ_STAGE") == "retry":
            compose_args.extend(["-f", "compose.yaml", "-f", "compose.payment-mq-retry-test.yaml"])
        elif os.getenv("PROJECT_ONE_TEST_MQ_STAGE") == "recovery":
            compose_args.extend(["-f", "compose.yaml", "-f", "compose.payment-mq-recovery-test.yaml"])
        result = subprocess.run(
            [*compose_args, "--profile", "payment-mq-test", *action, "payment-worker-test"],
            cwd=ROOT, capture_output=True, text=True, timeout=45,
        )
        # 不把可能含运行环境信息的完整 Docker 错误输出到测试报告。
        if result.returncode:
            raise RuntimeError(f"Isolated Worker action failed: {action[0]}")

    def setUp(self):
        self.docker_worker(["stop"])
        # 同名长存队列的累计统计可能延迟更新；每场景从独立的零计数开始。
        try:
            self.api(method="DELETE")
        except HTTPError as error:
            if error.code != 404:
                raise
        with app.connection_for_write() as broker_connection:
            app.amqp.queues[settings.CELERY_TASK_DEFAULT_QUEUE](broker_connection.channel()).declare()
        self.api("/contents", method="DELETE")
        deadline = time.monotonic() + 15
        while True:
            queue = self.api()
            self.before_ack = queue.get("message_stats", {}).get("ack", 0)
            if self.before_ack == 0 and queue.get("messages_ready", 0) == 0 and queue.get("messages_unacknowledged", 0) == 0:
                break
            if time.monotonic() >= deadline:
                self.fail("Dedicated queue did not reset its experiment counters.")
            time.sleep(0.2)
        super().setUp()

    def tearDown(self):
        # 先停执行者，再清专用测试队列，之后 Django 才 flush/销毁测试库。
        try:
            self.docker_worker(["stop"])
            self.api("/contents", method="DELETE")
        finally:
            super().tearDown()

    def start_worker(self):
        self.docker_worker(["up", "-d", "--no-deps"])

    def wait_for_event(self, event, state):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            event.refresh_from_db()
            if event.processing_status == state:
                return
            time.sleep(0.1)
        self.fail(f"Inbox {event.pk} did not reach {state} in the real Worker.")

    def wait_for_confirmed_tasks(self, count):
        deadline = time.monotonic() + 30
        latest = {}
        while time.monotonic() < deadline:
            latest = self.api()
            ack = latest.get("message_stats", {}).get("ack", 0)
            if (
                ack >= self.before_ack + count
                and latest.get("messages_ready") == 0
                and latest.get("messages_unacknowledged") == 0
            ):
                return {"ack_delta": ack - self.before_ack, "ready": 0, "unacknowledged": 0}
            time.sleep(0.5)
        self.fail(f"Dedicated queue expected ACK delta {count}; baseline={self.before_ack}, "
                  f"last_ack={latest.get('message_stats', {}).get('ack', 0)}, "
                  f"ready={latest.get('messages_ready')}, unack={latest.get('messages_unacknowledged')}.")

    def record(self, scenario, **facts):
        self.evidence.append({"scenario": scenario, **facts})
        if os.getenv("PROJECT_ONE_WRITE_MQ_EVIDENCE") == "true":
            destination = ROOT / "docs/verification" / self.evidence_name
            destination.write_text(json.dumps({
                "environment": {"database": "test_project_one", "broker_port": 5673,
                                "vhost": "project_one_test", "queue": "payment-events-test", "eager": False},
                "scenarios": self.evidence,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.mark.rabbitmq_integration
@skipUnless(MQ_ENABLED, "Requires explicit isolated RabbitMQ/Worker configuration.")
@override_settings(PAYMENT_EVENTS_ASYNC=True)
class PaymentMQIntegrationTests(PaymentMQIntegrationFixture, TransactionTestCase):
    def test_accepted_webhook_stays_unpaid_until_real_worker_processes_it(self):
        order, attempt = self.new_attempt("mq-accepted")
        payload = self.payload(attempt, "inbox-mq-accepted")
        response = APIClient().post(
            "/api/payments/mock/webhook/", payload, format="json",
            HTTP_X_MOCK_SIGNATURE=sign_payload(payload), REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual((response.status_code, response.data["processing_status"]), (202, "RECEIVED"))
        event = PaymentEvent.objects.get(event_id="inbox-mq-accepted")
        order.refresh_from_db()
        self.assertEqual((order.status, event.processing_attempt_count), ("PENDING_PAYMENT", 0))
        self.start_worker()
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(1)
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        reservation = InventoryReservation.objects.get(order_item__order=order)
        self.assertEqual((order.status, attempt.status, reservation.status), ("PAID", "SUCCEEDED", "COMMITTED"))
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 0))
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        self.record("accepted_then_worker", http_status=202, inbox_state=event.processing_status,
                    processing_attempts=1, order_state=order.status, payment_state=attempt.status,
                    inventory=[19, 0], commit_movements=1, queue=queue)

    def test_multiple_distinct_messages_for_one_inbox_use_one_budget_and_effect(self):
        order, attempt = self.new_attempt("mq-duplicates")
        # 入箱后直接保存 RECEIVED，随后下面三次都是真实发送，不用 eager。
        with override_settings(PAYMENT_EVENTS_ASYNC=False):
            event = self.interrupt_after_receipt(attempt, "inbox-mq-duplicates")
        task_ids = [process_payment_event_task.apply_async(args=(event.pk,), retry=False).id for _ in range(3)]
        self.assertEqual(len(set(task_ids)), 3)
        self.start_worker()
        self.wait_for_event(event, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(3)
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        self.record("three_messages_one_event", task_ids=task_ids, processing_attempts=1,
                    event_count=1, payment_attempt_count=1, commit_movements=1, queue=queue)

    def test_real_worker_rejects_forged_signature_without_blocking_valid_provider_id(self):
        order, attempt = self.new_attempt("mq-invalid")
        payload = self.payload(attempt, "inbox-mq-same-external-id")
        invalid = receive_payment_event(payload=payload, signature="forged")
        self.start_worker()
        self.wait_for_event(invalid, "REJECTED")
        self.wait_for_confirmed_tasks(1)
        order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((order.status, self.inventory.reserved_stock), ("PENDING_PAYMENT", 1))
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())
        self.assertEqual(invalid.error_code, "invalid_signature_or_provider")
        self.assertNotEqual(invalid.event_id, payload["event_id"])
        valid = receive_payment_event(payload=payload, signature=sign_payload(payload))
        self.wait_for_event(valid, "PROCESSED")
        queue = self.wait_for_confirmed_tasks(2)
        order.refresh_from_db()
        self.assertEqual(order.status, "PAID")
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        self.record("invalid_then_valid", invalid_state=invalid.processing_status, invalid_code=invalid.error_code,
                    valid_state=valid.processing_status, order_state=order.status, commit_movements=1, queue=queue)
