from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Project-owned user model, established before the first migration."""

    pass
