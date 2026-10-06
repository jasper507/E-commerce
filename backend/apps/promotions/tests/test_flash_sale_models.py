"""直接写入购买记录，独立验证 MySQL 约束，不经过下单服务或用户行锁。"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Category, Product, SKU
from apps.ordering.models import Order
from apps.promotions.models import FlashSale, FlashSaleItem, FlashSalePurchase


User = get_user_model()


class FlashSalePurchaseModelTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.vendor, "mysql", "购买记录约束必须由真实 MySQL 验证")
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        self.customer = User.objects.create_user(username="fs_model_customer")
        category = Category.objects.create(
            name="TEST ONLY 秒杀约束分类",
            slug="test-only-flash-sale-models",
            is_active=True,
        )
        product = Product.objects.create(
            category=category,
            name="TEST ONLY 秒杀约束鼠标",
            slug="test-only-flash-sale-model-mouse",
            is_active=True,
        )
        sku = SKU.objects.create(
            product=product,
            code="TEST-ONLY-FLASH-SALE-MODELS",
            price=Decimal("129.00"),
            is_active=True,
        )
        now = timezone.now()
        flash_sale = FlashSale.objects.create(
            name="TEST ONLY 购买记录约束活动",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
            is_active=True,
        )
        self.flash_sale_item = FlashSaleItem.objects.create(
            flash_sale=flash_sale,
            sku=sku,
            price=Decimal("89.00"),
            is_active=True,
        )
        self.order_number = 0

    def _create_purchase(self, *, customer, status, blocking_marker):
        self.order_number += 1
        order_status = {
            FlashSalePurchase.Status.HELD: Order.Status.PENDING_PAYMENT,
            FlashSalePurchase.Status.CONSUMED: Order.Status.PAID,
            FlashSalePurchase.Status.RELEASED: Order.Status.CANCELED,
        }.get(status, Order.Status.PENDING_PAYMENT)
        order = Order.objects.create(
            user=customer,
            status=order_status,
            creation_scope=Order.CreationScope.FLASH_SALE,
            subtotal=Decimal("89.00"),
            total=Decimal("89.00"),
            idempotency_key=f"test-only-model-order-{self.order_number}",
            request_fingerprint="f" * 64,
        )
        return FlashSalePurchase.objects.create(
            customer=customer,
            flash_sale_item=self.flash_sale_item,
            order=order,
            status=status,
            blocking_marker=blocking_marker,
        )

    def test_mysql_held_and_consumed_share_one_slot_with_multiple_released_histories(self):
        for blocking_status in (
            FlashSalePurchase.Status.HELD,
            FlashSalePurchase.Status.CONSUMED,
        ):
            with self.subTest(blocking_status=blocking_status):
                customer = User.objects.create_user(
                    username=f"fs_model_{blocking_status.lower()}"
                )
                for _ in range(2):
                    self._create_purchase(
                        customer=customer,
                        status=FlashSalePurchase.Status.RELEASED,
                        blocking_marker=None,
                    )
                self._create_purchase(
                    customer=customer,
                    status=blocking_status,
                    blocking_marker=1,
                )

                for duplicate_status in (
                    FlashSalePurchase.Status.HELD,
                    FlashSalePurchase.Status.CONSUMED,
                ):
                    with self.subTest(duplicate_status=duplicate_status):
                        with self.assertRaises(IntegrityError) as raised:
                            with transaction.atomic():
                                self._create_purchase(
                                    customer=customer,
                                    status=duplicate_status,
                                    blocking_marker=1,
                                )
                        self.assertEqual(raised.exception.args[0], 1062)
                        self.assertIn(
                            "promotions_purchase_one_blocking", str(raised.exception)
                        )

                records = FlashSalePurchase.objects.filter(customer=customer)
                self.assertEqual(records.count(), 3)
                self.assertEqual(
                    records.filter(status="RELEASED", blocking_marker__isnull=True).count(),
                    2,
                )
                self.assertEqual(records.filter(blocking_marker=1).count(), 1)

        self.assertEqual(FlashSalePurchase.objects.count(), 6)
        self.assertEqual(Order.objects.count(), 6)

    def test_mysql_rejects_invalid_status_marker_pairs_and_allows_valid_pairs(self):
        invalid_pairs = (
            (FlashSalePurchase.Status.HELD, None),
            (FlashSalePurchase.Status.CONSUMED, None),
            (FlashSalePurchase.Status.RELEASED, 1),
            ("NOT_A_STATUS", 1),
            (FlashSalePurchase.Status.HELD, 2),
        )
        for status, marker in invalid_pairs:
            with self.subTest(status=status, blocking_marker=marker):
                with self.assertRaises(DatabaseError) as raised:
                    with transaction.atomic():
                        self._create_purchase(
                            customer=self.customer,
                            status=status,
                            blocking_marker=marker,
                        )
                self.assertIn(
                    "promotions_purchase_status_marker_consistent", str(raised.exception)
                )
                self.assertEqual(FlashSalePurchase.objects.count(), 0)
                self.assertEqual(Order.objects.count(), 0)

        valid_pairs = (
            (FlashSalePurchase.Status.HELD, 1),
            (FlashSalePurchase.Status.CONSUMED, 1),
            (FlashSalePurchase.Status.RELEASED, None),
        )
        for status, marker in valid_pairs:
            with self.subTest(status=status, blocking_marker=marker):
                customer = User.objects.create_user(
                    username=f"fs_valid_{status.lower()}"
                )
                purchase = self._create_purchase(
                    customer=customer,
                    status=status,
                    blocking_marker=marker,
                )
                purchase.refresh_from_db()
                self.assertEqual(purchase.status, status)
                self.assertEqual(purchase.blocking_marker, marker)

        self.assertEqual(FlashSalePurchase.objects.count(), 3)
