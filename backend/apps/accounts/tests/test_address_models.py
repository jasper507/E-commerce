from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Address


User = get_user_model()


class AddressModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="customer_01",
            password="S3cure-customer-pass!",
        )

    def address_fields(self, suffix="一"):
        return {
            "user": self.user,
            "recipient_name": f"测试用户{suffix}",
            "phone": "13800138000",
            "province": "广东省",
            "city": "深圳市",
            "district": "南山区",
            "detail": f"科技园{suffix}号",
        }

    def assert_database_error(self, callback, error_class=DatabaseError):
        with self.assertRaises(error_class):
            with transaction.atomic():
                callback()

    def test_mysql_allows_at_most_one_default_address_per_user(self):
        Address.objects.create(default_marker=1, **self.address_fields())

        self.assert_database_error(
            lambda: Address.objects.create(
                default_marker=1,
                **self.address_fields("二"),
            ),
            IntegrityError,
        )

    def test_multiple_non_default_addresses_are_allowed(self):
        Address.objects.create(default_marker=None, **self.address_fields())
        Address.objects.create(
            default_marker=None,
            **self.address_fields("二"),
        )

        self.assertEqual(Address.objects.count(), 2)

    def test_mysql_rejects_an_unknown_default_marker(self):
        self.assert_database_error(
            lambda: Address.objects.create(
                default_marker=2,
                **self.address_fields(),
            )
        )

    def test_model_validation_rejects_an_invalid_normalized_phone(self):
        address = Address(phone="12800138000", **{
            key: value
            for key, value in self.address_fields().items()
            if key != "phone"
        })

        with self.assertRaises(ValidationError) as context:
            address.full_clean()

        self.assertIn("phone", context.exception.message_dict)
