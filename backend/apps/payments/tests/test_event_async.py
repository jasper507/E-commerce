"""异步接收的事务/HTTP 合约；这些测试不冒充真实消息投递实验。"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError
from rest_framework.test import APIClient

from apps.inventory.models import InventoryMovement
from apps.ordering.models import CartItem
from apps.ordering.services import create_order_from_cart
from apps.payments.dispatch import publish_payment_event
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.services import PaymentRuleError, receive_payment_event, reconcile_payment_attempt, start_payment
from apps.payments.tasks import PaymentEventTaskError, process_payment_event_task
from apps.payments.tests.test_event_recovery import PaymentEventRecoveryFixture


@override_settings(PAYMENT_EVENTS_ASYNC=True)
class PaymentEventAsyncTests(PaymentEventRecoveryFixture, TransactionTestCase):
    def receive(self, attempt, external_id, signature=None):
        payload = self.payload(attempt, external_id)
        return receive_payment_event(
            payload=payload, signature=sign_payload(payload) if signature is None else signature
        )

    def test_publish_sees_committed_inbox_from_another_connection_and_only_receives_id(self):
        order, attempt = self.new_attempt("async-commit")
        expected_payload = self.payload(attempt, "inbox-async-commit")
        observed = []

        def observe(*, event_id):
            self.assertFalse(connection.in_atomic_block)
            self.assertIs(type(event_id), int)
            observer = connection.copy(alias="publication-observer")
            try:
                with observer.cursor() as cursor:
                    cursor.execute(
                        "SELECT raw_payload, processing_status, processing_attempt_count "
                        "FROM payments_paymentevent WHERE id = %s", [event_id]
                    )
                    saved = cursor.fetchone()
                observed.append(saved)
            finally:
                observer.close()
            return True

        with patch("apps.payments.dispatch.publish_payment_event", side_effect=observe) as publish:
            with patch("apps.payments.services.process_stored_payment_event") as process:
                event = self.receive(attempt, "inbox-async-commit")
        process.assert_not_called()
        publish.assert_called_once_with(event_id=event.pk)
        self.assertEqual(json.loads(observed[0][0]), expected_payload)
        self.assertEqual(observed[0][1:], ("RECEIVED", 0))
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("PENDING_PAYMENT", "CREATED"))
        self.assertEqual(self.inventory.reserved_stock, 1)
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def test_publisher_sends_only_internal_id_and_does_not_retry_network_in_http(self):
        with patch("apps.payments.tasks.process_payment_event_task.apply_async") as send:
            self.assertTrue(publish_payment_event(event_id=7))
        send.assert_called_once_with(args=(7,), retry=False, timeout=3, confirm_timeout=3)

    def test_broker_failure_keeps_received_and_repost_keeps_one_original_event(self):
        order, attempt = self.new_attempt("broker-unavailable")
        with patch("apps.payments.tasks.process_payment_event_task.apply_async",
                   side_effect=BrokerOperationalError("controlled private broker failure")):
            with self.assertLogs("apps.payments.dispatch", level="WARNING") as logs:
                first = self.receive(attempt, "inbox-broker-unavailable")
                repeated = self.receive(attempt, "inbox-broker-unavailable")
        self.assertNotIn("controlled private broker failure", " ".join(logs.output))
        self.assertEqual(first.pk, repeated.pk)
        first.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(first.processing_status, "RECEIVED")
        self.assertEqual(first.processing_attempt_count, 0)
        self.assertEqual(first.error_code, "")
        self.assertEqual(order.status, "PENDING_PAYMENT")
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def test_receive_refuses_outer_transaction_and_storage_failure_before_publication(self):
        _, attempt = self.new_attempt("not-committed")
        with patch("apps.payments.dispatch.publish_payment_event") as publish:
            with transaction.atomic():
                with self.assertRaisesMessage(ValueError, "外层事务"):
                    self.receive(attempt, "inbox-not-committed")
            with patch("apps.payments.services.PaymentEvent.objects.get_or_create", side_effect=IntegrityError("controlled storage failure")):
                with self.assertRaises(IntegrityError):
                    self.receive(attempt, "inbox-not-committed")
        publish.assert_not_called()
        self.assertFalse(PaymentEvent.objects.exists())

    def test_publisher_rejects_transaction_and_invalid_id_before_network(self):
        with patch("apps.payments.tasks.process_payment_event_task.apply_async") as send:
            for invalid in (0, -1, True, "7", None):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    publish_payment_event(event_id=invalid)
            with transaction.atomic():
                with self.assertRaisesMessage(ValueError, "事务之外"):
                    publish_payment_event(event_id=7)
        send.assert_not_called()

    def test_task_reuses_business_gate_and_duplicate_delivery_keeps_one_effect(self):
        order, attempt = self.new_attempt("task-repeat")
        with patch("apps.payments.dispatch.publish_payment_event"):
            event = self.receive(attempt, "inbox-task-repeat")
        first = process_payment_event_task.run(event.pk)
        repeated = process_payment_event_task.run(event.pk)
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual((first, repeated), ("PROCESSED", "PROCESSED"))
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        with patch("apps.payments.dispatch.publish_payment_event") as publish:
            self.receive(attempt, "inbox-task-repeat")
        publish.assert_not_called()

    def test_task_does_not_bypass_future_blocked_exhausted_or_processing(self):
        _, attempt = self.new_attempt("task-gates")
        with patch("apps.payments.dispatch.publish_payment_event"):
            event = self.receive(attempt, "inbox-task-gates")
        for state, kind, count, next_time in (
            ("ERROR", "TRANSIENT", 1, timezone.now() + timedelta(days=1)),
            ("ERROR", "BLOCKED", 1, None),
            ("ERROR", "EXHAUSTED", 3, None),
            ("PROCESSING", "NONE", 1, None),
        ):
            with self.subTest(state=state, kind=kind):
                PaymentEvent.objects.filter(pk=event.pk).update(
                    processing_status=state, failure_kind=kind,
                    processing_attempt_count=count, next_retry_at=next_time,
                )
                with patch("apps.payments.services.process_payment_event") as process:
                    with patch("apps.payments.tasks.publish_payment_event", return_value=True) as publish:
                        self.assertEqual(process_payment_event_task.run(event.pk), state)
                if kind == "TRANSIENT":
                    publish.assert_called_once_with(event_id=event.pk, eta=next_time)
                else:
                    publish.assert_not_called()
                process.assert_not_called()
                event.refresh_from_db()
                self.assertEqual(event.processing_attempt_count, count)

    def test_task_reports_stable_error_without_raw_exception_and_without_auto_retry(self):
        _, attempt = self.new_attempt("task-error")
        with patch("apps.payments.dispatch.publish_payment_event"):
            event = self.receive(attempt, "inbox-task-error")
        with patch("apps.payments.services.process_payment_event", side_effect=RuntimeError("controlled private error")):
            with self.assertLogs("apps.payments.tasks", level="WARNING") as logs:
                with self.assertRaisesMessage(PaymentEventTaskError, "event_processing_failed"):
                    process_payment_event_task.run(event.pk)
        self.assertNotIn("controlled private error", " ".join(logs.output))
        event.refresh_from_db()
        self.assertEqual((event.processing_status, event.failure_kind, event.processing_attempt_count), ("ERROR", "BLOCKED", 1))
        self.assertEqual(process_payment_event_task.max_retries, 0)
        self.assertTrue(process_payment_event_task.acks_late)
        self.assertTrue(process_payment_event_task.reject_on_worker_lost)

    def test_task_invalid_or_missing_id_has_no_payment_side_effect(self):
        for invalid in (True, "7", 0):
            with self.subTest(invalid=invalid):
                with self.assertRaisesMessage(PaymentEventTaskError, "invalid_inbox_id"):
                    process_payment_event_task.run(invalid)
        with self.assertLogs("apps.payments.tasks", level="WARNING") as logs:
            with self.assertRaisesMessage(PaymentEventTaskError, "event_processing_failed"):
                process_payment_event_task.run(999999)
        self.assertNotIn("DoesNotExist", " ".join(logs.output))
        self.assertFalse(PaymentEvent.objects.exists())
        self.assertFalse(InventoryMovement.objects.exists())

    def test_webhook_acceptance_and_final_replay_have_different_http_results(self):
        _, attempt = self.new_attempt("http-accepted")
        payload = self.payload(attempt, "inbox-http-accepted")
        client = APIClient()
        with patch("apps.payments.dispatch.publish_payment_event"):
            accepted = client.post("/api/payments/mock/webhook/", payload, format="json", HTTP_X_MOCK_SIGNATURE=sign_payload(payload), REMOTE_ADDR="127.0.0.1")
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.data["processing_status"], "RECEIVED")
        event = PaymentEvent.objects.get(event_id="inbox-http-accepted")
        process_payment_event_task.run(event.pk)
        replay = client.post("/api/payments/mock/webhook/", payload, format="json", HTTP_X_MOCK_SIGNATURE=sign_payload(payload), REMOTE_ADDR="127.0.0.1")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["processing_status"], "PROCESSED")

    def test_invalid_signature_is_accepted_for_audit_then_rejected_by_task(self):
        order, attempt = self.new_attempt("async-invalid")
        payload = self.payload(attempt, "same-provider-id")
        client = APIClient()
        with patch("apps.payments.dispatch.publish_payment_event"):
            accepted = client.post("/api/payments/mock/webhook/", payload, format="json", HTTP_X_MOCK_SIGNATURE="forged", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(accepted.status_code, 202)
        invalid = PaymentEvent.objects.get(signature="forged")
        self.assertNotEqual(invalid.event_id, "same-provider-id")
        self.assertEqual(process_payment_event_task.run(invalid.pk), "REJECTED")
        rejected = client.post("/api/payments/mock/webhook/", payload, format="json", HTTP_X_MOCK_SIGNATURE="forged", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(rejected.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "PENDING_PAYMENT")
        with patch("apps.payments.dispatch.publish_payment_event"):
            valid = receive_payment_event(payload=payload, signature=sign_payload(payload))
        self.assertEqual(valid.event_id, "same-provider-id")
        self.assertEqual(process_payment_event_task.run(valid.pk), "PROCESSED")

    def test_async_start_and_reconcile_do_not_claim_success_before_processing(self):
        order, attempt = self.new_attempt("async-start")
        # 复用 fixture 的原尝试，以同一请求 Key 重放不再次请求付款。
        with patch("apps.payments.dispatch.publish_payment_event") as publish:
            reconciled = reconcile_payment_attempt(user=self.user, public_id=attempt.public_id)
        self.assertEqual(reconciled.status, "CREATED")
        event = PaymentEvent.objects.get()
        publish.assert_called_once_with(event_id=event.pk)
        replay, created = start_payment(user=self.user, order_public_id=order.public_id, idempotency_key="pay-async-start")
        self.assertFalse(created)
        self.assertEqual(replay.pk, attempt.pk)
        process_payment_event_task.run(event.pk)
        reconciled.refresh_from_db()
        self.assertEqual(reconciled.status, "SUCCEEDED")

    def test_creating_attempt_returns_201_for_creation_while_event_is_still_received(self):
        CartItem.objects.create(cart=self.cart, sku=self.sku, quantity=1)
        order, _ = create_order_from_cart(
            user=self.user, address_id=self.address.pk, idempotency_key="async-create-order"
        )
        client = APIClient()
        client.force_authenticate(self.user)
        with patch("apps.payments.dispatch.publish_payment_event") as publish:
            response = client.post(
                "/api/payments/attempts/", {"order_public_id": str(order.public_id)},
                format="json", HTTP_IDEMPOTENCY_KEY="async-create-attempt",
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "CREATED")
        event = PaymentEvent.objects.get()
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("RECEIVED", 0))
        publish.assert_called_once_with(event_id=event.pk)
        order.refresh_from_db()
        self.assertEqual(order.status, "PENDING_PAYMENT")
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def test_conflicting_external_event_id_cannot_replace_saved_payload_or_publish(self):
        _, attempt = self.new_attempt("async-conflict")
        with patch("apps.payments.dispatch.publish_payment_event"):
            original = self.receive(attempt, "inbox-async-conflict")
        different = {**original.raw_payload, "amount": "1.00"}
        with patch("apps.payments.dispatch.publish_payment_event") as publish:
            with self.assertRaises(PaymentRuleError) as caught:
                receive_payment_event(payload=different, signature=sign_payload(different))
        publish.assert_not_called()
        self.assertEqual(caught.exception.code, "event_id_conflict")
        original.refresh_from_db()
        self.assertEqual(original.raw_payload["amount"], "189.00")
