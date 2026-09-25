"""支付 Oracle：看订单、尝试、事件和库存最终事实，而不只看 HTTP 成功码。"""

from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import close_old_connections, connection
from django.test import RequestFactory, override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITransactionTestCase

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.fulfillment_services import FulfillmentRuleError, ship_order
from apps.ordering.models import Cart, CartItem, Order, Shipment
from apps.ordering.services import create_order_from_cart
from apps.payments.gateway import MockGateway, sign_payload
from apps.payments.models import MockGatewayPayment, PaymentAttempt, PaymentEvent
from apps.payments.services import create_payment_attempt, receive_payment_event


User = get_user_model()
ATTEMPTS_URL = "/api/payments/attempts/"


class PaymentAPITests(APITransactionTestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="payment_customer",
            password="S3cure-customer-pass!",
        )
        self.other_user = User.objects.create_user(
            username="other_customer",
            password="S3cure-other-pass!",
        )
        self.address = Address.objects.create(
            user=self.user,
            recipient_name="测试用户",
            phone="13800138000",
            province="广东省",
            city="深圳市",
            district="南山区",
            detail="科技园 1 号",
            default_marker=1,
        )
        category = Category.objects.create(
            name="鼠标",
            slug="payment-mice",
            is_active=True,
        )
        product = Product.objects.create(
            category=category,
            name="无线鼠标 M1",
            slug="payment-wireless-mouse",
            is_active=True,
        )
        self.sku = SKU.objects.create(
            product=product,
            code="PAY-M1-BLACK",
            price=Decimal("129.00"),
            is_active=True,
        )
        self.inventory = Inventory.objects.create(
            sku=self.sku,
            available_stock=20,
        )
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, sku=self.sku, quantity=2)
        self.order, created = create_order_from_cart(
            user=self.user,
            address_id=self.address.pk,
            idempotency_key="payment-fixture-order",
        )
        self.assertTrue(created)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(self.order.total, Decimal("258.00"))
        self.inventory.refresh_from_db()
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 2),
        )

    def authenticate(self, user=None):
        self.client.force_login(user or self.user)
        response = self.client.get("/api/auth/csrf/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        return self.client.cookies[settings.CSRF_COOKIE_NAME].value

    def post_attempt(self, *, key="pay-001", order=None, user=None):
        csrf_token = self.authenticate(user)
        return self.client.post(
            ATTEMPTS_URL,
            {"order_public_id": str((order or self.order).public_id)},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def event_payload(self, attempt, *, event_id, outcome="SUCCEEDED", **overrides):
        payload = {
            "provider": "mock",
            "event_id": event_id,
            "provider_reference": str(attempt.provider_reference),
            "order_public_id": str(attempt.order.public_id),
            "amount": str(attempt.amount),
            "currency": attempt.currency,
            "status": outcome,
        }
        payload.update(overrides)
        return payload

    def test_success_commits_reservation_once_without_decreasing_available_again(self):
        response = self.post_attempt()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        reservation = InventoryReservation.objects.get(order_item__order=self.order)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(PaymentAttempt.objects.get().status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(reservation.status, InventoryReservation.Status.COMMITTED)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                reservation=reservation,
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_same_key_replays_original_attempt_and_does_not_commit_stock_twice(self):
        first = self.post_attempt(key="pay-replay")
        second = self.post_attempt(key="pay-replay")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["public_id"], second.json()["public_id"])
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )
        self.inventory.refresh_from_db()
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )

    def test_same_key_with_different_order_returns_conflict(self):
        CartItem.objects.create(cart=self.cart, sku=self.sku, quantity=1)
        second_order, created = create_order_from_cart(
            user=self.user,
            address_id=self.address.pk,
            idempotency_key="payment-fixture-second-order",
        )
        self.assertTrue(created)

        first = self.post_attempt(key="pay-conflict")
        second = self.post_attempt(key="pay-conflict", order=second_order)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.json()["code"], "idempotency_conflict")
        self.assertIn("不同订单", second.json()["detail"])
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        second_order.refresh_from_db()
        self.assertEqual(second_order.status, Order.Status.PENDING_PAYMENT)

    def test_missing_key_is_rejected_without_creating_an_attempt(self):
        csrf_token = self.authenticate()
        response = self.client.post(
            ATTEMPTS_URL,
            {"order_public_id": str(self.order.public_id)},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("缺少", response.json()["detail"])
        self.assertFalse(PaymentAttempt.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_session_csrf_and_order_ownership_are_enforced_by_backend(self):
        unauthenticated = self.client.post(
            ATTEMPTS_URL,
            {"order_public_id": str(self.order.public_id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="pay-unauthenticated",
        )
        self.client.force_login(self.user)
        no_csrf = self.client.post(
            ATTEMPTS_URL,
            {"order_public_id": str(self.order.public_id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="pay-no-csrf",
        )
        other_user = self.post_attempt(key="pay-other-owner", user=self.other_user)

        self.assertEqual(unauthenticated.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(no_csrf.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(other_user.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_failed_attempt_keeps_order_payable_and_new_key_can_succeed(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "FAILED"):
            first = self.post_attempt(key="pay-failed")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(
            PaymentAttempt.objects.get(idempotency_key="pay-failed").status,
            PaymentAttempt.Status.FAILED,
        )
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 2),
        )

        second = self.post_attempt(key="pay-after-failure")

        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PaymentAttempt.objects.count(), 2)
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )

    def test_pending_attempt_blocks_new_key_and_same_key_reuses_it(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "PENDING"):
            first = self.post_attempt(key="pay-pending")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        different_key = self.post_attempt(key="pay-another")
        same_key = self.post_attempt(key="pay-pending")

        self.assertEqual(different_key.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(same_key.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["public_id"], same_key.json()["public_id"])
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

    def test_paid_order_cannot_start_another_attempt(self):
        first = self.post_attempt(key="pay-once")
        second = self.post_attempt(key="pay-again")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_customer_can_only_read_their_own_attempts(self):
        first = self.post_attempt(key="pay-private")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        public_id = first.json()["public_id"]

        self.authenticate(self.other_user)
        detail = self.client.get(f"{ATTEMPTS_URL}{public_id}/")
        listing = self.client.get(
            ATTEMPTS_URL,
            {"order_public_id": str(self.order.public_id)},
        )

        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn(
            listing.status_code,
            (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND),
        )
        if listing.status_code == status.HTTP_200_OK:
            self.assertEqual(listing.json(), [])

    def test_ten_replays_of_one_success_event_commit_reserved_stock_once(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "PENDING"):
            response = self.post_attempt(key="pay-event-replay")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-event-replay")
        payload = self.event_payload(attempt, event_id="payment-success-replayed")
        signature = sign_payload(payload)

        for _ in range(10):
            receive_payment_event(payload=payload, signature=signature)

        self.assertEqual(
            PaymentEvent.objects.filter(
                provider="mock",
                event_id="payment-success-replayed",
            ).count(),
            1,
        )
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )

    def test_signed_mock_webhook_can_complete_payment_without_customer_session(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "PENDING"):
            response = self.post_attempt(key="pay-webhook")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-webhook")
        payload = self.event_payload(attempt, event_id="payment-webhook-success")
        self.client.logout()

        webhook = self.client.post(
            "/api/payments/mock/webhook/",
            payload,
            format="json",
            HTTP_X_MOCK_SIGNATURE=sign_payload(payload),
        )

        self.assertEqual(webhook.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            PaymentEvent.objects.get(event_id="payment-webhook-success").processing_status,
            PaymentEvent.ProcessingStatus.PROCESSED,
        )

    def test_mock_webhook_rejects_non_loopback_request_without_storing_event(self):
        response = self.client.post(
            "/api/payments/mock/webhook/",
            {"provider": "mock", "event_id": "untrusted-remote"},
            format="json",
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(PaymentEvent.objects.exists())

    def test_mock_webhook_rejects_oversized_body_without_storing_event(self):
        response = self.client.post(
            "/api/payments/mock/webhook/",
            {"provider": "mock", "event_id": "oversized", "padding": "x" * 20000},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.assertFalse(PaymentEvent.objects.exists())

    @override_settings(IS_LOCAL_ENVIRONMENT=False)
    def test_mock_webhook_is_disabled_outside_local_environments(self):
        response = self.client.post(
            "/api/payments/mock/webhook/",
            {"provider": "mock", "event_id": "nonlocal"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(PaymentEvent.objects.exists())

    def test_identical_bad_signature_events_share_one_audit_record(self):
        payload = {"provider": "mock", "event_id": "forged-same-event"}

        first = receive_payment_event(payload=payload, signature="bad-signature")
        second = receive_payment_event(payload=payload, signature="bad-signature")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.processing_status, PaymentEvent.ProcessingStatus.REJECTED)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_non_ascii_signature_is_rejected_without_processing_error(self):
        payload = {"provider": "mock", "event_id": "non-ascii-signature"}

        event = receive_payment_event(payload=payload, signature="错误签名")

        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.REJECTED)
        self.assertEqual(PaymentEvent.objects.count(), 1)

    def test_bad_signature_amount_or_currency_keeps_original_inbox_and_no_payment(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "PENDING"):
            response = self.post_attempt(key="pay-invalid-events")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-invalid-events")
        cases = (
            (
                "payment-bad-signature",
                {},
                "incorrect-signature",
            ),
            (
                "payment-bad-amount",
                {"amount": "0.01"},
                None,
            ),
            (
                "payment-bad-currency",
                {"currency": "USD"},
                None,
            ),
        )

        for event_id, overrides, forged_signature in cases:
            with self.subTest(event_id=event_id):
                payload = self.event_payload(
                    attempt,
                    event_id=event_id,
                    **overrides,
                )
                event = receive_payment_event(
                    payload=payload,
                    signature=forged_signature or sign_payload(payload),
                )
                self.assertEqual(event.raw_payload, payload)
                self.assertEqual(
                    event.processing_status,
                    PaymentEvent.ProcessingStatus.REJECTED,
                )

        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 2),
        )
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).exists()
        )

    def test_forged_event_id_cannot_block_the_later_valid_signed_event(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "PENDING"):
            response = self.post_attempt(key="pay-forged-first")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-forged-first")
        payload = self.event_payload(attempt, event_id="payment-same-claimed-id")

        forged = receive_payment_event(
            payload=payload,
            signature="forged-signature",
        )
        self.assertEqual(
            forged.processing_status,
            PaymentEvent.ProcessingStatus.REJECTED,
        )
        self.assertNotEqual(forged.event_id, payload["event_id"])
        self.assertFalse(
            PaymentEvent.objects.filter(
                provider="mock",
                event_id=payload["event_id"],
            ).exists()
        )

        valid = receive_payment_event(
            payload=payload,
            signature=sign_payload(payload),
        )

        self.assertEqual(valid.event_id, payload["event_id"])
        self.assertEqual(
            valid.processing_status,
            PaymentEvent.ProcessingStatus.PROCESSED,
        )
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )

    def test_late_failed_event_cannot_reverse_confirmed_success(self):
        response = self.post_attempt(key="pay-before-late-failure")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-before-late-failure")
        payload = self.event_payload(
            attempt,
            event_id="payment-late-failure",
            outcome="FAILED",
        )

        event = receive_payment_event(
            payload=payload,
            signature=sign_payload(payload),
        )

        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(
            event.processing_status,
            PaymentEvent.ProcessingStatus.REJECTED,
        )
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )

    def test_commit_failure_rolls_back_payment_and_stock_then_inbox_can_retry(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "PENDING"):
            response = self.post_attempt(key="pay-commit-retry")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-commit-retry")
        payload = self.event_payload(attempt, event_id="payment-commit-retry")
        signature = sign_payload(payload)

        with patch(
            "apps.payments.services.InventoryMovement.objects.create",
            side_effect=RuntimeError("simulated database write failure"),
        ):
            with self.assertRaises(RuntimeError):
                receive_payment_event(payload=payload, signature=signature)

        event = PaymentEvent.objects.get(event_id="payment-commit-retry")
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        reservation = InventoryReservation.objects.get(order_item__order=self.order)
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.ERROR)
        self.assertEqual(attempt.status, PaymentAttempt.Status.PENDING)
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(reservation.status, InventoryReservation.Status.RESERVED)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 2),
        )
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).exists()
        )

        retried = receive_payment_event(payload=payload, signature=signature)

        self.assertEqual(
            retried.processing_status,
            PaymentEvent.ProcessingStatus.PROCESSED,
        )
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )

    def test_gateway_runs_after_the_attempt_transaction_has_committed(self):
        original_create_payment = MockGateway.create_payment
        observed = []

        def check_boundary(gateway, attempt):
            observed.append(
                (
                    connection.in_atomic_block,
                    PaymentAttempt.objects.filter(pk=attempt.pk).exists(),
                )
            )
            return original_create_payment(gateway, attempt)

        with patch.object(MockGateway, "create_payment", check_boundary):
            response = self.post_attempt(key="pay-transaction-boundary")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(observed, [(False, True)])

    def test_timeout_becomes_unknown_then_same_attempt_reconciles_success(self):
        original_create_payment = MockGateway.create_payment

        def timeout_after_provider(gateway, attempt):
            original_create_payment(gateway, attempt)
            raise TimeoutError("Provider response was lost after it processed payment.")

        with patch.object(MockGateway, "create_payment", timeout_after_provider):
            first = self.post_attempt(key="pay-timeout")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        attempt = PaymentAttempt.objects.get(idempotency_key="pay-timeout")
        self.assertEqual(attempt.status, PaymentAttempt.Status.UNKNOWN)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)

        another_key = self.post_attempt(key="pay-timeout-new-key")
        self.assertEqual(another_key.status_code, status.HTTP_409_CONFLICT)
        same_key = self.post_attempt(key="pay-timeout")
        self.assertEqual(same_key.status_code, status.HTTP_200_OK)
        self.assertEqual(PaymentAttempt.objects.count(), 1)

        csrf_token = self.authenticate()
        reconcile = self.client.post(
            f"{ATTEMPTS_URL}{attempt.public_id}/reconcile/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(reconcile.status_code, status.HTTP_200_OK)
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )

    def test_created_attempt_recovers_if_gateway_was_never_called(self):
        attempt, created = create_payment_attempt(
            user=self.user,
            order_public_id=self.order.public_id,
            idempotency_key="pay-crash-before-gateway",
        )
        self.assertTrue(created)
        self.assertFalse(MockGatewayPayment.objects.exists())

        csrf_token = self.authenticate()
        response = self.client.post(
            f"{ATTEMPTS_URL}{attempt.public_id}/reconcile/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(MockGatewayPayment.objects.count(), 1)

    def test_fourth_active_payment_attempt_is_rejected_after_three_failures(self):
        with patch("apps.payments.gateway.MockGateway.default_outcome", "FAILED"):
            for number in range(1, 4):
                with self.subTest(number=number):
                    response = self.post_attempt(key=f"pay-failure-{number}")
                    self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            fourth = self.post_attempt(key="pay-failure-4")

        self.assertEqual(fourth.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(PaymentAttempt.objects.count(), 3)
        self.order.refresh_from_db()
        self.inventory.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 2),
        )

    def test_two_concurrent_same_key_requests_create_one_attempt_and_one_commit(self):
        barrier = Barrier(2)
        outcomes = Queue()

        def pay():
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                client = APIClient()
                client.force_authenticate(user=user)
                barrier.wait(timeout=10)
                response = client.post(
                    ATTEMPTS_URL,
                    {"order_public_id": str(self.order.public_id)},
                    format="json",
                    HTTP_IDEMPOTENCY_KEY="pay-concurrent",
                )
                outcomes.put((response.status_code, response.json(), None))
            except Exception as exc:
                outcomes.put((None, None, exc))
            finally:
                close_old_connections()

        threads = [Thread(target=pay), Thread(target=pay)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        received = [outcomes.get_nowait(), outcomes.get_nowait()]
        self.assertEqual([error for _, _, error in received if error], [])
        self.assertEqual(
            sorted(code for code, _, _ in received),
            [status.HTTP_200_OK, status.HTTP_201_CREATED],
        )
        self.assertEqual(
            len({payload["public_id"] for _, payload, _ in received}),
            1,
        )
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovement.Type.COMMIT,
            ).count(),
            1,
        )
        self.inventory.refresh_from_db()
        self.assertEqual(
            (self.inventory.available_stock, self.inventory.reserved_stock),
            (18, 0),
        )

    def test_admin_permission_shipping_and_customer_receipt_complete_the_order(self):
        staff = User.objects.create_user(
            username="shipping_operator",
            password="S3cure-operator-pass!",
            is_staff=True,
        )
        superuser = User.objects.create_superuser(
            username="shipping_admin",
            password="S3cure-admin-pass!",
        )
        with self.assertRaises(FulfillmentRuleError) as before_payment:
            ship_order(order_id=self.order.pk, actor=superuser)
        self.assertEqual(before_payment.exception.code, "order_not_paid")
        self.assertIn("已支付", before_payment.exception.detail)

        paid = self.post_attempt(key="pay-before-shipping")
        self.assertEqual(paid.status_code, status.HTTP_201_CREATED)
        with self.assertRaises(FulfillmentRuleError) as missing_permission:
            ship_order(order_id=self.order.pk, actor=staff)
        self.assertEqual(missing_permission.exception.http_status, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

        change_order = Permission.objects.get(
            content_type__app_label="ordering",
            codename="change_order",
        )
        staff.user_permissions.add(change_order)
        staff = User.objects.get(pk=staff.pk)
        shipped_order, shipped = ship_order(order_id=self.order.pk, actor=staff)
        shipped_again, shipped_twice = ship_order(order_id=self.order.pk, actor=staff)
        self.assertTrue(shipped)
        self.assertFalse(shipped_twice)
        self.assertEqual(shipped_order.pk, shipped_again.pk)
        self.assertEqual(Shipment.objects.filter(order=self.order).count(), 1)
        self.assertEqual(shipped_order.status, Order.Status.SHIPPED)

        self.authenticate(self.other_user)
        csrf_token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        other_customer = self.client.post(
            f"/api/orders/{self.order.public_id}/confirm-receipt/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(other_customer.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("无权查看", other_customer.json()["detail"])

        csrf_token = self.authenticate()
        first_receipt = self.client.post(
            f"/api/orders/{self.order.public_id}/confirm-receipt/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        repeated_receipt = self.client.post(
            f"/api/orders/{self.order.public_id}/confirm-receipt/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(first_receipt.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated_receipt.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.COMPLETED)
        self.assertEqual(Shipment.objects.filter(order=self.order).count(), 1)

    def test_customer_cannot_confirm_receipt_before_shipping(self):
        paid = self.post_attempt(key="pay-no-early-receipt")
        self.assertEqual(paid.status_code, status.HTTP_201_CREATED)
        csrf_token = self.authenticate()

        receipt = self.client.post(
            f"/api/orders/{self.order.public_id}/confirm-receipt/",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(receipt.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("已发货", receipt.json()["detail"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertFalse(Shipment.objects.filter(order=self.order).exists())

    def test_admin_shipping_action_uses_the_paid_order_rule(self):
        operator = User.objects.create_user(
            username="admin_action_operator",
            password="S3cure-operator-pass!",
            is_staff=True,
        )
        change_order = Permission.objects.get(
            content_type__app_label="ordering",
            codename="change_order",
        )
        operator.user_permissions.add(change_order)
        operator = User.objects.get(pk=operator.pk)
        request = RequestFactory().post("/admin/ordering/order/")
        request.user = operator
        request.session = {}
        request._messages = FallbackStorage(request)
        order_admin = admin.site._registry[Order]
        self.assertIn("ship_selected_orders", order_admin.get_actions(request))

        selected = Order.objects.filter(pk=self.order.pk)
        order_admin.ship_selected_orders(request, selected)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING_PAYMENT)
        self.assertFalse(Shipment.objects.filter(order=self.order).exists())

        paid = self.post_attempt(key="pay-admin-action")
        self.assertEqual(paid.status_code, status.HTTP_201_CREATED)
        order_admin.ship_selected_orders(request, selected)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPED)
        self.assertEqual(Shipment.objects.filter(order=self.order).count(), 1)
