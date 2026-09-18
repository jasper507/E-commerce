from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from apps.accounts.address_services import (
    create_address,
    set_default_address,
)
from apps.accounts.models import Address


User = get_user_model()


class AddressConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="customer_01",
            password="S3cure-customer-pass!",
        )
        common_fields = {
            "phone": "13800138000",
            "province": "广东省",
            "city": "深圳市",
            "district": "南山区",
        }
        self.first = create_address(
            user=self.user,
            recipient_name="测试用户一",
            detail="科技园一号",
            **common_fields,
        )
        self.second = create_address(
            user=self.user,
            recipient_name="测试用户二",
            detail="科技园二号",
            **common_fields,
        )

    def test_concurrent_default_switches_leave_exactly_one_default(self):
        barrier = Barrier(2)
        errors = []

        def switch_default(address_id):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                barrier.wait(timeout=5)
                set_default_address(user=user, address_id=address_id)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = (
            Thread(target=switch_default, args=(self.first.pk,)),
            Thread(target=switch_default, args=(self.second.pk,)),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        if errors:
            raise errors[0]
        self.assertEqual(
            Address.objects.filter(user=self.user, default_marker=1).count(),
            1,
        )
