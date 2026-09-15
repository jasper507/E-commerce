from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


User = get_user_model()


class SKUAdminTests(TestCase):
    def setUp(self):
        category = Category.objects.create(
            name="鼠标",
            slug="mice",
            is_active=True,
        )
        self.product = Product.objects.create(
            category=category,
            name="无线鼠标 M1",
            slug="wireless-mouse-m1",
            is_active=True,
        )
        self.staff = User.objects.create_user(
            username="catalog_operator",
            password="S3cure-staff-pass!",
            is_staff=True,
        )
        self.add_sku_permission = Permission.objects.get(
            content_type__app_label="catalog",
            codename="add_sku",
        )
        self.add_inventory_permission = Permission.objects.get(
            content_type__app_label="inventory",
            codename="add_inventory",
        )
        self.staff.user_permissions.add(self.add_sku_permission)
        self.client.force_login(self.staff)

    def test_staff_cannot_open_sku_add_page_without_inventory_add_permission(self):
        response = self.client.get(reverse("admin:catalog_sku_add"))

        self.assertEqual(response.status_code, 403)

    def test_staff_with_both_permissions_creates_sku_and_inventory_together(self):
        self.staff.user_permissions.add(self.add_inventory_permission)

        response = self.client.post(
            reverse("admin:catalog_sku_add"),
            {
                "product": self.product.pk,
                "code": "M1-BLACK",
                "color": "黑色",
                "attributes": '{"connection": "bluetooth"}',
                "price": str(Decimal("129.00")),
                "is_active": "on",
                "inventory-TOTAL_FORMS": "1",
                "inventory-INITIAL_FORMS": "0",
                "inventory-MIN_NUM_FORMS": "1",
                "inventory-MAX_NUM_FORMS": "1",
                "inventory-0-available_stock": "7",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        sku = SKU.objects.get(code="M1-BLACK")
        self.assertEqual(sku.inventory.available_stock, 7)
        self.assertEqual(sku.inventory.reserved_stock, 0)
