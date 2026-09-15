from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


User = get_user_model()


class PublicCatalogAPITests(APITestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.category = Category.objects.create(
            name="鼠标",
            slug="mice",
            is_active=True,
        )
        self.hidden_category = Category.objects.create(
            name="未发布分类",
            slug="hidden-category",
            is_active=False,
        )
        self.product = Product.objects.create(
            category=self.category,
            name="无线鼠标 M1",
            slug="wireless-mouse-m1",
            description="双模无线鼠标",
            is_active=True,
        )
        self.empty_product = Product.objects.create(
            category=self.category,
            name="即将补货鼠标",
            slug="restocking-mouse",
            is_active=True,
        )
        self.hidden_product = Product.objects.create(
            category=self.category,
            name="下架鼠标",
            slug="hidden-mouse",
            is_active=False,
        )
        self.hidden_category_product = Product.objects.create(
            category=self.hidden_category,
            name="隐藏分类商品",
            slug="hidden-category-product",
            is_active=True,
        )

        self.black_sku = SKU.objects.create(
            product=self.product,
            code="M1-BLACK",
            color="黑色",
            attributes={"connection": "bluetooth"},
            price=Decimal("129.00"),
            is_active=True,
        )
        Inventory.objects.create(
            sku=self.black_sku,
            available_stock=10,
        )
        self.white_sku = SKU.objects.create(
            product=self.product,
            code="M1-WHITE",
            color="白色",
            attributes={"connection": "2.4g"},
            price=Decimal("139.00"),
            is_active=True,
        )
        Inventory.objects.create(sku=self.white_sku)
        hidden_sku = SKU.objects.create(
            product=self.product,
            code="M1-HIDDEN",
            color="测试色",
            price=Decimal("99.00"),
            is_active=False,
        )
        Inventory.objects.create(
            sku=hidden_sku,
            available_stock=100,
        )

    def assert_no_private_catalog_fields(self, payload):
        def collect_keys(value):
            if isinstance(value, dict):
                return set(value).union(
                    *(collect_keys(item) for item in value.values())
                )
            if isinstance(value, list):
                return set().union(*(collect_keys(item) for item in value))
            return set()

        private_fields = {
            "id",
            "available_stock",
            "reserved_stock",
            "is_active",
            "created_at",
            "updated_at",
        }
        self.assertTrue(private_fields.isdisjoint(collect_keys(payload)))

    def test_guest_lists_only_active_categories_without_session_or_csrf(self):
        response = self.client.get(reverse("catalog:category-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            [{"slug": "mice", "name": "鼠标"}],
        )
        self.assert_no_private_catalog_fields(response.json())

    def test_product_list_is_paginated_and_hides_inactive_content(self):
        response = self.client.get(reverse("catalog:product-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(set(payload), {"count", "next", "previous", "results"})
        self.assertEqual(payload["count"], 2)
        products = {item["slug"]: item for item in payload["results"]}
        self.assertEqual(
            products["wireless-mouse-m1"],
            {
                "slug": "wireless-mouse-m1",
                "name": "无线鼠标 M1",
                "category": {"slug": "mice", "name": "鼠标"},
                "lowest_price": "129.00",
                "in_stock": True,
            },
        )
        self.assertEqual(
            products["restocking-mouse"],
            {
                "slug": "restocking-mouse",
                "name": "即将补货鼠标",
                "category": {"slug": "mice", "name": "鼠标"},
                "lowest_price": None,
                "in_stock": False,
            },
        )
        self.assertNotIn("hidden-mouse", products)
        self.assertNotIn("hidden-category-product", products)
        self.assert_no_private_catalog_fields(payload)

    def test_product_list_can_be_filtered_by_category_slug(self):
        other_category = Category.objects.create(
            name="键盘",
            slug="keyboards",
            is_active=True,
        )
        Product.objects.create(
            category=other_category,
            name="机械键盘 K1",
            slug="mechanical-keyboard-k1",
            is_active=True,
        )

        response = self.client.get(
            reverse("catalog:product-list"),
            {"category": self.category.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["slug"] for item in response.json()["results"]},
            {"wireless-mouse-m1", "restocking-mouse"},
        )

    def test_product_list_has_fixed_page_size_and_bounded_queries(self):
        for index in range(19):
            Product.objects.create(
                category=self.category,
                name=f"测试鼠标 {index:02d}",
                slug=f"test-mouse-{index:02d}",
                is_active=True,
            )

        with CaptureQueriesContext(connection) as queries:
            first_page_response = self.client.get(
                reverse("catalog:product-list"),
                {"page_size": 100},
            )

        self.assertEqual(first_page_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_page_response.json()["count"], 21)
        self.assertEqual(len(first_page_response.json()["results"]), 20)
        self.assertIsNotNone(first_page_response.json()["next"])
        self.assertLessEqual(len(queries), 2)

        second_page_response = self.client.get(
            reverse("catalog:product-list"),
            {"page": 2},
        )
        self.assertEqual(second_page_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(second_page_response.json()["results"]), 1)

    def test_product_detail_returns_only_active_skus_and_boolean_stock(self):
        response = self.client.get(
            reverse(
                "catalog:product-detail",
                kwargs={"slug": self.product.slug},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "slug": "wireless-mouse-m1",
                "name": "无线鼠标 M1",
                "description": "双模无线鼠标",
                "category": {"slug": "mice", "name": "鼠标"},
                "in_stock": True,
                "skus": [
                    {
                        "code": "M1-BLACK",
                        "color": "黑色",
                        "attributes": {"connection": "bluetooth"},
                        "price": "129.00",
                        "in_stock": True,
                    },
                    {
                        "code": "M1-WHITE",
                        "color": "白色",
                        "attributes": {"connection": "2.4g"},
                        "price": "139.00",
                        "in_stock": False,
                    },
                ],
            },
        )
        self.assert_no_private_catalog_fields(response.json())

    def test_missing_inventory_is_safely_reported_as_out_of_stock(self):
        sku_without_inventory = SKU.objects.create(
            product=self.empty_product,
            code="RESTOCK-BLACK",
            color="黑色",
            price=Decimal("199.00"),
            is_active=True,
        )

        response = self.client.get(
            reverse(
                "catalog:product-detail",
                kwargs={"slug": self.empty_product.slug},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json()["skus"],
            [
                {
                    "code": sku_without_inventory.code,
                    "color": "黑色",
                    "attributes": {},
                    "price": "199.00",
                    "in_stock": False,
                }
            ],
        )

    def test_hidden_products_return_404_even_for_staff(self):
        staff_user = User.objects.create_user(
            username="operator_01",
            password="S3cure-staff-pass!",
            is_staff=True,
        )
        self.client.force_authenticate(user=staff_user)

        for slug in (
            self.hidden_product.slug,
            self.hidden_category_product.slug,
            "does-not-exist",
        ):
            with self.subTest(slug=slug):
                response = self.client.get(
                    reverse(
                        "catalog:product-detail",
                        kwargs={"slug": slug},
                    )
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )

    def test_public_endpoints_do_not_accept_writes(self):
        for url in (
            reverse("catalog:category-list"),
            reverse("catalog:product-list"),
            reverse(
                "catalog:product-detail",
                kwargs={"slug": self.product.slug},
            ),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, {}, format="json")
                self.assertEqual(
                    response.status_code,
                    status.HTTP_405_METHOD_NOT_ALLOWED,
                )

    def test_logged_in_session_without_csrf_still_gets_method_not_allowed(self):
        user = User.objects.create_user(
            username="customer_01",
            password="S3cure-customer-pass!",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("catalog:product-list"),
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_product_detail_query_count_does_not_grow_with_skus(self):
        for index in range(5):
            SKU.objects.create(
                product=self.product,
                code=f"M1-EXTRA-{index}",
                price=Decimal("149.00"),
                is_active=True,
            )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(
                reverse(
                    "catalog:product-detail",
                    kwargs={"slug": self.product.slug},
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(queries), 2)
