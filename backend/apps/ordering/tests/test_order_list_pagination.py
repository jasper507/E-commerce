from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import Category, Product, SKU
from apps.ordering.models import AddressSnapshot, Order, OrderItem


User = get_user_model()


class OrderListPaginationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="pagination_owner")
        cls.other_user = User.objects.create_user(username="pagination_other")
        category = Category.objects.create(name="测试分类", slug="pagination-category")
        product = Product.objects.create(
            category=category, name="测试商品", slug="pagination-product",
        )
        sku = SKU.objects.create(product=product, code="PAGINATION-SKU", price=Decimal("1.00"))
        cls.orders = []
        for user, count in ((cls.user, 21), (cls.other_user, 2)):
            for index in range(count):
                order = Order.objects.create(
                    user=user,
                    subtotal=Decimal("1.00"),
                    total=Decimal("1.00"),
                    idempotency_key=f"pagination-{index}",
                    request_fingerprint="a" * 64,
                )
                AddressSnapshot.objects.create(
                    order=order,
                    recipient_name=user.username,
                    phone="13800138000",
                    province="广东省",
                    city="深圳市",
                    district="南山区",
                    detail="分页测试地址",
                )
                OrderItem.objects.create(
                    order=order,
                    sku=sku,
                    product_name_snapshot="下单时商品名称",
                    sku_code_snapshot=sku.code,
                    unit_price=Decimal("1.00"),
                    quantity=1,
                    line_total=Decimal("1.00"),
                )
                if user == cls.user:
                    cls.orders.append(order)
        # 相同时间跨越分页边界，验证 ID 排序能让静态数据的两页不重复、不漏单。
        cls.created_at = timezone.now()
        Order.objects.filter(user=cls.user).update(created_at=cls.created_at)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("orders:order-list")

    def test_twenty_one_orders_have_stable_pages_and_complete_snapshots(self):
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        first_page = first.json()
        self.assertEqual(first_page["count"], 21)
        self.assertIsNone(first_page["previous"])
        self.assertEqual(parse_qs(urlsplit(first_page["next"]).query), {"page": ["2"]})

        second = self.client.get(self.url, {"page": 2})
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        second_page = second.json()
        self.assertEqual(second_page["count"], 21)
        self.assertIsNone(second_page["next"])
        self.assertIsNotNone(second_page["previous"])
        self.assertEqual(len(first_page["results"]), 20)
        self.assertEqual(len(second_page["results"]), 1)
        actual_ids = [
            order["public_id"]
            for order in first_page["results"] + second_page["results"]
        ]
        self.assertEqual(actual_ids, [str(order.public_id) for order in reversed(self.orders)])
        self.assertEqual(len(set(actual_ids)), 21)

        last_order = second_page["results"][0]
        self.assertEqual(last_order["address"]["recipient_name"], self.user.username)
        self.assertEqual(last_order["items"][0]["product_name_snapshot"], "下单时商品名称")
        self.assertEqual(last_order["items"][0]["line_total"], "1.00")
        detail = self.client.get(
            reverse("orders:order-detail", kwargs={"public_id": last_order["public_id"]}),
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.json(), last_order)

    def test_created_time_takes_priority_over_id(self):
        earliest_id = self.orders[0]
        Order.objects.filter(pk=earliest_id.pk).update(
            created_at=self.created_at + timedelta(days=1),
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["results"][0]["public_id"], str(earliest_id.public_id))

    def test_count_and_results_cannot_be_changed_to_another_user(self):
        response = self.client.get(self.url, {"user_id": self.other_user.pk, "page_size": 1000})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["count"], 21)
        self.assertEqual(len(response.json()["results"]), 20)
        self.assertTrue(all(
            order["address"]["recipient_name"] == self.user.username
            for order in response.json()["results"]
        ))

        self.client.force_login(self.other_user)
        other_page = self.client.get(self.url)
        self.assertEqual(other_page.json()["count"], 2)
        self.assertEqual(len(other_page.json()["results"]), 2)
        forbidden_detail = self.client.get(
            reverse("orders:order-detail", kwargs={"public_id": self.orders[0].public_id}),
        )
        self.assertEqual(forbidden_detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_empty_first_page_is_successful(self):
        Order.objects.filter(user=self.user).delete()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {"count": 0, "next": None, "previous": None, "results": []},
        )
        self.assertEqual(self.client.get(self.url, {"page": 2}).status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_and_out_of_range_pages_return_not_found(self):
        for page in ("0", "-1", "1.5", "abc", "last", "3", "999999999999999999999"):
            with self.subTest(page=page):
                response = self.client.get(self.url, {"page": page})
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertIn("detail", response.json())

    def test_data_query_count_does_not_grow_with_the_page_length(self):
        # 此处隔离认证查询，只保护订单统计、地址 JOIN 与明细预加载的查询预算。
        self.client.force_authenticate(user=self.user)
        for page, size in ((1, 20), (2, 1)):
            with self.subTest(page=page), self.assertNumQueries(3):
                response = self.client.get(self.url, {"page": page})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(len(response.json()["results"]), size)
                self.assertTrue(all(order["address"] and order["items"] for order in response.json()["results"]))
