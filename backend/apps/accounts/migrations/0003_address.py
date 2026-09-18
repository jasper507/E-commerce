# Generated for the MVP-2 minimum address slice.

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_alter_user_username"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Address",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("recipient_name", models.CharField(max_length=50)),
                (
                    "phone",
                    models.CharField(
                        max_length=11,
                        validators=[
                            django.core.validators.RegexValidator(
                                code="invalid_recipient_phone",
                                message=(
                                    "Enter a valid 11-digit mainland China "
                                    "mobile number."
                                ),
                                regex="^1[3-9][0-9]{9}$",
                            )
                        ],
                    ),
                ),
                ("province", models.CharField(max_length=50)),
                ("city", models.CharField(max_length=50)),
                ("district", models.CharField(max_length=50)),
                ("detail", models.CharField(max_length=255)),
                (
                    "default_marker",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        editable=False,
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="addresses",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-default_marker", "created_at", "id"),
                "constraints": (
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
                ),
            },
        ),
    ]
