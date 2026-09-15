from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase


User = get_user_model()


class UsernamePolicyTests(TestCase):
    def assert_username_is_invalid(self, username):
        user = User(
            username=username,
            password="test-password",
        )

        with self.assertRaises(ValidationError) as context:
            user.full_clean()

        self.assertIn("username", context.exception.message_dict)
        self.assertIn(
            "invalid_username",
            {
                error.code
                for error in context.exception.error_dict["username"]
            },
        )

    def test_username_is_trimmed_and_lowercased(self):
        user = User.objects.create_user(
            username="  Alice_01  ",
            password="test-password",
        )
        user.refresh_from_db()

        self.assertEqual(user.username, "alice_01")

    def test_direct_save_also_normalizes_username(self):
        user = User(username="  Bob_02  ")
        user.set_password("test-password")

        user.save()
        user.refresh_from_db()

        self.assertEqual(user.username, "bob_02")

    def test_username_cannot_start_with_number(self):
        self.assert_username_is_invalid("1alice")

    def test_username_cannot_be_shorter_than_three_characters(self):
        self.assert_username_is_invalid("ab")

    def test_username_cannot_contain_hyphen(self):
        self.assert_username_is_invalid("alice-01")

    def test_username_cannot_be_longer_than_thirty_characters(self):
        self.assert_username_is_invalid("a" + "b" * 30)

    def test_three_character_username_is_valid(self):
        user = User(
            username="abc",
            password="test-password",
        )

        user.full_clean()

        self.assertEqual(user.username, "abc")

    def test_thirty_character_username_is_valid(self):
        username = "a" + "b" * 29
        user = User(
            username=username,
            password="test-password",
        )

        user.full_clean()

        self.assertEqual(user.username, username)

    def test_username_is_unique_after_normalization(self):
        User.objects.create_user(
            username="Alice",
            password="test-password",
        )
        duplicate_user = User(
            username=" alice ",
            password="test-password",
        )

        with self.assertRaises(ValidationError) as context:
            duplicate_user.full_clean()

        self.assertEqual(duplicate_user.username, "alice")
        self.assertTrue(
            any(
                error.code == "unique"
                for error in context.exception.error_dict["username"]
            )
        )

    def test_database_rejects_username_duplicate_after_normalization(self):
        User.objects.create_user(
            username="Alice",
            password="test-password",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username=" alice ",
                    password="test-password",
                )

    def test_superuser_username_is_trimmed_and_lowercased(self):
        user = User.objects.create_superuser(
            username="  Root_01  ",
            password="test-password",
        )
        user.refresh_from_db()

        self.assertEqual(user.username, "root_01")
