from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Category, Product, SKU
from apps.ordering.models import Order, OrderItem


User = get_user_model()


class OrderModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="customer_01",
            password="S3cure-customer-pass!",
        )
        category = Category.objects.create(
            name="鼠标",
            slug="mice",
            is_active=True,
        )
        product = Product.objects.create(
            category=category,
            name="无线鼠标 M1",
            slug="wireless-mouse-m1",
            is_active=True,
        )
        self.sku = SKU.objects.create(
            product=product,
            code="M1-BLACK",
            price=Decimal("129.00"),
            is_active=True,
        )

    def order_fields(self, **overrides):
        fields = {
            "user": self.user,
            "subtotal": Decimal("129.00"),
            "discount_total": Decimal("0.00"),
            "shipping_fee": Decimal("0.00"),
            "total": Decimal("129.00"),
            "idempotency_key": "checkout-001",
            "request_fingerprint": "f" * 64,
        }
        fields.update(overrides)
        return fields

    def assert_database_error(self, callback, error_class=DatabaseError):
        with self.assertRaises(error_class):
            with transaction.atomic():
                callback()

    def test_idempotency_key_is_unique_per_user(self):
        Order.objects.create(**self.order_fields())

        self.assert_database_error(
            lambda: Order.objects.create(**self.order_fields()),
            IntegrityError,
        )

    def test_mysql_rejects_invalid_order_amounts_and_formula(self):
        cases = (
            {"subtotal": Decimal("-0.01"), "total": Decimal("-0.01")},
            {"discount_total": Decimal("130.00"), "total": Decimal("0.00")},
            {"shipping_fee": Decimal("-0.01"), "total": Decimal("128.99")},
            {"total": Decimal("0.01")},
        )

        for index, overrides in enumerate(cases):
            with self.subTest(overrides=overrides):
                self.assert_database_error(
                    lambda index=index, overrides=overrides: Order.objects.create(
                        **self.order_fields(
                            idempotency_key=f"checkout-{index}",
                            **overrides,
                        )
                    )
                )

    def test_mysql_rejects_invalid_order_item_quantity_or_total(self):
        order = Order.objects.create(**self.order_fields())
        cases = (
            {"quantity": 0, "line_total": Decimal("0.00")},
            {"quantity": 2, "line_total": Decimal("1.00")},
            {"unit_price": Decimal("-1.00"), "line_total": Decimal("-1.00")},
            {"discount_amount": Decimal("130.00"), "line_total": Decimal("0.00")},
            {"discount_amount": Decimal("10.00"), "line_total": Decimal("129.00")},
        )

        for overrides in cases:
            fields = {
                "order": order,
                "sku": self.sku,
                "product_name_snapshot": "无线鼠标 M1",
                "sku_code_snapshot": "M1-BLACK",
                "unit_price": Decimal("129.00"),
                "quantity": 1,
                "discount_amount": Decimal("0.00"),
                "line_total": Decimal("129.00"),
            }
            fields.update(overrides)
            with self.subTest(overrides=overrides):
                self.assert_database_error(lambda fields=fields: OrderItem.objects.create(**fields))
