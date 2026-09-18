from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase

from apps.catalog.models import Category, Product, SKU
from apps.ordering.models import Cart, CartItem


User = get_user_model()


class CartModelTests(TestCase):
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
        self.cart = Cart.objects.create(user=self.user)

    def assert_database_error(self, callback, error_class=DatabaseError):
        with self.assertRaises(error_class):
            with transaction.atomic():
                callback()

    def test_each_user_has_at_most_one_cart(self):
        self.assert_database_error(
            lambda: Cart.objects.create(user=self.user),
            IntegrityError,
        )

    def test_same_sku_can_appear_only_once_in_a_cart(self):
        CartItem.objects.create(cart=self.cart, sku=self.sku, quantity=1)

        self.assert_database_error(
            lambda: CartItem.objects.create(
                cart=self.cart,
                sku=self.sku,
                quantity=2,
            ),
            IntegrityError,
        )

    def test_mysql_enforces_quantity_range(self):
        for quantity in (0, 100):
            with self.subTest(quantity=quantity):
                self.assert_database_error(
                    lambda quantity=quantity: CartItem.objects.create(
                        cart=self.cart,
                        sku=self.sku,
                        quantity=quantity,
                    )
                )
