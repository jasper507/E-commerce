"""C2 调度规则与业务 Oracle；受控异常/交错不代表真实 MQ 或 Worker 故障证据。"""

from datetime import timedelta
from unittest.mock import call, patch

from django.db import OperationalError, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.ordering.models import Order
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.services import _claim_event_processing, process_stored_payment_event
from apps.payments.tasks import PaymentEventTaskError, process_payment_event_task
from apps.payments.tests.test_event_recovery import PaymentEventRecoveryFixture


@override_settings(PAYMENT_EVENTS_ASYNC=False)
class PaymentEventTaskRetryTests(PaymentEventRecoveryFixture, TransactionTestCase):
    def transient_event(self, key, *, as_of):
        order, attempt = self.new_attempt(key)
        event = self.interrupt_after_receipt(attempt, f"inbox-{key}")
        with patch(
            "apps.payments.services.process_payment_event",
            side_effect=OperationalError(1213, "controlled transient failure"),
        ):
            with self.assertRaises(OperationalError):
                process_stored_payment_event(event_id=event.pk, as_of=as_of)
        event.refresh_from_db()
        return order, attempt, event

    def assert_payment_pending(self, order, attempt):
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(attempt.status, PaymentAttempt.Status.CREATED)
        self.assertEqual(
            InventoryReservation.objects.get(order_item__order=order).status,
            InventoryReservation.Status.RESERVED,
        )
        self.assertFalse(
            InventoryMovement.objects.filter(
                reservation__order_item__order=order, movement_type=InventoryMovement.Type.COMMIT
            ).exists()
        )

    def test_first_failure_sends_only_id_and_five_second_eta_after_rollback(self):
        clock = timezone.now()
        order, attempt = self.new_attempt("task-first-failure")
        event = self.interrupt_after_receipt(attempt, "inbox-task-first-failure")
        due = clock + timedelta(seconds=5)

        def observe_publication(**options):
            self.assertFalse(connection.in_atomic_block)
            saved = PaymentEvent.objects.get(pk=event.pk)
            self.assertEqual(
                (saved.processing_status, saved.failure_kind, saved.processing_attempt_count),
                ("ERROR", "TRANSIENT", 1),
            )
            self.assertEqual(saved.next_retry_at, due)
            self.assert_payment_pending(order, attempt)
            self.assertEqual(
                options,
                {
                    "args": (event.pk,), "eta": due, "retry": False,
                    "timeout": 3, "confirm_timeout": 3,
                },
            )

        with patch("apps.payments.services.timezone.now", return_value=clock):
            with patch(
                "apps.payments.services.InventoryMovement.objects.create",
                side_effect=OperationalError(1205, "controlled lock wait"),
            ):
                with patch(
                    "apps.payments.tasks.process_payment_event_task.apply_async",
                    side_effect=observe_publication,
                ) as send:
                    self.assertEqual(process_payment_event_task.run(event.pk), "ERROR")

        send.assert_called_once()
        event.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(event.error_code, "database_transient_error")
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 1))
        self.assertEqual(process_payment_event_task.max_retries, 0)

    def test_second_failure_waits_ten_seconds_and_third_exhausts_total_budget(self):
        clock = timezone.now()
        order, attempt, event = self.transient_event("task-exhaustion", as_of=clock)
        first_due = clock + timedelta(seconds=5)
        self.assertEqual(event.next_retry_at, first_due)
        second_due = first_due + timedelta(seconds=10)

        with patch("apps.payments.services.timezone.now", return_value=first_due):
            with patch(
                "apps.payments.services.process_payment_event",
                side_effect=OperationalError(1213, "controlled second deadlock"),
            ):
                with patch("apps.payments.tasks.publish_payment_event", return_value=True) as publish:
                    self.assertEqual(process_payment_event_task.run(event.pk), "ERROR")
        publish.assert_called_once_with(event_id=event.pk, eta=second_due)
        event.refresh_from_db()
        self.assertEqual((event.failure_kind, event.processing_attempt_count), ("TRANSIENT", 2))
        self.assertEqual(event.next_retry_at, second_due)

        with patch("apps.payments.tasks.publish_payment_event") as publish:
            with patch("apps.payments.services.timezone.now", return_value=second_due):
                with patch(
                    "apps.payments.services.process_payment_event",
                    side_effect=OperationalError(1213, "controlled third deadlock"),
                ):
                    with self.assertRaisesMessage(PaymentEventTaskError, "event_processing_failed"):
                        process_payment_event_task.run(event.pk)
            with patch("apps.payments.services.timezone.now", return_value=second_due + timedelta(days=1)):
                with patch("apps.payments.services.process_payment_event") as process:
                    self.assertEqual(process_payment_event_task.run(event.pk), "ERROR")
                    process.assert_not_called()
        publish.assert_not_called()
        event.refresh_from_db()
        self.assertEqual((event.processing_status, event.failure_kind, event.processing_attempt_count),
                         ("ERROR", "EXHAUSTED", 3))
        self.assertEqual(event.error_code, "database_transient_error")
        self.assertIsNone(event.next_retry_at)
        self.assert_payment_pending(order, attempt)
        self.assertEqual((PaymentAttempt.objects.count(), PaymentEvent.objects.count()), (1, 1))

    def test_due_retry_then_duplicate_delivery_commits_inventory_once(self):
        order, attempt, event = self.transient_event("task-due-success", as_of=timezone.now())
        with patch("apps.payments.services.timezone.now", return_value=event.next_retry_at):
            with patch("apps.payments.tasks.publish_payment_event") as publish:
                first = process_payment_event_task.run(event.pk)
                repeated = process_payment_event_task.run(event.pk)
        publish.assert_not_called()
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((first, repeated), ("PROCESSED", "PROCESSED"))
        self.assertEqual(event.processing_attempt_count, 2)
        self.assertEqual((event.failure_kind, event.error_code, event.next_retry_at), ("NONE", "", None))
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 0))
        self.assertEqual(
            InventoryMovement.objects.filter(movement_type=InventoryMovement.Type.COMMIT).count(), 1
        )
        self.assertEqual((PaymentAttempt.objects.count(), PaymentEvent.objects.count()), (1, 1))

    def test_retry_publication_false_or_raw_exception_preserves_original_error_and_budget(self):
        clock = timezone.now()
        for raises in (False, True):
            with self.subTest(raises=raises):
                order, attempt = self.new_attempt(f"task-publish-failure-{raises}")
                event = self.interrupt_after_receipt(attempt, f"inbox-task-publish-failure-{raises}")
                original_payload = event.raw_payload
                publication_options = (
                    {"side_effect": RuntimeError("controlled private publication detail")}
                    if raises else {"return_value": False}
                )
                with patch("apps.payments.services.timezone.now", return_value=clock):
                    with patch(
                        "apps.payments.services.process_payment_event",
                        side_effect=OperationalError(1213, "controlled private processing detail"),
                    ):
                        with patch("apps.payments.tasks.publish_payment_event", **publication_options) as publish:
                            with self.assertLogs("apps.payments.tasks", level="WARNING") as logs:
                                with self.assertRaises(PaymentEventTaskError) as caught:
                                    process_payment_event_task.run(event.pk)
                self.assertEqual(str(caught.exception), "event_retry_publication_failed")
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertNotIn("controlled private processing detail", " ".join(logs.output))
                self.assertNotIn("controlled private publication detail", " ".join(logs.output))
                publish.assert_called_once_with(event_id=event.pk, eta=clock + timedelta(seconds=5))
                event.refresh_from_db()
                self.assertEqual(
                    (event.processing_status, event.failure_kind, event.error_code, event.processing_attempt_count),
                    ("ERROR", "TRANSIENT", "database_transient_error", 1),
                )
                self.assertEqual(event.next_retry_at, clock + timedelta(seconds=5))
                self.assertEqual(event.raw_payload, original_payload)
                self.assert_payment_pending(order, attempt)

    def test_early_duplicate_messages_keep_budget_and_reschedule_original_due_time(self):
        order, attempt, event = self.transient_event("task-early", as_of=timezone.now())
        original_due = event.next_retry_at
        before_due = original_due - timedelta(microseconds=1)
        with patch("apps.payments.services.timezone.now", return_value=before_due):
            with patch("apps.payments.services.process_payment_event") as process:
                with patch("apps.payments.tasks.publish_payment_event", return_value=True) as publish:
                    self.assertEqual(process_payment_event_task.run(event.pk), "ERROR")
                    self.assertEqual(process_payment_event_task.run(event.pk), "ERROR")
        process.assert_not_called()
        self.assertEqual(publish.call_args_list, [
            call(event_id=event.pk, eta=before_due + timedelta(seconds=1))
        ] * 2)
        event.refresh_from_db()
        self.assertEqual((event.processing_attempt_count, event.next_retry_at), (1, original_due))
        self.assert_payment_pending(order, attempt)

    def test_fresh_terminal_state_wins_over_stale_error_return_or_old_exception(self):
        # 顺序控制另一个处理者已提交的事实，不把这个交错当作真实线程并发实验。
        for signature, old_exception in (("valid", False), ("forged", False), ("valid", True)):
            with self.subTest(signature=signature, old_exception=old_exception):
                key = f"task-fresh-terminal-{signature}-{old_exception}"
                order, attempt, stale_error = self.transient_event(key, as_of=timezone.now())
                if signature == "forged":
                    PaymentEvent.objects.filter(pk=stale_error.pk).update(signature="forged")
                terminal = "PROCESSED" if signature == "valid" else "REJECTED"

                def complete_before_old_handler_returns(*, event_id):
                    completed = process_stored_payment_event(
                        event_id=event_id, as_of=stale_error.next_retry_at
                    )
                    self.assertEqual(completed.processing_status, terminal)
                    if old_exception:
                        raise RuntimeError("controlled obsolete handler failure")
                    return stale_error

                with patch(
                    "apps.payments.tasks.process_stored_payment_event",
                    side_effect=complete_before_old_handler_returns,
                ):
                    with patch("apps.payments.tasks.publish_payment_event") as publish:
                        if old_exception:
                            with self.assertRaisesMessage(PaymentEventTaskError, "event_processing_failed"):
                                process_payment_event_task.run(stale_error.pk)
                        else:
                            self.assertEqual(process_payment_event_task.run(stale_error.pk), terminal)
                publish.assert_not_called()
                saved = PaymentEvent.objects.get(pk=stale_error.pk)
                self.assertEqual((saved.processing_status, saved.failure_kind, saved.processing_attempt_count),
                                 (terminal, "NONE", 2))
                self.assertIsNone(saved.next_retry_at)
                if signature == "valid":
                    order.refresh_from_db()
                    self.assertEqual(order.status, "PAID")
                    self.assertEqual(InventoryMovement.objects.filter(
                        reservation__order_item__order=order, movement_type=InventoryMovement.Type.COMMIT
                    ).count(), 1)
                else:
                    self.assert_payment_pending(order, attempt)

    def test_fresh_processing_claim_prevents_publication_from_stale_error(self):
        order, attempt, stale_error = self.transient_event("task-fresh-claim", as_of=timezone.now())

        def another_handler_claims(*, event_id):
            _, version = _claim_event_processing(event_id=event_id, as_of=stale_error.next_retry_at)
            self.assertEqual(version, 2)
            return stale_error

        with patch("apps.payments.tasks.process_stored_payment_event", side_effect=another_handler_claims):
            with patch("apps.payments.tasks.publish_payment_event") as publish:
                self.assertEqual(process_payment_event_task.run(stale_error.pk), "PROCESSING")
        publish.assert_not_called()
        saved = PaymentEvent.objects.get(pk=stale_error.pk)
        self.assertEqual((saved.processing_status, saved.processing_attempt_count, saved.next_retry_at),
                         ("PROCESSING", 2, None))
        self.assert_payment_pending(order, attempt)

    def test_blocked_failure_and_incomplete_retry_plans_are_not_scheduled(self):
        order, attempt = self.new_attempt("task-blocked")
        event = self.interrupt_after_receipt(attempt, "inbox-task-blocked")
        with patch(
            "apps.payments.services.process_payment_event",
            side_effect=RuntimeError("controlled private code error"),
        ):
            with patch("apps.payments.tasks.publish_payment_event") as publish:
                with self.assertLogs("apps.payments.tasks", level="WARNING") as logs:
                    with self.assertRaisesMessage(PaymentEventTaskError, "event_processing_failed"):
                        process_payment_event_task.run(event.pk)
        publish.assert_not_called()
        self.assertNotIn("controlled private code error", " ".join(logs.output))
        event.refresh_from_db()
        self.assertEqual((event.failure_kind, event.error_code, event.processing_attempt_count),
                         ("BLOCKED", "unexpected_processing_error", 1))
        self.assertIsNone(event.next_retry_at)

        future = timezone.now() + timedelta(days=1)
        for kind, count, due in (
            ("BLOCKED", 1, None), ("TRANSIENT", 0, future),
            ("TRANSIENT", 1, None), ("TRANSIENT", 3, future),
        ):
            with self.subTest(kind=kind, count=count, due=due):
                PaymentEvent.objects.filter(pk=event.pk).update(
                    failure_kind=kind, processing_attempt_count=count, next_retry_at=due
                )
                with patch("apps.payments.services.process_payment_event") as process:
                    with patch("apps.payments.tasks.publish_payment_event") as publish:
                        self.assertEqual(process_payment_event_task.run(event.pk), "ERROR")
                process.assert_not_called()
                publish.assert_not_called()
                event.refresh_from_db()
                self.assertEqual((event.failure_kind, event.processing_attempt_count, event.next_retry_at),
                                 (kind, count, due))
        self.assert_payment_pending(order, attempt)

    def test_failure_recording_error_keeps_processing_and_never_schedules_unknown_state(self):
        order, attempt = self.new_attempt("task-record-failure")
        event = self.interrupt_after_receipt(attempt, "inbox-task-record-failure")
        with patch(
            "apps.payments.services.InventoryMovement.objects.create",
            side_effect=OperationalError(1213, "controlled private business detail"),
        ):
            with patch(
                "apps.payments.services._record_event_processing_failure",
                side_effect=OperationalError(2006, "controlled private recording detail"),
            ):
                with patch("apps.payments.tasks.publish_payment_event") as publish:
                    with self.assertLogs("apps.payments.tasks", level="WARNING") as logs:
                        with self.assertRaisesMessage(PaymentEventTaskError, "event_processing_failed"):
                            process_payment_event_task.run(event.pk)
        publish.assert_not_called()
        self.assertNotIn("controlled private business detail", " ".join(logs.output))
        self.assertNotIn("controlled private recording detail", " ".join(logs.output))
        event.refresh_from_db()
        self.assertEqual((event.processing_status, event.processing_attempt_count, event.next_retry_at),
                         ("PROCESSING", 1, None))
        self.assert_payment_pending(order, attempt)
