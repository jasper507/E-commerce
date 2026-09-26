"""未付款取消、到期释放与支付成功竞争的 MySQL 合约。"""

from decimal import Decimal
from io import StringIO
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.lifecycle_services import (
    OrderLifecycleError,
    cancel_order,
    expire_due_orders,
)
from apps.ordering.models import Cart, CartItem, Order, Shipment
from apps.ordering.services import create_order_from_cart
from apps.payments.gateway import MockGateway, sign_payload
from apps.payments.models import MockGatewayPayment, PaymentAttempt, PaymentEvent, RefundSimulation
from apps.payments.services import (
    create_payment_attempt,
    mark_attempt_unknown,
    receive_payment_event,
    start_payment,
)


User = get_user_model()


class OrderLifecycleTests(APITransactionTestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="lifecycle_customer", password="S3cure-customer-pass!"
        )
        self.other_user = User.objects.create_user(
            username="lifecycle_other", password="S3cure-other-pass!"
        )
        address = Address.objects.create(
            user=self.user,
            recipient_name="测试顾客",
            phone="13800138000",
            province="广东省",
            city="深圳市",
            district="南山区",
            detail="科技园 1 号",
            default_marker=1,
        )
        category = Category.objects.create(
            name="实验鼠标", slug="lifecycle-mice", is_active=True
        )
        product = Product.objects.create(
            category=category,
            name="无线鼠标",
            slug="lifecycle-mouse",
            is_active=True,
        )
        sku = SKU.objects.create(
            product=product,
            code="LIFECYCLE-MOUSE",
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(sku=sku, available_stock=1)
        cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=cart, sku=sku, quantity=1)
        self.order, created = create_order_from_cart(
            user=self.user,
            address_id=address.pk,
            idempotency_key="lifecycle-order",
        )
        self.assertTrue(created)

    def _cancel_api(self, user):
        self.client.force_login(user)
        csrf_response = self.client.get("/api/auth/csrf/")
        self.assertEqual(csrf_response.status_code, 204)
        return self.client.post(
            f"/api/orders/{self.order.public_id}/cancel/",
            HTTP_X_CSRFTOKEN=self.client.cookies[settings.CSRF_COOKIE_NAME].value,
        )

    def _assert_inventory(self, available, reserved, reservation_status, release_count):
        self.inventory.refresh_from_db()
        reservation = InventoryReservation.objects.get(order_item__order=self.order)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (available, reserved),
        )
        self.assertEqual(reservation.status, reservation_status)
        self.assertEqual(
            InventoryMovement.objects.filter(
                reservation=reservation,
                movement_type=InventoryMovement.Type.RELEASE,
            ).count(),
            release_count,
        )

    def test_cancel_releases_once_and_only_owner_can_cancel(self):
        denied = self._cancel_api(self.other_user)
        self.assertEqual(denied.status_code, 404)
        first = self._cancel_api(self.user)
        second = self._cancel_api(self.user)

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json()["status"], Order.Status.CANCELED)
        self.assertEqual(second.json()["status"], Order.Status.CANCELED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_expiration_command_only_closes_due_order_and_is_repeatable(self):
        first_run = StringIO()
        call_command("expire_pending_orders", stdout=first_run)
        self.assertIn("expired=0", first_run.getvalue())
        self._assert_inventory(0, 1, InventoryReservation.Status.RESERVED, 0)

        InventoryReservation.objects.filter(order_item__order=self.order).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
        second_run = StringIO()
        third_run = StringIO()
        call_command("expire_pending_orders", stdout=second_run)
        call_command("expire_pending_orders", stdout=third_run)

        self.assertIn("expired=1", second_run.getvalue())
        self.assertIn("expired=0", third_run.getvalue())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.EXPIRED)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)

    def test_unresolved_payment_blocks_cancel_and_expiration(self):
        attempt, created = create_payment_attempt(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="unresolved-lifecycle",
        )
        self.assertTrue(created)
        self.assertEqual(attempt.status, PaymentAttempt.Status.CREATED)
        attempt = mark_attempt_unknown(attempt_id=attempt.pk)
        self.assertEqual(attempt.status, PaymentAttempt.Status.UNKNOWN)
        response = self._cancel_api(self.user)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "payment_unresolved")
        InventoryReservation.objects.filter(order_item__order=self.order).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
        self.assertEqual(expire_due_orders(), (0, 1))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self._assert_inventory(0, 1, InventoryReservation.Status.RESERVED, 0)

    def test_paid_unshipped_cancel_restores_stock_and_records_one_mock_refund(self):
        attempt, created = start_payment(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="paid-lifecycle",
            gateway=MockGateway(),
        )
        self.assertTrue(created)
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        first = self._cancel_api(self.user)
        second = self._cancel_api(self.user)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(first.json()["status"], Order.Status.CANCELED)
        self._assert_inventory(1, 0, InventoryReservation.Status.RESTORED, 0)
        self.assertEqual(InventoryMovement.objects.filter(movement_type=InventoryMovement.Type.RESTORE).count(), 1)
        refund = RefundSimulation.objects.get(attempt=attempt)
        self.assertEqual(refund.reason, RefundSimulation.Reason.PAID_CANCEL)
        self.assertEqual(refund.status, RefundSimulation.Status.SIMULATED)
        self.assertEqual((refund.amount, refund.currency), (self.order.total, self.order.currency))
        self.assertEqual(RefundSimulation.objects.count(), 1)
        self.client.force_login(self.user)
        detail = self.client.get(f"/api/payments/attempts/{attempt.public_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["refund_status"], "SIMULATED")
        payload, _ = MockGateway().query_payment(attempt)
        another_payload = {**payload, "event_id": "paid-cancel-success-again"}
        event = receive_payment_event(
            payload=another_payload, signature=sign_payload(another_payload)
        )
        self.assertEqual(event.error_code, "late_success_refund_simulated")
        self.assertEqual(RefundSimulation.objects.count(), 1)
        self._assert_inventory(1, 0, InventoryReservation.Status.RESTORED, 0)

    def test_paid_cancel_refund_write_failure_rolls_back_inventory_and_order(self):
        start_payment(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="rollback-lifecycle",
            gateway=MockGateway(),
        )
        with patch(
            "apps.ordering.lifecycle_services.simulate_refund",
            side_effect=RuntimeError("mock refund unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                cancel_order(public_id=self.order.public_id, user=self.user)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self._assert_inventory(0, 0, InventoryReservation.Status.COMMITTED, 0)
        self.assertEqual(InventoryMovement.objects.filter(movement_type=InventoryMovement.Type.RESTORE).count(), 0)
        self.assertEqual(RefundSimulation.objects.count(), 0)

    def test_shipped_order_cannot_cancel_or_restore_stock(self):
        attempt, _ = start_payment(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="shipped-lifecycle",
            gateway=MockGateway(),
        )
        Shipment.objects.create(order=self.order, shipped_by=self.user)
        self.order.status = Order.Status.SHIPPED
        self.order.save(update_fields=("status", "updated_at"))
        response = self._cancel_api(self.user)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "order_not_cancelable")
        self._assert_inventory(0, 0, InventoryReservation.Status.COMMITTED, 0)
        self.assertFalse(RefundSimulation.objects.filter(attempt=attempt).exists())

    def test_late_success_after_cancel_records_one_refund_without_second_stock_change(self):
        class FailedGateway(MockGateway):
            default_outcome = MockGatewayPayment.Outcome.FAILED

        attempt, _ = start_payment(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="late-cancel-lifecycle",
            gateway=FailedGateway(),
        )
        self.assertEqual(self._cancel_api(self.user).status_code, 200)
        MockGatewayPayment.objects.filter(attempt=attempt).update(
            outcome=MockGatewayPayment.Outcome.SUCCEEDED
        )
        payload, signature = MockGateway().query_payment(attempt)
        first = receive_payment_event(payload=payload, signature=signature)
        replay = receive_payment_event(payload=payload, signature=signature)
        another_payload = {**payload, "event_id": "late-cancel-success-again"}
        another = receive_payment_event(
            payload=another_payload, signature=sign_payload(another_payload)
        )

        self.assertEqual(first.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(first.error_code, "late_success_refund_simulated")
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(another.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)
        self.assertEqual(InventoryMovement.objects.filter(movement_type=InventoryMovement.Type.COMMIT).count(), 0)
        self.assertEqual(RefundSimulation.objects.get(attempt=attempt).reason, RefundSimulation.Reason.LATE_SUCCESS)
        self.assertEqual(RefundSimulation.objects.count(), 1)
        self.client.force_login(self.user)
        detail = self.client.get(f"/api/payments/attempts/{attempt.public_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["refund_status"], "SIMULATED")

    def test_late_success_after_expiry_keeps_order_expired_and_refunds(self):
        class FailedGateway(MockGateway):
            default_outcome = MockGatewayPayment.Outcome.FAILED

        attempt, _ = start_payment(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="late-expiry-lifecycle",
            gateway=FailedGateway(),
        )
        InventoryReservation.objects.filter(order_item__order=self.order).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1)
        )
        self.assertEqual(expire_due_orders(), (1, 0))
        MockGatewayPayment.objects.filter(attempt=attempt).update(
            outcome=MockGatewayPayment.Outcome.SUCCEEDED
        )
        payload, signature = MockGateway().query_payment(attempt)
        event = receive_payment_event(payload=payload, signature=signature)
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.EXPIRED)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)
        self.assertEqual(RefundSimulation.objects.get(attempt=attempt).reason, RefundSimulation.Reason.LATE_SUCCESS)

    def test_invalid_late_success_cannot_create_refund(self):
        attempt, _ = create_payment_attempt(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="invalid-late-lifecycle",
        )
        attempt.status = PaymentAttempt.Status.FAILED
        attempt.active_marker = None
        attempt.save(update_fields=("status", "active_marker", "updated_at"))
        self.assertEqual(self._cancel_api(self.user).status_code, 200)
        payload = {
            "provider": "mock",
            "event_id": "invalid-late-success",
            "provider_reference": str(attempt.provider_reference),
            "order_public_id": str(self.order.public_id),
            "amount": "0.01",
            "currency": attempt.currency,
            "status": "SUCCEEDED",
        }
        event = receive_payment_event(payload=payload, signature=sign_payload(payload))
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.REJECTED)
        self.assertEqual(event.error_code, "payment_mismatch")
        self.assertEqual(RefundSimulation.objects.count(), 0)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)

    def test_failed_payment_can_be_followed_by_cancel_and_one_release(self):
        class FailedGateway(MockGateway):
            default_outcome = MockGatewayPayment.Outcome.FAILED

        attempt, _ = start_payment(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="failed-lifecycle",
            gateway=FailedGateway(),
        )
        self.assertEqual(attempt.status, PaymentAttempt.Status.FAILED)
        response = self._cancel_api(self.user)
        self.assertEqual(response.status_code, 200)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)

    def test_two_simultaneous_cancels_release_only_once(self):
        start = Barrier(2)
        results = Queue()

        def cancel():
            close_old_connections()
            try:
                start.wait(timeout=10)
                _, changed = cancel_order(
                    public_id=self.order.public_id, user=self.user
                )
                results.put(changed)
            except Exception as exc:
                results.put(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=cancel), Thread(target=cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(results.get_nowait() for _ in threads), [False, True])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)
        self._assert_inventory(1, 0, InventoryReservation.Status.RELEASED, 1)

    def test_payment_success_and_cancel_race_never_releases_paid_stock(self):
        attempt, _ = create_payment_attempt(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="race-lifecycle",
        )
        payload = {
            "provider": "mock",
            "event_id": "race-lifecycle-success",
            "provider_reference": str(attempt.provider_reference),
            "order_public_id": str(self.order.public_id),
            "amount": str(attempt.amount),
            "currency": attempt.currency,
            "status": "SUCCEEDED",
        }
        start = Barrier(2)
        results = Queue()

        def pay():
            close_old_connections()
            try:
                start.wait(timeout=10)
                event = receive_payment_event(
                    payload=payload, signature=sign_payload(payload)
                )
                results.put(("payment", event.processing_status))
            except Exception as exc:
                results.put(("payment_error", exc))
            finally:
                close_old_connections()

        def cancel():
            close_old_connections()
            try:
                start.wait(timeout=10)
                _, changed = cancel_order(public_id=self.order.public_id, user=self.user)
                results.put(("cancel", changed))
            except OrderLifecycleError as exc:
                results.put(("cancel", exc.code))
            except Exception as exc:
                results.put(("cancel_error", exc))
            finally:
                close_old_connections()

        threads = [Thread(target=pay), Thread(target=cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        outcomes = dict(results.get_nowait() for _ in threads)
        self.assertNotIn("payment_error", outcomes)
        self.assertNotIn("cancel_error", outcomes)
        self.assertEqual(outcomes["payment"], PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertIn(outcomes["cancel"], {"payment_unresolved", True})
        self.order.refresh_from_db()
        if outcomes["cancel"] is True:
            self.assertEqual(self.order.status, Order.Status.CANCELED)
            self._assert_inventory(1, 0, InventoryReservation.Status.RESTORED, 0)
            self.assertEqual(RefundSimulation.objects.count(), 1)
        else:
            self.assertEqual(self.order.status, Order.Status.PAID)
            self._assert_inventory(0, 0, InventoryReservation.Status.COMMITTED, 0)
            self.assertEqual(RefundSimulation.objects.count(), 0)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT
            ).count(),
            1,
        )
