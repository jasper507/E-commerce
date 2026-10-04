"""C3 补投资格和命令规则；Mock publisher 不构成真实 MQ 投递/恢复证据。"""

from datetime import timedelta
from io import StringIO
from unittest.mock import call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Order
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.recovery import republish_payment_events
from apps.payments.services import process_stored_payment_event
from apps.payments.tests.test_event_recovery import PaymentEventRecoveryFixture


@override_settings(PAYMENT_EVENTS_ASYNC=True, IS_LOCAL_ENVIRONMENT=True)
class PaymentEventPublicationRecoveryTests(PaymentEventRecoveryFixture, TransactionTestCase):
    def received_event(self, key):
        order, attempt = self.new_attempt(key)
        with override_settings(PAYMENT_EVENTS_ASYNC=False):
            event = self.interrupt_after_receipt(attempt, f"inbox-{key}")
        return order, attempt, event

    def transient_event(self, key, *, as_of):
        order, attempt, event = self.received_event(key)
        with patch(
            "apps.payments.services.process_payment_event",
            side_effect=OperationalError(1213, "controlled deadlock"),
        ):
            with self.assertRaises(OperationalError):
                process_stored_payment_event(event_id=event.pk, as_of=as_of)
        event.refresh_from_db()
        return order, attempt, event

    def business_snapshot(self):
        return {
            name: list(model.objects.order_by("pk").values())
            for name, model in (
                ("events", PaymentEvent), ("attempts", PaymentAttempt), ("orders", Order),
                ("inventory", Inventory), ("reservations", InventoryReservation),
                ("movements", InventoryMovement),
            )
        }

    def assert_result(self, result, *, selected, published=(), failed=(), skipped=(), unattempted=()):
        self.assertEqual(result.selected_ids, tuple(selected))
        self.assertEqual(result.published_ids, list(published))
        self.assertEqual(result.failed_ids, list(failed))
        self.assertEqual(result.skipped_ids, list(skipped))
        self.assertEqual(result.unattempted_ids, list(unattempted))

    def test_candidates_require_fresh_zero_budget_or_due_valid_transient_plan(self):
        cutoff = timezone.now()
        past = cutoff - timedelta(seconds=1)
        future = cutoff + timedelta(microseconds=1)
        cases = (
            ("RECEIVED", "NONE", 0, None, True),
            ("ERROR", "TRANSIENT", 1, cutoff, True),
            ("ERROR", "TRANSIENT", 2, past, True),
            ("RECEIVED", "NONE", 1, None, False),
            ("RECEIVED", "TRANSIENT", 0, None, False),
            ("ERROR", "TRANSIENT", 0, past, False),
            ("ERROR", "TRANSIENT", 3, past, False),
            ("ERROR", "TRANSIENT", 1, None, False),
            ("ERROR", "TRANSIENT", 1, future, False),
            ("ERROR", "BLOCKED", 1, past, False),
            ("ERROR", "EXHAUSTED", 3, past, False),
            ("ERROR", "NONE", 1, past, False),
            ("PROCESSING", "NONE", 1, None, False),
            ("PROCESSED", "NONE", 1, None, False),
            ("REJECTED", "NONE", 1, None, False),
        )
        eligible_ids = []
        for index, (state, kind, count, due, eligible) in enumerate(cases):
            event = PaymentEvent.objects.create(
                provider="mock", event_id=f"publication-qualification-{index}",
                raw_payload={"marker": index}, processing_status=state,
                failure_kind=kind, processing_attempt_count=count, next_retry_at=due,
            )
            PaymentEvent.objects.filter(pk=event.pk).update(
                received_at=cutoff - timedelta(minutes=20 - index)
            )
            if eligible:
                eligible_ids.append(event.pk)
        before = self.business_snapshot()

        with patch("apps.payments.recovery.publish_payment_event", return_value=True) as publish:
            result = republish_payment_events(as_of=cutoff)

        self.assert_result(result, selected=eligible_ids, published=eligible_ids)
        self.assertEqual(publish.call_args_list, [call(event_id=event_id) for event_id in eligible_ids])
        self.assertEqual(self.business_snapshot(), before)

    def test_dry_run_oldest_limit_and_target_only_preview_without_writes(self):
        clock = timezone.now()
        _, _, first = self.received_event("publication-first")
        _, _, second = self.received_event("publication-second")
        _, _, third = self.received_event("publication-third")
        for event, age in ((first, 2), (second, 3), (third, 1)):
            PaymentEvent.objects.filter(pk=event.pk).update(received_at=clock - timedelta(minutes=age))
        before = self.business_snapshot()

        with patch("apps.payments.recovery.publish_payment_event") as publish:
            with patch("apps.payments.recovery.process_stored_payment_event") as process:
                preview = republish_payment_events(limit=2, dry_run=True, as_of=clock)
                targeted = republish_payment_events(event_id=third.pk, limit=1, dry_run=True, as_of=clock)
                missing = republish_payment_events(event_id=999999, dry_run=True, as_of=clock)

        self.assert_result(preview, selected=(second.pk, first.pk))
        self.assert_result(targeted, selected=(third.pk,))
        self.assert_result(missing, selected=())
        publish.assert_not_called()
        process.assert_not_called()
        self.assertEqual(self.business_snapshot(), before)

    def test_publication_is_outside_transaction_only_ids_and_batch_is_fixed(self):
        _, _, first = self.received_event("publication-fixed-first")
        _, _, second = self.received_event("publication-fixed-second")
        original_rows = list(PaymentEvent.objects.order_by("pk").values())
        inserted_ids = []

        def observe(*, event_id):
            self.assertFalse(connection.in_atomic_block)
            self.assertIs(type(event_id), int)
            if event_id == first.pk:
                late = PaymentEvent.objects.create(
                    provider="mock", event_id="publication-added-after-snapshot",
                    raw_payload={"marker": "late"},
                )
                inserted_ids.append(late.pk)
            return True

        with patch("apps.payments.recovery.publish_payment_event", side_effect=observe) as publish:
            with patch("apps.payments.recovery.process_stored_payment_event") as process:
                result = republish_payment_events(limit=2)

        self.assert_result(result, selected=(first.pk, second.pk), published=(first.pk, second.pk))
        self.assertEqual(publish.call_args_list, [call(event_id=first.pk), call(event_id=second.pk)])
        process.assert_not_called()
        self.assertEqual(
            list(PaymentEvent.objects.exclude(pk__in=inserted_ids).order_by("pk").values()), original_rows
        )
        self.assertEqual(len(inserted_ids), 1)

    def test_first_broker_false_stops_remaining_publication_and_keeps_all_business_data(self):
        events = [self.received_event(f"publication-broker-{index}")[2] for index in range(3)]
        event_ids = tuple(event.pk for event in events)
        before = self.business_snapshot()
        with patch("apps.payments.recovery.publish_payment_event", side_effect=(True, False)) as publish:
            with patch("apps.payments.recovery.process_stored_payment_event") as process:
                result = republish_payment_events()

        self.assert_result(
            result, selected=event_ids, published=event_ids[:1], failed=event_ids[1:2],
            unattempted=event_ids[2:],
        )
        self.assertEqual(publish.call_args_list, [call(event_id=event_ids[0]), call(event_id=event_ids[1])])
        process.assert_not_called()
        self.assertEqual(self.business_snapshot(), before)

    def test_fresh_recheck_skips_committed_terminal_and_retry_postponed_after_snapshot(self):
        # 受控顺序交错检验 fresh read；不是实际线程并发或 Worker 中断实验。
        cutoff = timezone.now()
        _, _, first = self.received_event("publication-race-first")
        completed_order, _, completed = self.received_event("publication-race-terminal")
        pending_order, _, deferred = self.transient_event(
            "publication-race-deferred", as_of=cutoff - timedelta(seconds=5)
        )
        self.assertEqual(deferred.next_retry_at, cutoff)

        def another_handler_changes_remaining(*, event_id):
            self.assertEqual(event_id, first.pk)
            process_stored_payment_event(event_id=completed.pk)
            with patch(
                "apps.payments.services.process_payment_event",
                side_effect=OperationalError(1213, "controlled next-attempt failure"),
            ):
                with self.assertRaises(OperationalError):
                    process_stored_payment_event(event_id=deferred.pk, as_of=cutoff)
            return True

        with patch("apps.payments.recovery.publish_payment_event", side_effect=another_handler_changes_remaining) as publish:
            result = republish_payment_events(as_of=cutoff)

        self.assert_result(
            result, selected=(first.pk, completed.pk, deferred.pk), published=(first.pk,),
            skipped=(completed.pk, deferred.pk),
        )
        publish.assert_called_once_with(event_id=first.pk)
        first.refresh_from_db()
        completed.refresh_from_db()
        deferred.refresh_from_db()
        completed_order.refresh_from_db()
        pending_order.refresh_from_db()
        self.assertEqual((first.processing_status, first.processing_attempt_count), ("RECEIVED", 0))
        self.assertEqual((completed.processing_status, completed.processing_attempt_count), ("PROCESSED", 1))
        self.assertEqual(completed_order.status, "PAID")
        self.assertEqual((deferred.processing_status, deferred.failure_kind, deferred.processing_attempt_count),
                         ("ERROR", "TRANSIENT", 2))
        self.assertEqual(deferred.next_retry_at, cutoff + timedelta(seconds=10))
        self.assertEqual(pending_order.status, "PENDING_PAYMENT")
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)

    def test_multiple_scans_can_republish_without_claiming_budget_or_processing(self):
        cutoff = timezone.now()
        _, _, fresh = self.received_event("publication-repeat-fresh")
        _, _, retry = self.transient_event("publication-repeat-retry", as_of=cutoff - timedelta(seconds=5))
        before = self.business_snapshot()
        event_ids = (fresh.pk, retry.pk)
        with patch("apps.payments.recovery.publish_payment_event", return_value=True) as publish:
            with patch("apps.payments.recovery.process_stored_payment_event") as process:
                first = republish_payment_events(as_of=cutoff)
                second = republish_payment_events(as_of=cutoff)

        self.assert_result(first, selected=event_ids, published=event_ids)
        self.assert_result(second, selected=event_ids, published=event_ids)
        self.assertEqual(publish.call_args_list, [call(event_id=event_id) for event_id in event_ids] * 2)
        process.assert_not_called()
        self.assertEqual(self.business_snapshot(), before)

    def test_service_refuses_outer_transaction_and_invalid_batch_parameters(self):
        with patch("apps.payments.recovery.PaymentEvent.objects.filter") as query:
            with patch("apps.payments.recovery.publish_payment_event") as publish:
                with transaction.atomic():
                    with self.assertRaises(ValueError):
                        republish_payment_events()
                for options in ({"limit": 0}, {"limit": -1}, {"event_id": 0}, {"event_id": -1}):
                    with self.subTest(options=options):
                        with self.assertRaises(ValueError):
                            republish_payment_events(**options)
        query.assert_not_called()
        publish.assert_not_called()

    def test_publish_command_requires_async_and_local_even_for_dry_run(self):
        with patch("apps.payments.recovery.PaymentEvent.objects.filter") as query:
            with patch("apps.payments.recovery.publish_payment_event") as publish:
                for async_enabled, local in ((False, True), (True, False)):
                    for dry_run in (False, True):
                        with self.subTest(async_enabled=async_enabled, local=local, dry_run=dry_run):
                            with override_settings(PAYMENT_EVENTS_ASYNC=async_enabled, IS_LOCAL_ENVIRONMENT=local):
                                with self.assertRaises(CommandError):
                                    call_command("recover_payment_events", publish=True, dry_run=dry_run)
        query.assert_not_called()
        publish.assert_not_called()

    def test_publish_command_reports_publication_preview_and_partial_failure_without_processing(self):
        _, _, first = self.received_event("publication-command-first")
        _, _, second = self.received_event("publication-command-second")
        before = self.business_snapshot()
        preview = StringIO()
        with patch("apps.payments.recovery.publish_payment_event") as publish:
            call_command("recover_payment_events", "--publish", "--dry-run", stdout=preview)
        publish.assert_not_called()
        self.assertIn("dry_run=true", preview.getvalue())
        self.assertIn("selected=2", preview.getvalue())

        success = StringIO()
        with patch("apps.payments.recovery.publish_payment_event", return_value=True) as publish:
            call_command("recover_payment_events", "--publish", "--event-id", str(second.pk), stdout=success)
        publish.assert_called_once_with(event_id=second.pk)
        self.assertIn("selected=1 published=1 errors=0 skipped=0 unattempted=0", success.getvalue())
        self.assertNotIn("processed=", success.getvalue())

        output, errors = StringIO(), StringIO()
        with patch("apps.payments.recovery.publish_payment_event", return_value=False) as publish:
            with self.assertRaises(CommandError):
                call_command("recover_payment_events", "--publish", stdout=output, stderr=errors)
        publish.assert_called_once_with(event_id=first.pk)
        self.assertIn("selected=2 published=0 errors=1 skipped=0 unattempted=1", output.getvalue())
        self.assertNotIn("processed=", output.getvalue())
        self.assertIn(str(first.pk), errors.getvalue())
        self.assertNotIn(first.event_id, output.getvalue() + errors.getvalue())
        self.assertEqual(self.business_snapshot(), before)

    def test_default_command_still_processes_synchronously_without_publication(self):
        order, attempt, event = self.received_event("publication-default-sync")
        output = StringIO()
        with patch("apps.payments.recovery.publish_payment_event") as publish:
            call_command("recover_payment_events", event_id=event.pk, stdout=output)
        publish.assert_not_called()
        self.assertIn("selected=1 processed=1 rejected=0 errors=0", output.getvalue())
        self.assertNotIn("published=", output.getvalue())
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("PROCESSED", 1))
        self.assertEqual((order.status, attempt.status), ("PAID", "SUCCEEDED"))
        self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
