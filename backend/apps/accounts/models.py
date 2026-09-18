from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


username_validator = RegexValidator(
    regex=r"^[a-zA-Z][a-zA-Z0-9_]{2,29}$",
    message=_(
        "Enter a username with 3 to 30 letters, numbers, or underscores, "
        "starting with a letter."
    ),
    code="invalid_username",
)

recipient_phone_validator = RegexValidator(
    regex=r"^1[3-9][0-9]{9}$",
    message=_("Enter a valid 11-digit mainland China mobile number."),
    code="invalid_recipient_phone",
)


class User(AbstractUser):
    username = models.CharField(
        _("username"),
        max_length=30,
        unique=True,
        help_text=_(
            "Required. 3 to 30 letters, numbers, or underscores. "
            "Must start with a letter. Uppercase letters are stored as lowercase."
        ),
        validators=[username_validator],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )

    @classmethod
    def normalize_username(cls, username):
        username = super().normalize_username(username)
        if username:
            return username.strip().lower()

        return username

    def full_clean(self, *args, **kwargs):
        self.username = self.normalize_username(self.username)
        return super().full_clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.username = self.normalize_username(self.username)
        return super().save(*args, **kwargs)


class Address(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    recipient_name = models.CharField(max_length=50)
    phone = models.CharField(
        max_length=11,
        validators=(recipient_phone_validator,),
    )
    province = models.CharField(max_length=50)
    city = models.CharField(max_length=50)
    district = models.CharField(max_length=50)
    detail = models.CharField(max_length=255)
    # MySQL UNIQUE constraints allow multiple NULL values. Storing 1 only for
    # the default row lets the database enforce at most one default per user.
    default_marker = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-default_marker", "created_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(default_marker__isnull=True)
                    | models.Q(default_marker=1)
                ),
                name="accounts_address_default_marker_null_or_1",
            ),
            models.UniqueConstraint(
                fields=("user", "default_marker"),
                name="accounts_address_one_default_per_user",
            ),
        )

    @property
    def is_default(self):
        return self.default_marker == 1

    def __str__(self):
        return f"{self.user.username}: {self.recipient_name}"
