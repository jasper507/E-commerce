import re

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.accounts.models import Address, recipient_phone_validator


User = get_user_model()
username_model_field = User._meta.get_field("username")


class NormalizedUsernameField(serializers.CharField):
    def to_internal_value(self, data):
        username = super().to_internal_value(data)
        return User.normalize_username(username)


class RegistrationSerializer(serializers.ModelSerializer):
    username = NormalizedUsernameField(
        max_length=username_model_field.max_length,
        validators=[
            *username_model_field.validators,
            UniqueValidator(
                queryset=User.objects.all(),
                message=username_model_field.error_messages["unique"],
            ),
        ],
    )
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    class Meta:
        model = User
        fields = ("id", "username", "password")
        read_only_fields = ("id",)

    def validate(self, attrs):
        candidate_user = User(username=attrs["username"])

        try:
            password_validation.validate_password(
                attrs["password"],
                user=candidate_user,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"password": list(exc.messages)}
            ) from exc

        return attrs

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return User.objects.create_user(**validated_data)
        except IntegrityError as exc:
            username = validated_data["username"]
            if User.objects.filter(username=username).exists():
                raise serializers.ValidationError(
                    {
                        "username": [
                            username_model_field.error_messages["unique"]
                        ]
                    }
                ) from exc

            raise


class LoginSerializer(serializers.Serializer):
    username = NormalizedUsernameField()
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )


class SessionUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "is_staff")


def normalize_recipient_phone(value):
    compact = re.sub(r"[\s-]+", "", value)
    if compact.startswith("+86"):
        compact = compact[3:]
    return compact


class RecipientPhoneField(serializers.CharField):
    default_error_messages = {
        "invalid": "Enter a valid 11-digit mainland China mobile number.",
    }

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        normalized = normalize_recipient_phone(value)
        try:
            recipient_phone_validator(normalized)
        except DjangoValidationError:
            self.fail("invalid")
        return normalized


class AddressWriteSerializer(serializers.Serializer):
    recipient_name = serializers.CharField(max_length=50)
    phone = RecipientPhoneField(max_length=32)
    province = serializers.CharField(max_length=50)
    city = serializers.CharField(max_length=50)
    district = serializers.CharField(max_length=50)
    detail = serializers.CharField(max_length=255)
    is_default = serializers.BooleanField(required=False)


class AddressSerializer(serializers.ModelSerializer):
    is_default = serializers.SerializerMethodField()

    class Meta:
        model = Address
        fields = (
            "id",
            "recipient_name",
            "phone",
            "province",
            "city",
            "district",
            "detail",
            "is_default",
        )

    def get_is_default(self, address):
        return address.is_default
