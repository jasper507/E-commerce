from decimal import Decimal

from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


class InventoryModelTests(TestCase):
    def setUp(self):
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

    def assert_integrity_error(self, callback):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                callback()

    def assert_database_error(self, callback):
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                callback()

    def test_each_sku_has_at_most_one_inventory_record(self):
        Inventory.objects.create(sku=self.sku)

        self.assert_integrity_error(
            lambda: Inventory.objects.create(sku=self.sku)
        )

    def test_stock_defaults_to_zero(self):
        inventory = Inventory.objects.create(sku=self.sku)

        self.assertEqual(inventory.available_stock, 0)
        self.assertEqual(inventory.reserved_stock, 0)

    def test_mysql_rejects_negative_available_or_reserved_stock(self):
        inventory = Inventory.objects.create(
            sku=self.sku,
            available_stock=10,
            reserved_stock=2,
        )

        for field in ("available_stock", "reserved_stock"):
            with self.subTest(field=field):
                self.assert_database_error(
                    lambda field=field: Inventory.objects.filter(
                        pk=inventory.pk
                    ).update(**{field: -1})
                )
