"""C4 租约、版本与事务 Oracle；线程行锁测试不代替真实 Worker kill/ACK 证据。"""

from datetime import timedelta
from io import StringIO
from queue import Queue
from threading import Event as ThreadEvent, Thread
from time import monotonic
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.payments.models import PaymentEvent
from apps.payments.recovery import recover_stale_payment_events
from apps.payments.services import (
    _claim_event_processing,
    _process_claimed_payment_event,
    _record_event_processing_failure,
    process_payment_event,
    process_stored_payment_event,
    release_stale_payment_event,
)
from apps.payments.tests.test_event_recovery import PaymentEventRecoveryFixture


@override_settings(PAYMENT_EVENTS_ASYNC=False, PAYMENT_EVENT_PROCESSING_LEASE_SECONDS=60)
class PaymentEventProcessingLeaseTests(PaymentEventRecoveryFixture, TransactionTestCase):
    def received_event(self, key):
        order, attempt = self.new_attempt(key)
        event = self.interrupt_after_receipt(attempt, f"inbox-{key}")
        return order, attempt, event

    def claimed_event(self, key, *, started_at):
        order, attempt, event = self.received_event(key)
        with patch("apps.payments.services.timezone.now", return_value=started_at):
            claimed, version = _claim_event_processing(event_id=event.pk, as_of=started_at)
        self.assertEqual(version, 1)
        return order, attempt, claimed

    def assert_pending(self, order, attempt):
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual((order.status, attempt.status), ("PENDING_PAYMENT", "CREATED"))
        self.assertEqual(InventoryReservation.objects.get(order_item__order=order).status, "RESERVED")
        self.assertFalse(InventoryMovement.objects.filter(
            reservation__order_item__order=order, movement_type="COMMIT"
        ).exists())

    def test_claim_lease_starts_at_actual_claim_clock_and_duplicate_keeps_same_owner(self):
        _, _, event = self.received_event("lease-clock")
        actual_clock = timezone.now()
        old_cutoff = actual_clock - timedelta(seconds=30)
        with override_settings(PAYMENT_EVENT_PROCESSING_LEASE_SECONDS=7):
            with patch("apps.payments.services.timezone.now", return_value=actual_clock):
                claimed, version = _claim_event_processing(event_id=event.pk, as_of=old_cutoff)
        self.assertEqual((claimed.processing_status, version), ("PROCESSING", 1))
        self.assertEqual(claimed.processing_started_at, actual_clock)
        self.assertEqual(claimed.processing_lease_until, actual_clock + timedelta(seconds=7))
        before = PaymentEvent.objects.filter(pk=event.pk).values().get()

        with patch("apps.payments.services.timezone.now", return_value=actual_clock + timedelta(seconds=1)):
            repeated, repeated_version = _claim_event_processing(event_id=event.pk, as_of=actual_clock)
        self.assertIsNone(repeated_version)
        self.assertEqual(repeated.processing_attempt_count, 1)
        self.assertEqual(PaymentEvent.objects.filter(pk=event.pk).values().get(), before)

    def test_success_rejection_and_failures_clear_deadline_but_preserve_start_audit(self):
        clock = timezone.now()
        cases = (
            ("success", None, "PROCESSED", "NONE"),
            ("invalid", None, "REJECTED", "NONE"),
            ("transient", OperationalError(1213, "controlled deadlock"), "ERROR", "TRANSIENT"),
            ("blocked", RuntimeError("controlled code failure"), "ERROR", "BLOCKED"),
        )
        for key, error, state, kind in cases:
            with self.subTest(key=key):
                order, attempt, event = self.received_event(f"lease-clear-{key}")
                if key == "invalid":
                    PaymentEvent.objects.filter(pk=event.pk).update(signature="forged")
                with patch("apps.payments.services.timezone.now", return_value=clock):
                    if error is None:
                        process_stored_payment_event(event_id=event.pk, as_of=clock)
                    else:
                        with patch("apps.payments.services.process_payment_event", side_effect=error):
                            with self.assertRaises(type(error)):
                                process_stored_payment_event(event_id=event.pk, as_of=clock)
                event.refresh_from_db()
                self.assertEqual((event.processing_status, event.failure_kind, event.processing_attempt_count),
                                 (state, kind, 1))
                self.assertEqual(event.processing_started_at, clock)
                self.assertIsNone(event.processing_lease_until)
                if key == "success":
                    order.refresh_from_db()
                    self.assertEqual(order.status, "PAID")
                else:
                    self.assert_pending(order, attempt)

    def test_strict_due_candidates_preview_limit_target_and_invalid_metadata_are_unchanged(self):
        cutoff = timezone.now()
        started = cutoff - timedelta(seconds=60)
        cases = (
            ("PROCESSING", 1, started, cutoff, True),
            ("PROCESSING", 2, started, cutoff - timedelta(seconds=1), True),
            ("PROCESSING", 3, started, cutoff, True),
            ("PROCESSING", 1, started, cutoff + timedelta(microseconds=1), False),
            ("PROCESSING", 1, None, cutoff, False),
            ("PROCESSING", 1, started, None, False),
            ("PROCESSING", 0, started, cutoff, False),
            ("PROCESSING", 4, started, cutoff, False),
            ("PROCESSING", 1, cutoff + timedelta(microseconds=1), cutoff, False),
            ("RECEIVED", 1, started, cutoff, False),
            ("ERROR", 1, started, cutoff, False),
            ("PROCESSED", 1, started, cutoff, False),
            ("REJECTED", 1, started, cutoff, False),
        )
        eligible, invalid = [], []
        for index, (state, count, start, deadline, expected) in enumerate(cases):
            event = PaymentEvent.objects.create(
                provider="mock", event_id=f"lease-qualification-{index}", raw_payload={"marker": index},
                processing_status=state, processing_attempt_count=count,
                processing_started_at=start, processing_lease_until=deadline,
            )
            PaymentEvent.objects.filter(pk=event.pk).update(
                received_at=cutoff - timedelta(minutes=20 - index)
            )
            (eligible if expected else invalid).append(event)
        before = list(PaymentEvent.objects.order_by("pk").values())

        with patch("apps.payments.recovery.release_stale_payment_event") as release:
            with patch("apps.payments.recovery.publish_payment_event") as publish:
                preview = recover_stale_payment_events(limit=2, dry_run=True, as_of=cutoff)
                targeted = recover_stale_payment_events(event_id=eligible[2].pk, dry_run=True, as_of=cutoff)
                all_candidates = recover_stale_payment_events(dry_run=True, as_of=cutoff)
        release.assert_not_called()
        publish.assert_not_called()
        self.assertEqual(preview.selected_ids, tuple(event.pk for event in eligible[:2]))
        self.assertEqual(targeted.selected_ids, (eligible[2].pk,))
        self.assertEqual(all_candidates.selected_ids, tuple(event.pk for event in eligible))
        self.assertEqual((preview.retryable_ids, preview.exhausted_ids, preview.skipped_ids), ([], [], []))

        for event in invalid:
            with self.subTest(state=event.processing_status, count=event.processing_attempt_count):
                self.assertIsNone(release_stale_payment_event(
                    event_id=event.pk, processing_attempt=event.processing_attempt_count,
                    lease_until=event.processing_lease_until, as_of=cutoff,
                ))
        self.assertEqual(list(PaymentEvent.objects.order_by("pk").values()), before)

    def test_expiration_preserves_budget_and_third_claim_exhausts_without_fourth(self):
        clock = timezone.now()
        order, attempt, event = self.claimed_event("lease-budget", started_at=clock)
        original_payload = event.raw_payload
        for count in (1, 2, 3):
            with self.subTest(count=count):
                deadline = event.processing_lease_until
                started = event.processing_started_at
                with patch("apps.payments.recovery.publish_payment_event") as publish:
                    result = recover_stale_payment_events(event_id=event.pk, as_of=deadline)
                    repeated = recover_stale_payment_events(event_id=event.pk, as_of=deadline)
                publish.assert_not_called()
                self.assertEqual(result.selected_ids, (event.pk,))
                self.assertEqual(result.retryable_ids, [event.pk] if count < 3 else [])
                self.assertEqual(result.exhausted_ids, [event.pk] if count == 3 else [])
                self.assertEqual(repeated.selected_ids, ())
                event.refresh_from_db()
                self.assertEqual(event.processing_attempt_count, count)
                self.assertEqual(event.processing_status, "ERROR")
                self.assertEqual(event.error_code, "processing_lease_expired")
                self.assertEqual(event.processing_started_at, started)
                self.assertEqual(event.processed_at, deadline)
                self.assertIsNone(event.processing_lease_until)
                self.assertEqual(event.next_retry_at, deadline if count < 3 else None)
                self.assertEqual(event.raw_payload, original_payload)
                self.assert_pending(order, attempt)
                if count < 3:
                    with patch("apps.payments.services.timezone.now", return_value=deadline):
                        event, version = _claim_event_processing(event_id=event.pk, as_of=deadline)
                    self.assertEqual(version, count + 1)

        before = PaymentEvent.objects.filter(pk=event.pk).values().get()
        with patch("apps.payments.services.process_payment_event") as process:
            skipped = process_stored_payment_event(event_id=event.pk, as_of=clock + timedelta(days=1))
        process.assert_not_called()
        self.assertEqual((skipped.failure_kind, skipped.processing_attempt_count), ("EXHAUSTED", 3))
        self.assertEqual(PaymentEvent.objects.filter(pk=event.pk).values().get(), before)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.available_stock, self.inventory.reserved_stock), (19, 1))

    def test_released_old_version_cannot_execute_record_failure_or_release_new_owner(self):
        clock = timezone.now()
        order, _, old = self.claimed_event("lease-version", started_at=clock)
        old_deadline = old.processing_lease_until
        released = release_stale_payment_event(
            event_id=old.pk, processing_attempt=1, lease_until=old_deadline, as_of=old_deadline
        )
        self.assertEqual(released.failure_kind, "TRANSIENT")
        with patch("apps.payments.services.timezone.now", return_value=old_deadline):
            current, version = _claim_event_processing(event_id=old.pk, as_of=old_deadline)
        self.assertEqual(version, 2)
        before = PaymentEvent.objects.filter(pk=old.pk).values().get()

        with patch("apps.payments.services.process_payment_event") as process:
            _process_claimed_payment_event(event_id=old.pk, processing_attempt=1)
        process.assert_not_called()
        _record_event_processing_failure(
            event_id=old.pk, processing_attempt=1, error=RuntimeError("controlled obsolete failure"),
            as_of=current.processing_lease_until,
        )
        self.assertIsNone(release_stale_payment_event(
            event_id=old.pk, processing_attempt=1, lease_until=old_deadline,
            as_of=current.processing_lease_until,
        ))
        self.assertEqual(PaymentEvent.objects.filter(pk=old.pk).values().get(), before)

        completed = _process_claimed_payment_event(event_id=old.pk, processing_attempt=version)
        self.assertEqual((completed.processing_status, completed.processing_attempt_count), ("PROCESSED", 2))
        self.assertIsNone(completed.processing_lease_until)
        terminal = PaymentEvent.objects.filter(pk=old.pk).values().get()
        self.assertIsNone(release_stale_payment_event(
            event_id=old.pk, processing_attempt=version, lease_until=current.processing_lease_until,
            as_of=current.processing_lease_until,
        ))
        self.assertEqual(PaymentEvent.objects.filter(pk=old.pk).values().get(), terminal)
        order.refresh_from_db()
        self.assertEqual(order.status, "PAID")
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)

    def test_snapshot_deadline_change_and_duplicate_repair_are_skipped_not_counted_twice(self):
        cutoff = timezone.now()
        _, _, first = self.claimed_event("lease-snapshot-first", started_at=cutoff - timedelta(seconds=65))
        _, _, second = self.claimed_event("lease-snapshot-second", started_at=cutoff - timedelta(seconds=65))
        original_release = release_stale_payment_event
        renewed_deadline = cutoff - timedelta(seconds=1)

        def change_after_snapshot(**options):
            self.assertFalse(connection.in_atomic_block)
            if options["event_id"] == first.pk:
                # 另一个扫描先修复同一事件，本扫描不能把它重复计入 retryable。
                original_release(**options)
                PaymentEvent.objects.filter(pk=second.pk).update(
                    processing_started_at=cutoff - timedelta(seconds=30),
                    processing_lease_until=renewed_deadline,
                )
            return original_release(**options)

        with patch("apps.payments.recovery.release_stale_payment_event", side_effect=change_after_snapshot):
            result = recover_stale_payment_events(as_of=cutoff)
        self.assertEqual(result.selected_ids, (first.pk, second.pk))
        self.assertEqual((result.retryable_ids, result.exhausted_ids), ([], []))
        self.assertEqual(result.skipped_ids, [first.pk, second.pk])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.processing_status, first.processing_attempt_count), ("ERROR", 1))
        self.assertEqual((second.processing_status, second.processing_attempt_count), ("PROCESSING", 1))
        self.assertEqual(second.processing_lease_until, renewed_deadline)

    def test_expired_slow_business_owner_holds_lock_so_scan_skips_then_owner_commits_once(self):
        cutoff = timezone.now()
        order, _, event = self.claimed_event("lease-held-owner", started_at=cutoff - timedelta(seconds=61))
        owner_locked, allow_finish = ThreadEvent(), ThreadEvent()
        outcomes = Queue()
        original_process = process_payment_event

        def slow_process(*, event_id):
            owner_locked.set()
            if not allow_finish.wait(timeout=5):
                raise RuntimeError("controlled owner wait timed out")
            return original_process(event_id=event_id)

        def owner():
            close_old_connections()
            try:
                completed = _process_claimed_payment_event(event_id=event.pk, processing_attempt=1)
                outcomes.put((completed.processing_status, None))
            except Exception as error:
                outcomes.put((None, error))
            finally:
                close_old_connections()

        with patch("apps.payments.services.process_payment_event", side_effect=slow_process):
            thread = Thread(target=owner)
            thread.start()
            try:
                self.assertTrue(owner_locked.wait(timeout=5))
                started = monotonic()
                result = recover_stale_payment_events(event_id=event.pk, as_of=cutoff)
                elapsed = monotonic() - started
            finally:
                allow_finish.set()
                thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        status, error = outcomes.get_nowait()
        self.assertIsNone(error)
        self.assertEqual(status, "PROCESSED")
        self.assertLess(elapsed, 1.0)
        self.assertEqual(result.selected_ids, (event.pk,))
        self.assertEqual((result.retryable_ids, result.exhausted_ids, result.skipped_ids), ([], [], [event.pk]))
        event.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("PROCESSED", 1))
        self.assertEqual(order.status, "PAID")
        self.assertIsNone(event.processing_lease_until)
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
        self.assertEqual(recover_stale_payment_events(event_id=event.pk, as_of=cutoff).selected_ids, ())

    def test_stale_command_repairs_without_async_or_publication_and_default_still_processes(self):
        cutoff = timezone.now()
        order, attempt, event = self.claimed_event("lease-command", started_at=cutoff - timedelta(seconds=60))
        before = PaymentEvent.objects.filter(pk=event.pk).values().get()
        preview, repaired, processed = StringIO(), StringIO(), StringIO()
        with override_settings(IS_LOCAL_ENVIRONMENT=True, PAYMENT_EVENTS_ASYNC=False):
            with patch("apps.payments.recovery.timezone.now", return_value=cutoff):
                with patch("apps.payments.recovery.publish_payment_event") as publish:
                    call_command("recover_payment_events", "--recover-stale", "--dry-run",
                                 event_id=event.pk, stdout=preview)
                    self.assertIn("dry_run=true selected=1", preview.getvalue())
                    self.assertEqual(PaymentEvent.objects.filter(pk=event.pk).values().get(), before)
                    call_command("recover_payment_events", "--recover-stale", event_id=event.pk, stdout=repaired)
                    self.assertIn("selected=1 retryable=1 exhausted=0 skipped=0", repaired.getvalue())
                    self.assertNotIn("processed=", repaired.getvalue())
                    self.assertNotIn("published=", repaired.getvalue())
                    self.assert_pending(order, attempt)
                    call_command("recover_payment_events", event_id=event.pk, stdout=processed)
        publish.assert_not_called()
        self.assertIn("selected=1 processed=1 rejected=0 errors=0", processed.getvalue())
        event.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("PROCESSED", 2))
        self.assertEqual(order.status, "PAID")
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)

    def test_stale_command_mode_environment_and_service_parameter_guards(self):
        with patch("apps.payments.recovery.PaymentEvent.objects.filter") as query:
            with patch("apps.payments.recovery.publish_payment_event") as publish:
                with self.assertRaises(CommandError):
                    call_command("recover_payment_events", "--publish", "--recover-stale")
                with self.assertRaises(CommandError):
                    call_command("recover_payment_events", publish=True, recover_stale=True)
                with override_settings(IS_LOCAL_ENVIRONMENT=False):
                    with self.assertRaises(CommandError):
                        call_command("recover_payment_events", "--recover-stale", dry_run=True)
                for options in ({"limit": 0}, {"limit": True}, {"event_id": 0}, {"event_id": True}):
                    with self.subTest(options=options):
                        with self.assertRaises(ValueError):
                            recover_stale_payment_events(**options)
                with transaction.atomic():
                    with self.assertRaises(ValueError):
                        recover_stale_payment_events()
        query.assert_not_called()
        publish.assert_not_called()


class PaymentEventProcessingLeaseMigrationTests(TransactionTestCase):
    def test_legacy_processing_has_no_fabricated_lease_or_automatic_repair(self):
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        old_target = [("payments", "0003_paymentevent_retry_policy")]
        new_target = [("payments", "0004_paymentevent_processing_lease")]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old_target)
            old_apps = executor.loader.project_state(old_target).apps
            old_event = old_apps.get_model("payments", "PaymentEvent")
            for state in ("PROCESSING", "PROCESSED", "ERROR"):
                old_event.objects.create(
                    provider="mock", event_id=f"legacy-lease-{state}", raw_payload={"legacy": state},
                    processing_status=state, processing_attempt_count=1,
                    failure_kind="BLOCKED" if state == "ERROR" else "NONE",
                )
            MigrationExecutor(connection).migrate(new_target)
            for state in ("PROCESSING", "PROCESSED", "ERROR"):
                preserved = PaymentEvent.objects.get(event_id=f"legacy-lease-{state}")
                self.assertEqual((preserved.processing_status, preserved.processing_attempt_count), (state, 1))
                self.assertEqual(preserved.raw_payload, {"legacy": state})
                self.assertIsNone(preserved.processing_started_at)
                self.assertIsNone(preserved.processing_lease_until)
            with patch("apps.payments.recovery.release_stale_payment_event") as release:
                result = recover_stale_payment_events(as_of=timezone.now() + timedelta(days=1))
            release.assert_not_called()
            self.assertEqual(result.selected_ids, ())
        finally:
            MigrationExecutor(connection).migrate(new_target)
