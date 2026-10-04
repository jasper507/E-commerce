"""支付恢复 Oracle：观察原事件、订单和库存，不用重新付款或重新入箱。"""

from decimal import Decimal
from io import StringIO
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, close_old_connections, transaction
from django.test import TransactionTestCase, override_settings

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import Cart, CartItem, Order
from apps.ordering.services import create_order_from_cart
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent
from apps.payments.recovery import recover_payment_events
from apps.payments.services import (
    _record_event_processing_failure,
    create_payment_attempt,
    process_payment_event,
    process_stored_payment_event,
    receive_payment_event,
)


class SimulatedProcessInterruption(BaseException):
    """受控中断处理入口；不是实际操作系统进程或 Worker 的终止实验。"""


class PaymentEventRecoveryFixture:
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="inbox_customer")
        self.address = Address.objects.create(
            user=self.user,
            recipient_name="测试用户",
            phone="13800138000",
            province="广东省",
            city="深圳市",
            district="南山区",
            detail="科技园 1 号",
        )
        category = Category.objects.create(name="鼠标", slug="inbox-mice", is_active=True)
        product = Product.objects.create(
            category=category, name="恢复测试鼠标", slug="inbox-mouse", is_active=True
        )
        self.sku = SKU.objects.create(
            product=product, code="INBOX-MOUSE", price=Decimal("189.00"), is_active=True
        )
        self.inventory = Inventory.objects.create(sku=self.sku, available_stock=20)
        self.cart = Cart.objects.create(user=self.user)

    def new_attempt(self, key):
        CartItem.objects.create(cart=self.cart, sku=self.sku, quantity=1)
        order, _ = create_order_from_cart(
            user=self.user, address_id=self.address.pk, idempotency_key=f"order-{key}"
        )
        attempt, _ = create_payment_attempt(
            user=self.user, order_public_id=order.public_id, idempotency_key=f"pay-{key}"
        )
        return order, attempt

    def payload(self, attempt, event_id):
        return {
            "provider": "mock",
            "event_id": event_id,
            "provider_reference": str(attempt.provider_reference),
            "order_public_id": str(attempt.order.public_id),
            "amount": str(attempt.amount),
            "currency": attempt.currency,
            "status": "SUCCEEDED",
        }

    def interrupt_after_receipt(self, attempt, event_id):
        payload = self.payload(attempt, event_id)
        with patch(
            "apps.payments.services.process_stored_payment_event",
            side_effect=SimulatedProcessInterruption,
        ):
            with self.assertRaises(SimulatedProcessInterruption):
                receive_payment_event(payload=payload, signature=sign_payload(payload))
        return PaymentEvent.objects.get(event_id=event_id)


class PaymentEventRecoveryTests(PaymentEventRecoveryFixture, TransactionTestCase):
    def test_interruption_after_inbox_commit_leaves_received_event(self):
        order, attempt = self.new_attempt("baseline")

        event = self.interrupt_after_receipt(attempt, "inbox-baseline")

        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.RECEIVED)
        self.assertEqual(event.raw_payload, self.payload(attempt, "inbox-baseline"))
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(attempt.status, PaymentAttempt.Status.CREATED)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (19, 1)
        )
        self.assertEqual(
            InventoryReservation.objects.get(order_item__order=order).status,
            InventoryReservation.Status.RESERVED,
        )
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT
            ).exists()
        )

    def test_received_event_recovers_without_repost_and_repeat_scan_is_empty(self):
        order, attempt = self.new_attempt("received")
        event = self.interrupt_after_receipt(attempt, "inbox-received")
        original_payload = event.raw_payload

        with patch("apps.payments.gateway.MockGateway.create_payment") as create_payment:
            result = recover_payment_events()
            repeated = recover_payment_events()

        create_payment.assert_not_called()
        self.assertEqual(result.selected_ids, (event.pk,))
        self.assertEqual((result.processed, result.rejected, result.failed_ids), (1, 0, []))
        self.assertEqual(repeated.selected_ids, ())
        event.refresh_from_db()
        attempt.refresh_from_db()
        order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(event.raw_payload, original_payload)
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (19, 0)
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT
            ).count(), 1
        )

    def test_error_event_recovers_from_its_saved_payload(self):
        order, attempt = self.new_attempt("error")
        payload = self.payload(attempt, "inbox-error")
        with patch(
            "apps.payments.services.InventoryMovement.objects.create",
            side_effect=OperationalError(1213, "controlled deadlock"),
        ):
            with self.assertRaises(OperationalError):
                receive_payment_event(payload=payload, signature=sign_payload(payload))
        event = PaymentEvent.objects.get(event_id="inbox-error")
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.ERROR)
        order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.inventory.reserved_stock, 1)

        result = recover_payment_events(event_id=event.pk, as_of=event.next_retry_at)

        self.assertEqual(result.processed, 1)
        event.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(event.error_code, "")
        self.assertEqual(order.status, Order.Status.PAID)

    def test_first_failure_does_not_roll_back_later_event_or_retry_in_same_batch(self):
        failed_order, failed_attempt = self.new_attempt("first-failure")
        failed_event = self.interrupt_after_receipt(failed_attempt, "inbox-first-failure")
        good_order, good_attempt = self.new_attempt("after-failure")
        good_event = self.interrupt_after_receipt(good_attempt, "inbox-after-failure")
        original_process = process_payment_event
        visited = []

        def fail_first(*, event_id):
            visited.append(event_id)
            if event_id == failed_event.pk:
                raise RuntimeError("controlled persistent failure")
            return original_process(event_id=event_id)

        with patch("apps.payments.services.process_payment_event", side_effect=fail_first):
            with self.assertLogs("apps.payments.recovery", level="WARNING") as logs:
                result = recover_payment_events()

        self.assertEqual(visited, [failed_event.pk, good_event.pk])
        self.assertEqual(result.failed_ids, [failed_event.pk])
        self.assertEqual(result.processed, 1)
        self.assertNotIn("controlled persistent failure", " ".join(logs.output))
        failed_event.refresh_from_db()
        failed_order.refresh_from_db()
        good_event.refresh_from_db()
        good_order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(failed_event.processing_status, PaymentEvent.ProcessingStatus.ERROR)
        self.assertEqual(failed_order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(good_event.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(good_order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (18, 1)
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT
            ).count(), 1
        )

    def test_invalid_received_event_is_rejected_and_not_selected_again(self):
        order, attempt = self.new_attempt("invalid")
        event = self.interrupt_after_receipt(attempt, "inbox-invalid")
        PaymentEvent.objects.filter(pk=event.pk).update(signature="forged")

        result = recover_payment_events()

        event.refresh_from_db()
        order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual((result.processed, result.rejected, result.failed_ids), (0, 1, []))
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.REJECTED)
        self.assertEqual(event.error_code, "invalid_signature_or_provider")
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.inventory.reserved_stock, 1)
        self.assertEqual(recover_payment_events(event_id=event.pk).selected_ids, ())

    def test_inventory_invariant_failure_stays_visible_without_business_changes(self):
        order, attempt = self.new_attempt("invariant")
        event = self.interrupt_after_receipt(attempt, "inbox-invariant")
        InventoryReservation.objects.filter(order_item__order=order).update(
            status=InventoryReservation.Status.RELEASED
        )

        with self.assertLogs("apps.payments.recovery", level="WARNING"):
            result = recover_payment_events(event_id=event.pk)

        self.assertEqual(result.failed_ids, [event.pk])
        event.refresh_from_db()
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.ERROR)
        self.assertEqual(event.error_code, "payment_invariant_error")
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.BLOCKED)
        self.assertEqual(recover_payment_events(event_id=event.pk).selected_ids, ())
        self.assertEqual(order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(attempt.status, PaymentAttempt.Status.CREATED)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (19, 1)
        )
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT
            ).exists()
        )

    def test_error_recording_preserves_terminal_state_committed_by_another_handler(self):
        # 受控交错覆盖错误留存分支，不把它当作真正 Worker 中断或线程并发实验。
        for signature in ("valid", "forged"):
            with self.subTest(signature=signature):
                _, attempt = self.new_attempt(f"terminal-{signature}")
                event = self.interrupt_after_receipt(attempt, f"inbox-terminal-{signature}")
                if signature == "forged":
                    PaymentEvent.objects.filter(pk=event.pk).update(signature="forged")
                terminal = []

                def complete_then_record(**options):
                    event_id = options["event_id"]
                    completed = process_payment_event(event_id=event_id)
                    terminal.append(
                        (completed.processing_status, completed.error_code, completed.processed_at)
                    )
                    return _record_event_processing_failure(**options)

                with patch(
                    "apps.payments.services._record_event_processing_failure",
                    side_effect=complete_then_record,
                ):
                    with patch(
                        "apps.payments.services._process_claimed_payment_event",
                        side_effect=RuntimeError("controlled business failure"),
                    ):
                        with self.assertRaises(RuntimeError):
                            process_stored_payment_event(event_id=event.pk)

                event.refresh_from_db()
                self.assertEqual(
                    (event.processing_status, event.error_code, event.processed_at), terminal[0]
                )

    def test_dry_run_and_limit_and_internal_id_filter(self):
        _, first_attempt = self.new_attempt("first-limit")
        first = self.interrupt_after_receipt(first_attempt, "inbox-first-limit")
        _, second_attempt = self.new_attempt("second-limit")
        second = self.interrupt_after_receipt(second_attempt, "inbox-second-limit")

        with patch("apps.payments.recovery.process_stored_payment_event") as process:
            preview = recover_payment_events(limit=1, dry_run=True)
            selected = recover_payment_events(event_id=second.pk, dry_run=True)
        process.assert_not_called()
        self.assertEqual(preview.selected_ids, (first.pk,))
        self.assertEqual(selected.selected_ids, (second.pk,))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.processing_status, PaymentEvent.ProcessingStatus.RECEIVED)
        self.assertEqual(second.processing_status, PaymentEvent.ProcessingStatus.RECEIVED)

        self.assertEqual(recover_payment_events(limit=1).selected_ids, (first.pk,))
        second.refresh_from_db()
        self.assertEqual(second.processing_status, PaymentEvent.ProcessingStatus.RECEIVED)
        self.assertEqual(recover_payment_events(event_id=first.pk).selected_ids, ())
        self.assertEqual(recover_payment_events(event_id=second.pk).selected_ids, (second.pk,))

    def test_batch_rejects_outer_transaction_and_invalid_parameters(self):
        with transaction.atomic():
            with self.assertRaisesMessage(ValueError, "外层事务"):
                recover_payment_events()
        for options in ({"limit": 0}, {"limit": -1}, {"event_id": 0}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    recover_payment_events(**options)

    def test_two_concurrent_scans_do_not_commit_inventory_twice(self):
        _, attempt = self.new_attempt("concurrent")
        event = self.interrupt_after_receipt(attempt, "inbox-concurrent")
        barrier = Barrier(2)
        outcomes = Queue()

        def wait_then_process(*, event_id, as_of=None):
            barrier.wait(timeout=10)
            return process_stored_payment_event(event_id=event_id, as_of=as_of)

        def scan():
            close_old_connections()
            try:
                outcomes.put((recover_payment_events(event_id=event.pk), None))
            except Exception as exc:
                outcomes.put((None, exc))
            finally:
                close_old_connections()

        with patch(
            "apps.payments.recovery.process_stored_payment_event", side_effect=wait_then_process
        ):
            threads = [Thread(target=scan), Thread(target=scan)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            self.assertFalse(any(thread.is_alive() for thread in threads))

        received = [outcomes.get_nowait(), outcomes.get_nowait()]
        self.assertEqual([error for _, error in received if error is not None], [])
        self.assertEqual([result.selected_ids for result, _ in received], [(event.pk,), (event.pk,)])
        self.assertGreaterEqual(sum(result.processed for result, _ in received), 1)
        self.assertEqual(sum(result.processed + result.skipped for result, _ in received), 2)
        event.refresh_from_db()
        self.assertEqual(event.processing_attempt_count, 1)
        self.inventory.refresh_from_db()
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock), (19, 0)
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT
            ).count(), 1
        )

    def test_command_preview_and_partial_failure_exit_status(self):
        _, attempt = self.new_attempt("command")
        event = self.interrupt_after_receipt(attempt, "inbox-command")
        output = StringIO()
        call_command("recover_payment_events", dry_run=True, event_id=event.pk, stdout=output)
        self.assertIn("dry_run=true selected=1", output.getvalue())
        event.refresh_from_db()
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.RECEIVED)

        failed_output, errors = StringIO(), StringIO()
        with patch(
            "apps.payments.services.process_payment_event",
            side_effect=OperationalError(1213, "controlled deadlock"),
        ):
            with self.assertLogs("apps.payments.recovery", level="WARNING"):
                with self.assertRaises(CommandError):
                    call_command("recover_payment_events", stdout=failed_output, stderr=errors)
        self.assertIn("selected=1 processed=0 rejected=0 errors=1", failed_output.getvalue())
        self.assertIn(f"failed_inbox_ids={event.pk}", errors.getvalue())

        success_output = StringIO()
        event.refresh_from_db()
        with patch("apps.payments.services.timezone.now", return_value=event.next_retry_at):
            call_command("recover_payment_events", event_id=event.pk, stdout=success_output)
        self.assertIn("selected=1 processed=1 rejected=0 errors=0", success_output.getvalue())

    def test_command_validates_parameters_and_local_environment_before_reading(self):
        with patch("apps.payments.recovery.PaymentEvent.objects.filter") as query:
            with self.assertRaises(CommandError):
                call_command("recover_payment_events", limit=0)
            with self.assertRaises(CommandError):
                call_command("recover_payment_events", event_id=-1)
            with override_settings(IS_LOCAL_ENVIRONMENT=False):
                with self.assertRaises(CommandError):
                    call_command("recover_payment_events", dry_run=True)
        query.assert_not_called()
