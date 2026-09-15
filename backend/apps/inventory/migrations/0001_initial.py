import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Inventory",
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
                ("available_stock", models.PositiveIntegerField(default=0)),
                ("reserved_stock", models.PositiveIntegerField(default=0)),
                (
                    "sku",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="inventory",
                        to="catalog.sku",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(available_stock__gte=0),
                        name="inventory_available_gte_0",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(reserved_stock__gte=0),
                        name="inventory_reserved_gte_0",
                    ),
                ]
            },
        ),
    ]
