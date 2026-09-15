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
