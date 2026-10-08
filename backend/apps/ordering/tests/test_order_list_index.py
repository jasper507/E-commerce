"""Migration lifecycle and an opt-in plan check on disposable MySQL."""

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product, SKU
from apps.ordering.models import AddressSnapshot, Order, OrderItem
from apps.ordering.tests.test_order_query_baseline import (
    analyze_tables,
    assert_unindexed_baseline,
    check_payload,
    create_read_fixture,
    indexes,
    measure,
    plans,
)


INDEX_NAME = "ordering_order_user_created_id"
OLD_TARGET = [("ordering", "0004_order_creation_scope")]
NEW_TARGET = [("ordering", "0005_order_list_index")]
VERIFY_PLANS = os.getenv("PROJECT_ONE_ORDER_INDEX_VERIFY") == "1"

if VERIFY_PLANS and (
    settings.ENVIRONMENT != "test"
    or settings.DATABASES["default"]["TEST"]["NAME"] != "test_project_one"
    or settings.DATABASES["default"]["NAME"] == "test_project_one"
):
    raise RuntimeError("Order-index plan verification requires its disposable test_project_one database.")


def order_constraints():
    with connection.cursor() as cursor:
        return connection.introspection.get_constraints(cursor, Order._meta.db_table)


def assert_index_present():
    actual = order_constraints()[INDEX_NAME]
    assert actual["columns"] == ["user_id", "created_at", "id"]
    assert actual["index"] and not actual["unique"]
    unique = order_constraints()["ordering_order_unique_user_scope_key"]
    assert unique["columns"] == ["user_id", "creation_scope", "idempotency_key"]
    assert unique["unique"]


def data_snapshot():
    return [list(model.objects.order_by("id").values()) for model in (Order, AddressSnapshot, OrderItem)]


@pytest.mark.django_db(transaction=True)
def test_index_migration_round_trip_preserves_existing_orders():
    assert connection.vendor == "mysql" and connection.settings_dict["NAME"].startswith("test_")
    MigrationExecutor(connection).migrate(OLD_TARGET)
    try:
        assert INDEX_NAME not in order_constraints()
        assert_unindexed_baseline()
        user = get_user_model().objects.create_user(username="index_migration_owner")
        category = Category.objects.create(name="迁移分类", slug="index-migration-category")
        product = Product.objects.create(category=category, name="迁移商品", slug="index-migration-product")
        sku = SKU.objects.create(product=product, code="INDEX-MIGRATION-SKU", price=Decimal("1.00"))
        for i in range(2):
            order = Order.objects.create(
                user=user, subtotal=Decimal("1.00"), total=Decimal("1.00"),
                idempotency_key=f"migration-{i}", request_fingerprint="a" * 64,
            )
            AddressSnapshot.objects.create(
                order=order, recipient_name="迁移测试", phone="13800138000",
                province="广东省", city="深圳市", district="南山区", detail="原地址快照",
            )
            OrderItem.objects.create(
                order=order, sku=sku, product_name_snapshot="原商品快照", sku_code_snapshot=sku.code,
                unit_price=Decimal("1.00"), quantity=1, line_total=Decimal("1.00"),
            )
        before = data_snapshot()
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(reverse("orders:order-list"))
        assert response.status_code == 200
        before_body = response.content
        for target, present in ((NEW_TARGET, True), (OLD_TARGET, False), (NEW_TARGET, True)):
            MigrationExecutor(connection).migrate(target)
            assert data_snapshot() == before
            assert client.get(reverse("orders:order-list")).content == before_body
            if present:
                assert_index_present()
                with pytest.raises(RuntimeError, match="not the unindexed baseline"):
                    assert_unindexed_baseline()
            else:
                assert INDEX_NAME not in order_constraints()
                assert_unindexed_baseline()
    finally:
        MigrationExecutor(connection).migrate(NEW_TARGET)
    assert_index_present()


@pytest.mark.skipif(not VERIFY_PLANS, reason="explicit formal-index plan verification only")
@pytest.mark.django_db(transaction=True)
def test_formal_index_uses_verified_order_list_path():
    assert connection.vendor == "mysql" and connection.settings_dict["NAME"] == "test_project_one"
    output = Path(os.environ["PROJECT_ONE_ORDER_INDEX_OUTPUT"])
    assert not output.exists(), "Choose a new evidence file."
    assert_index_present()
    owner, expected = create_read_fixture()
    analyze_tables()
    client = APIClient()
    client.force_authenticate(user=owner)
    shapes = {}
    for scenario, page in (("first", 1), ("deep", 201)):
        sample, queries, payload = measure(owner, scenario, expected)
        query_plans = plans(queries)
        order_plan = query_plans[1]
        assert order_plan["estimated"][0]["key"] == INDEX_NAME
        assert INDEX_NAME in order_plan["actual_tree"]
        assert "Sort:" not in order_plan["actual_tree"]
        response = client.get(reverse("orders:order-list"), {"page": page})
        assert response.status_code == 200
        check_payload(response.json(), scenario, expected)
        assert response.json() == payload
        shapes[scenario] = {"sample": sample, "queries": queries, "plans": query_plans}
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION(), DATABASE()")
        version, database = cursor.fetchone()
    files = (
        Path(__file__), Path(__file__).with_name("test_order_query_baseline.py"),
        Path("backend/apps/ordering/models.py"),
        Path("backend/apps/ordering/migrations/0005_order_list_index.py"),
    )
    result = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "mysql": version, "database": database,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "migration": NEW_TARGET, "indexes": indexes(), "shapes": shapes,
        "method": "one observation per page, no latency threshold, no FORCE INDEX; timings are not a benchmark",
        "fixture": "5000 owner + 5000 other orders, 10000 addresses, 20000 items; fixed time permutation/ties",
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Formal migration index selected for first and deep pages; snapshots and ownership preserved.")
