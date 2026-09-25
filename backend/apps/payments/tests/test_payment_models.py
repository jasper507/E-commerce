from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase

from apps.ordering.models import Order
from apps.payments.models import PaymentAttempt, PaymentEvent


User = get_user_model()


class PaymentModelConstraintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="payment_model_customer",
            password="S3cure-customer-pass!",
        )
        self.first_order = self.make_order("model-order-first")
        self.second_order = self.make_order("model-order-second")

    def make_order(self, key):
        return Order.objects.create(
            user=self.user,
            subtotal=Decimal("10.00"),
            discount_total=Decimal("0.00"),
            shipping_fee=Decimal("0.00"),
            total=Decimal("10.00"),
            idempotency_key=key,
            request_fingerprint="o" * 64,
        )

    def make_attempt(self, *, order=None, key="model-pay-1", number=1, marker=1):
        return PaymentAttempt.objects.create(
            order=order or self.first_order,
            user=self.user,
            amount=Decimal("10.00"),
            currency="CNY",
            idempotency_key=key,
            request_fingerprint="p" * 64,
            attempt_number=number,
            active_marker=marker,
        )

    def assert_database_rejects(self, callback, error_class=DatabaseError):
        with self.assertRaises(error_class):
            with transaction.atomic():
                callback()

    def test_mysql_rejects_same_customer_key_even_for_different_orders(self):
        self.make_attempt(key="same-customer-key")

        self.assert_database_rejects(
            lambda: self.make_attempt(
                order=self.second_order,
                key="same-customer-key",
            ),
            IntegrityError,
        )

    def test_mysql_rejects_two_active_attempts_for_one_order(self):
        self.make_attempt(key="first-active", number=1, marker=1)

        self.assert_database_rejects(
            lambda: self.make_attempt(
                key="second-active",
                number=2,
                marker=1,
            ),
            IntegrityError,
        )

    def test_mysql_rejects_duplicate_event_for_same_provider(self):
        self.make_attempt()
        PaymentEvent.objects.create(
            provider="mock",
            event_id="duplicate-provider-event",
            raw_payload={"status": "PENDING"},
        )

        self.assert_database_rejects(
            lambda: PaymentEvent.objects.create(
                provider="mock",
                event_id="duplicate-provider-event",
                raw_payload={"status": "SUCCEEDED"},
            ),
            IntegrityError,
        )
        self.assertEqual(
            PaymentEvent.objects.filter(
                provider="mock",
                event_id="duplicate-provider-event",
            ).count(),
            1,
        )

    def test_mysql_rejects_negative_amount_and_zero_attempt_number(self):
        for key, overrides in (
            ("negative-amount", {"amount": Decimal("-0.01")}),
            ("zero-number", {"attempt_number": 0}),
        ):
            with self.subTest(key=key):
                fields = {
                    "order": self.first_order,
                    "user": self.user,
                    "amount": Decimal("10.00"),
                    "currency": "CNY",
                    "idempotency_key": key,
                    "request_fingerprint": "p" * 64,
                    "attempt_number": 1,
                    "active_marker": None,
                }
                fields.update(overrides)
                self.assert_database_rejects(
                    lambda fields=fields: PaymentAttempt.objects.create(**fields)
                )
