"""事件重试以数据库事实为准；未知异常不能仅因重复扫描就再次执行。"""

from datetime import timedelta
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError, OperationalError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from apps.catalog.models import SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Order
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.recovery import recover_payment_events
from apps.payments.services import (
    PaymentInvariantError,
    _claim_event_processing,
    _process_claimed_payment_event,
    _record_event_processing_failure,
    process_payment_event,
    process_stored_payment_event,
    receive_payment_event,
)
from apps.payments.tests.test_event_recovery import PaymentEventRecoveryFixture, SimulatedProcessInterruption


class PaymentEventRetryTests(PaymentEventRecoveryFixture, TransactionTestCase):
    def fail_event(self, *, key, error, as_of=None):
        order, attempt = self.new_attempt(key)
        event = self.interrupt_after_receipt(attempt, f"inbox-{key}")
        with patch("apps.payments.services.process_payment_event", side_effect=error):
            with self.assertRaises(type(error)):
                process_stored_payment_event(event_id=event.pk, as_of=as_of)
        event.refresh_from_db()
        return order, attempt, event

    def test_unclassified_error_is_not_a_recovery_candidate(self):
        _, attempt = self.new_attempt("unknown-error")
        payload = self.payload(attempt, "inbox-unknown-error")
        with patch(
            "apps.payments.services.process_payment_event",
            side_effect=RuntimeError("controlled unknown failure"),
        ):
            with self.assertRaises(RuntimeError):
                receive_payment_event(payload=payload, signature=sign_payload(payload))
        event = PaymentEvent.objects.get(event_id="inbox-unknown-error")
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.ERROR)

        # 对照 5A 的候选查询，真实数据库中这条未知异常原本仍会被选中。
        legacy_candidates = tuple(PaymentEvent.objects.filter(
            processing_status__in=("RECEIVED", "ERROR")
        ).values_list("pk", flat=True))
        self.assertEqual(legacy_candidates, (event.pk,))
        self.assertEqual(recover_payment_events(dry_run=True).selected_ids, ())
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.BLOCKED)
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertIsNone(event.next_retry_at)

    def test_transient_failure_keeps_budget_while_business_rolls_back(self):
        clock = timezone.now()
        order, attempt = self.new_attempt("rollback-budget")
        event = self.interrupt_after_receipt(attempt, "inbox-rollback-budget")
        with patch("apps.payments.services.InventoryMovement.objects.create",
                   side_effect=OperationalError(1205, "controlled lock wait")):
            with self.assertRaises(OperationalError):
                process_stored_payment_event(event_id=event.pk, as_of=clock)
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.TRANSIENT)
        self.assertEqual(event.error_code, "database_transient_error")
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual(event.next_retry_at, clock + timedelta(seconds=5))
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(attempt.status, PaymentAttempt.Status.CREATED)
        self.assertEqual(self.inventory.reserved_stock, 1)
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def test_early_scan_repost_and_internal_id_cannot_bypass_delay(self):
        clock = timezone.now()
        _, attempt, event = self.fail_event(
            key="early", error=OperationalError(1213, "controlled deadlock"), as_of=clock
        )
        payload = event.raw_payload
        before_due = event.next_retry_at - timedelta(microseconds=1)
        with patch("apps.payments.services.process_payment_event") as process:
            self.assertEqual(recover_payment_events(event_id=event.pk, as_of=before_due).selected_ids, ())
            deferred = process_stored_payment_event(event_id=event.pk, as_of=before_due)
            with patch("apps.payments.services.timezone.now", return_value=before_due):
                repeated = receive_payment_event(payload=payload, signature=sign_payload(payload))
            process.assert_not_called()
        self.assertEqual(deferred.processing_attempt_count, 1)
        self.assertEqual(repeated.processing_attempt_count, 1)
        event.refresh_from_db()
        self.assertEqual(event.next_retry_at, clock + timedelta(seconds=5))
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_due_recovery_succeeds_at_boundary_and_clears_retry_metadata(self):
        order, _, event = self.fail_event(key="due", error=OperationalError(1213, "controlled deadlock"))
        result = recover_payment_events(event_id=event.pk, as_of=event.next_retry_at)
        self.assertEqual((result.processed, result.skipped, result.failed_ids), (1, 0, []))
        event.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(event.processing_attempt_count, 2)
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.NONE)
        self.assertIsNone(event.next_retry_at)
        self.assertEqual(event.error_code, "")
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)

    def test_three_total_failures_exhaust_budget_across_all_entry_points(self):
        clock = timezone.now()
        _, _, event = self.fail_event(
            key="exhausted", error=OperationalError(1213, "controlled deadlock"), as_of=clock
        )
        for expected_count, delay in ((2, 10), (3, None)):
            # 每轮从 MySQL 重新读状态，命令调用本身没有额外的次数预算。
            event = PaymentEvent.objects.get(pk=event.pk)
            due = event.next_retry_at
            with patch("apps.payments.services.process_payment_event", side_effect=OperationalError(1213, "controlled deadlock")):
                with self.assertLogs("apps.payments.recovery", level="WARNING"):
                    result = recover_payment_events(event_id=event.pk, as_of=due)
            self.assertEqual(result.failed_ids, [event.pk])
            event.refresh_from_db()
            self.assertEqual(event.processing_attempt_count, expected_count)
            self.assertEqual(event.next_retry_at, due + timedelta(seconds=delay) if delay else None)
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.EXHAUSTED)
        self.assertEqual(event.error_code, "database_transient_error")
        later = clock + timedelta(days=1)
        with patch("apps.payments.services.process_payment_event") as process:
            self.assertEqual(recover_payment_events(event_id=event.pk, as_of=later).selected_ids, ())
            process_stored_payment_event(event_id=event.pk, as_of=later)
            with patch("apps.payments.services.timezone.now", return_value=later):
                receive_payment_event(payload=event.raw_payload, signature=event.signature)
            process.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.processing_attempt_count, 3)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def test_only_allowlisted_mysql_operational_errors_are_retryable(self):
        cases = (
            (OperationalError(1205, "controlled lock wait"), "TRANSIENT", "database_transient_error"),
            (OperationalError(1213, "controlled deadlock"), "TRANSIENT", "database_transient_error"),
            (OperationalError(2006, "connection lost"), "BLOCKED", "database_nonretryable_error"),
            (IntegrityError(1062, "duplicate"), "BLOCKED", "database_nonretryable_error"),
            (DatabaseError("other database failure"), "BLOCKED", "database_nonretryable_error"),
            (PaymentInvariantError("missing_reservation"), "BLOCKED", "payment_invariant_error"),
            (TimeoutError("unknown timeout"), "BLOCKED", "unexpected_processing_error"),
        )
        for index, (error, kind, code) in enumerate(cases):
            with self.subTest(error=type(error).__name__, errno=error.args[0]):
                _, _, event = self.fail_event(key=f"classify-{index}", error=error)
                self.assertEqual((event.failure_kind, event.error_code), (kind, code))
                self.assertEqual(event.processing_attempt_count, 1)
                if kind == "BLOCKED":
                    self.assertIsNone(event.next_retry_at)
                    self.assertEqual(recover_payment_events(event_id=event.pk).selected_ids, ())

    def test_inventory_inconsistency_is_blocked_even_after_repair(self):
        order, attempt = self.new_attempt("inventory-block")
        event = self.interrupt_after_receipt(attempt, "inbox-inventory-block")
        reservation = InventoryReservation.objects.get(order_item__order=order)
        InventoryReservation.objects.filter(pk=reservation.pk).update(status="RELEASED")
        with self.assertLogs("apps.payments.recovery", level="WARNING"):
            self.assertEqual(recover_payment_events().failed_ids, [event.pk])
        InventoryReservation.objects.filter(pk=reservation.pk).update(status="RESERVED")
        event.refresh_from_db()
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.BLOCKED)
        with patch("apps.payments.services.process_payment_event") as process:
            process_stored_payment_event(event_id=event.pk)
            self.assertEqual(recover_payment_events(event_id=event.pk).selected_ids, ())
            process.assert_not_called()

    def test_claimed_interruption_keeps_processing_and_does_not_automatically_take_over(self):
        _, attempt = self.new_attempt("claimed-interrupt")
        event = self.interrupt_after_receipt(attempt, "inbox-claimed-interrupt")
        with patch("apps.payments.services.process_payment_event", side_effect=SimulatedProcessInterruption):
            with self.assertRaises(SimulatedProcessInterruption):
                process_stored_payment_event(event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.PROCESSING)
        self.assertEqual(event.processing_attempt_count, 1)
        with patch("apps.payments.services.process_payment_event") as process:
            self.assertEqual(recover_payment_events(as_of=timezone.now() + timedelta(days=1)).selected_ids, ())
            repeated = process_stored_payment_event(event_id=event.pk)
            process.assert_not_called()
        self.assertEqual(repeated.processing_attempt_count, 1)
        self.assertFalse(InventoryMovement.objects.filter(movement_type="COMMIT").exists())

    def test_failure_recording_error_keeps_durable_claim(self):
        _, attempt = self.new_attempt("record-failure")
        event = self.interrupt_after_receipt(attempt, "inbox-record-failure")
        with patch("apps.payments.services.process_payment_event", side_effect=OperationalError(1213, "controlled deadlock")):
            with patch("apps.payments.services._record_event_processing_failure", side_effect=OperationalError(2006, "record unavailable")):
                with self.assertRaises(OperationalError):
                    process_stored_payment_event(event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.PROCESSING)
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual(recover_payment_events().selected_ids, ())

    def test_stale_claim_cannot_execute_or_overwrite_newer_processing(self):
        _, attempt = self.new_attempt("stale-claim")
        event = self.interrupt_after_receipt(attempt, "inbox-stale-claim")
        clock = timezone.now()
        _, old_version = _claim_event_processing(event_id=event.pk, as_of=clock)
        failed = _record_event_processing_failure(
            event_id=event.pk, processing_attempt=old_version,
            error=OperationalError(1213, "controlled deadlock"), as_of=clock,
        )
        _, new_version = _claim_event_processing(event_id=event.pk, as_of=failed.next_retry_at)
        with patch("apps.payments.services.process_payment_event") as process:
            stale = _process_claimed_payment_event(event_id=event.pk, processing_attempt=old_version)
            process.assert_not_called()
        self.assertEqual(stale.processing_attempt_count, 2)
        recorded = _record_event_processing_failure(
            event_id=event.pk, processing_attempt=old_version,
            error=RuntimeError("old failure"), as_of=clock,
        )
        self.assertEqual(recorded.processing_status, PaymentEvent.ProcessingStatus.PROCESSING)
        self.assertEqual(recorded.processing_attempt_count, new_version)
        completed = _process_claimed_payment_event(event_id=event.pk, processing_attempt=new_version)
        self.assertEqual(completed.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)

    def test_invalid_event_is_terminal_and_uses_only_one_processing_attempt(self):
        _, attempt = self.new_attempt("invalid-budget")
        event = self.interrupt_after_receipt(attempt, "inbox-invalid-budget")
        PaymentEvent.objects.filter(pk=event.pk).update(signature="forged")
        result = recover_payment_events(event_id=event.pk)
        self.assertEqual(result.rejected, 1)
        repeated = process_stored_payment_event(event_id=event.pk)
        self.assertEqual(repeated.processing_attempt_count, 1)
        self.assertEqual(repeated.failure_kind, PaymentEvent.FailureKind.NONE)
        self.assertEqual(repeated.error_code, "invalid_signature_or_provider")
        self.assertEqual(recover_payment_events().selected_ids, ())

    def test_entry_point_rejects_outer_transaction_before_consuming_budget(self):
        _, attempt = self.new_attempt("outer-tx")
        event = self.interrupt_after_receipt(attempt, "inbox-outer-tx")
        with transaction.atomic():
            with self.assertRaisesMessage(ValueError, "外层事务"):
                process_stored_payment_event(event_id=event.pk)
        event.refresh_from_db()
        self.assertEqual(event.processing_attempt_count, 0)
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.RECEIVED)

    def test_real_mysql_deadlock_rolls_back_business_but_keeps_claim_and_can_recover(self):
        _, first_attempt = self.new_attempt("deadlock-first")
        first_event = self.interrupt_after_receipt(first_attempt, "inbox-deadlock-first")
        _, second_attempt = self.new_attempt("deadlock-second")
        second_event = self.interrupt_after_receipt(second_attempt, "inbox-deadlock-second")
        extra_sku = SKU.objects.create(product=self.sku.product, code="DEADLOCK-EXTRA", price="1.00", is_active=True)
        extra_inventory = Inventory.objects.create(sku=extra_sku, available_stock=0)
        barrier, outcomes = Barrier(2), Queue()

        def controlled_lock_cycle(*, event_id):
            # 测试故意反转两行锁顺序，触发真实 InnoDB 死锁；不是生产锁顺序。
            lock_ids = (self.inventory.pk, extra_inventory.pk)
            if event_id == second_event.pk:
                lock_ids = tuple(reversed(lock_ids))
            Inventory.objects.select_for_update().get(pk=lock_ids[0])
            barrier.wait(timeout=10)
            Inventory.objects.select_for_update().get(pk=lock_ids[1])
            return process_payment_event(event_id=event_id)

        def run(event_id):
            close_old_connections()
            try:
                completed = process_stored_payment_event(event_id=event_id)
                outcomes.put((event_id, completed.processing_status, None))
            except OperationalError as error:
                outcomes.put((event_id, None, error.args[0]))
            except Exception as error:
                outcomes.put((event_id, None, type(error).__name__))
            finally:
                close_old_connections()

        with patch("apps.payments.services.process_payment_event", side_effect=controlled_lock_cycle):
            threads = [Thread(target=run, args=(item.pk,)) for item in (first_event, second_event)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        observed = [outcomes.get_nowait(), outcomes.get_nowait()]
        self.assertEqual(sorted(error for _, _, error in observed if error is not None), [1213])
        self.assertEqual(sum(state == "PROCESSED" for _, state, _ in observed), 1)
        failed_id = next(event_id for event_id, _, error in observed if error == 1213)
        failed = PaymentEvent.objects.get(pk=failed_id)
        self.assertEqual(failed.processing_attempt_count, 1)
        self.assertEqual(failed.failure_kind, PaymentEvent.FailureKind.TRANSIENT)
        self.assertEqual(PaymentEvent.objects.exclude(pk=failed_id).get().processing_attempt_count, 1)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        result = recover_payment_events(event_id=failed_id, as_of=failed.next_retry_at)
        self.assertEqual((result.processed, result.failed_ids), (1, []))
        failed.refresh_from_db()
        self.assertEqual(failed.processing_attempt_count, 2)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 2)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (18, 0))


class PaymentEventRetryMigrationTests(TransactionTestCase):
    def test_existing_errors_are_blocked_without_inventing_history_or_changing_terminal_events(self):
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        old_target = [("payments", "0002_refundsimulation")]
        new_target = [("payments", "0004_paymentevent_processing_lease")]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            old_event = old_apps.get_model("payments", "PaymentEvent")
            for state in ("ERROR", "RECEIVED", "PROCESSED", "REJECTED"):
                old_event.objects.create(
                    provider="mock", event_id=f"legacy-{state}", raw_payload={"legacy": state},
                    signature="saved", processing_status=state,
                    error_code="processing_error" if state == "ERROR" else "",
                )
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            migrated = PaymentEvent.objects.get(event_id="legacy-ERROR")
            self.assertEqual(migrated.failure_kind, PaymentEvent.FailureKind.BLOCKED)
            self.assertEqual(migrated.processing_attempt_count, 0)
            self.assertEqual(migrated.error_code, "processing_error")
            self.assertIsNone(migrated.next_retry_at)
            self.assertEqual(recover_payment_events(dry_run=True).selected_ids,
                             (PaymentEvent.objects.get(event_id="legacy-RECEIVED").pk,))
            for state in ("RECEIVED", "PROCESSED", "REJECTED"):
                preserved = PaymentEvent.objects.get(event_id=f"legacy-{state}")
                self.assertEqual(preserved.processing_status, state)
                self.assertEqual(preserved.failure_kind, PaymentEvent.FailureKind.NONE)
                self.assertEqual(preserved.processing_attempt_count, 0)
                self.assertEqual(preserved.raw_payload, {"legacy": state})
        finally:
            MigrationExecutor(connection).migrate(new_target)
