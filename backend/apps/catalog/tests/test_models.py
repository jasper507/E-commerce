from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


class CatalogModelTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name="鼠标",
            slug="mice",
            is_active=True,
        )
        self.product = Product.objects.create(
            category=self.category,
            name="无线鼠标 M1",
            slug="wireless-mouse-m1",
            description="双模无线鼠标",
            is_active=True,
        )

    def assert_integrity_error(self, callback):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                callback()

    def test_public_identifiers_and_category_name_are_unique_in_mysql(self):
        self.assert_integrity_error(
            lambda: Category.objects.create(
                name=self.category.name,
                slug="another-category",
            )
        )
        self.assert_integrity_error(
            lambda: Category.objects.create(
                name="键盘",
                slug=self.category.slug,
            )
        )
        self.assert_integrity_error(
            lambda: Product.objects.create(
                category=self.category,
                name="另一个商品",
                slug=self.product.slug,
            )
        )

        sku = SKU.objects.create(
            product=self.product,
            code="M1-BLACK",
            color="黑色",
            price=Decimal("129.00"),
        )
        self.assert_integrity_error(
            lambda: SKU.objects.create(
                product=self.product,
                code=sku.code,
                color="白色",
                price=Decimal("139.00"),
            )
        )

    def test_price_is_exact_decimal_and_mysql_rejects_negative_value(self):
        sku = SKU.objects.create(
            product=self.product,
            code="M1-BLACK",
            price=Decimal("129.00"),
        )
        sku.refresh_from_db()

        self.assertEqual(sku.price, Decimal("129.00"))
        self.assert_integrity_error(
            lambda: SKU.objects.filter(pk=sku.pk).update(
                price=Decimal("-0.01")
            )
        )

    def test_sku_attributes_must_be_a_json_object(self):
        sku = SKU(
            product=self.product,
            code="M1-BLACK",
            price=Decimal("129.00"),
            attributes=["bluetooth", "2.4g"],
        )

        with self.assertRaises(ValidationError) as context:
            sku.full_clean()

        self.assertIn("attributes", context.exception.message_dict)

    def test_catalog_relationships_are_protected_from_cascading_deletes(self):
        sku = SKU.objects.create(
            product=self.product,
            code="M1-BLACK",
            price=Decimal("129.00"),
        )
        Inventory.objects.create(sku=sku)

        with self.assertRaises(ProtectedError):
            self.category.delete()
        with self.assertRaises(ProtectedError):
            self.product.delete()
        with self.assertRaises(ProtectedError):
            sku.delete()
