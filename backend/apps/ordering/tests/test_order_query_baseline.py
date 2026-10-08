"""Explicit, disposable-MySQL experiment; never a latency threshold in normal CI."""

import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

import django
import pytest
import rest_framework
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.urls import reverse
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from apps.catalog.models import Category, Product, SKU
from apps.ordering.models import AddressSnapshot, Order, OrderItem
from apps.ordering.serializers import OrderSerializer
from apps.ordering.views import OrderCollectionView, OrderPagination, orders_for


ENABLED = os.getenv("PROJECT_ONE_ORDER_QUERY_BASELINE") == "1"
TEST_DATABASE = "test_project_one"
INDEX_NAME = "experiment_order_user_created_id"
SCENARIOS = ("all", "first", "deep")
PAGES = {"first": 1, "deep": 201}
SAMPLES = 5
OWNER_ORDERS = 5000
TABLES = (Order._meta.db_table, AddressSnapshot._meta.db_table, OrderItem._meta.db_table)

pytestmark = [
    pytest.mark.skipif(not ENABLED, reason="explicit order-query experiment only"),
    pytest.mark.django_db(transaction=True),
]

# This runs before pytest-django may create/drop a database, not just before DDL.
if ENABLED and (
    settings.ENVIRONMENT != "test"
    or settings.DATABASES["default"]["TEST"]["NAME"] != TEST_DATABASE
    or settings.DATABASES["default"]["NAME"] == TEST_DATABASE
):
    raise RuntimeError("Order query experiment requires DJANGO_ENV=test and test_project_one.")


def capture_queries(records):
    def wrapper(execute, sql, params, many, context):
        started = perf_counter()
        result = execute(sql, params, many, context)
        records.append({
            "sql": sql,
            "params": list(params or ()),
            "execute_ms": (perf_counter() - started) * 1000,
        })
        return result
    return wrapper


def rows_as_dicts(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def indexes():
    result = {}
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"SHOW INDEX FROM `{table}`")
            result[table] = rows_as_dicts(cursor)
    return result


def analyze_tables():
    with connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f"ANALYZE TABLE `{table}`")
            assert all(row[2:] == ("status", "OK") for row in cursor.fetchall())


def plans(queries):
    result = []
    with connection.cursor() as cursor:
        for query in queries:
            assert query["sql"].startswith("SELECT")
            cursor.execute("EXPLAIN " + query["sql"], query["params"])
            estimated = rows_as_dicts(cursor)
            cursor.execute("EXPLAIN ANALYZE " + query["sql"], query["params"])
            result.append({
                "sql": query["sql"], "params": query["params"],
                "estimated": estimated, "actual_tree": "\n".join(row[0] for row in cursor.fetchall()),
            })
    return result


def create_read_fixture():
    owner = get_user_model().objects.create_user(username="query_owner")
    other = get_user_model().objects.create_user(username="query_other")
    category = Category.objects.create(name="查询实验分类", slug="query-category")
    product = Product.objects.create(category=category, name="查询实验商品", slug="query-product")
    skus = [SKU.objects.create(product=product, code=f"QUERY-SKU-{i}", price=Decimal("1.00")) for i in range(2)]
    pending = [
        Order(
            user=user, public_id=uuid5(NAMESPACE_URL, f"project-one-query/{user.username}/{i}"),
            subtotal=Decimal("2.00"), total=Decimal("2.00"),
            idempotency_key=f"query-{i}", request_fingerprint="a" * 64,
        )
        for user in (owner, other) for i in range(OWNER_ORDERS)
    ]
    Order.objects.bulk_create(pending, batch_size=500)
    orders = list(Order.objects.order_by("id"))
    AddressSnapshot.objects.bulk_create([
        AddressSnapshot(
            order=order, recipient_name="query_owner" if order.user_id == owner.pk else "query_other",
            phone="13800138000", province="广东省", city="深圳市", district="南山区", detail="合成查询地址",
        ) for order in orders
    ], batch_size=500)
    OrderItem.objects.bulk_create([
        OrderItem(
            order=order, sku=sku, product_name_snapshot="下单时商品名称",
            sku_code_snapshot=sku.code, unit_price=Decimal("1.00"), quantity=1, line_total=Decimal("1.00"),
        ) for order in orders for sku in skus
    ], batch_size=500)
    # Explicitly replace auto_now_add timestamps. The permutation tests time priority and ties.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE ordering_order SET created_at = TIMESTAMPADD(SECOND, MOD(id * 37, 503), %s)",
            [datetime(2026, 1, 1)],
        )
    expected = sorted(
        [order for order in orders if order.user_id == owner.pk],
        key=lambda order: ((order.pk * 37) % 503, order.pk), reverse=True,
    )
    assert len(expected) == OWNER_ORDERS
    assert len({(order.pk * 37) % 503 for order in expected}) < OWNER_ORDERS
    return owner, expected


def check_payload(payload, scenario, expected):
    if scenario == "all":
        rows, wanted = payload, expected
    else:
        offset = (PAGES[scenario] - 1) * OrderPagination.page_size
        rows, wanted = payload["results"], expected[offset:offset + OrderPagination.page_size]
        assert payload["count"] == OWNER_ORDERS
    assert [row["public_id"] for row in rows] == [str(order.public_id) for order in wanted]
    for row, order in zip(rows, wanted):
        assert row["address"]["recipient_name"] == "query_owner"
        assert row["address"]["detail"] == "合成查询地址"
        assert row["total"] == "2.00"
        assert datetime.fromisoformat(row["created_at"]).timestamp() == (
            datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() + (order.pk * 37) % 503
        )
        assert [item["sku_code_snapshot"] for item in row["items"]] == ["QUERY-SKU-0", "QUERY-SKU-1"]
        assert all(item["product_name_snapshot"] == "下单时商品名称" and item["line_total"] == "1.00" for item in row["items"])
    return len(rows), sum(len(row["items"]) for row in rows)


def measure(user, scenario, expected):
    request = Request(APIRequestFactory().get(reverse("orders:order-list"), {"page": PAGES.get(scenario, 1)}))
    queries = []
    with connection.execute_wrapper(capture_queries(queries)):
        started = perf_counter()
        if scenario == "all":
            page = list(orders_for(user))
        else:
            paginator = OrderPagination()
            page = paginator.paginate_queryset(orders_for(user), request, view=OrderCollectionView())
        materialize_ms = (perf_counter() - started) * 1000
        before_serialization = len(queries)
        started = perf_counter()
        data = OrderSerializer(page, many=True).data
        payload = data if scenario == "all" else paginator.get_paginated_response(data).data
        serialize_ms = (perf_counter() - started) * 1000
        assert len(queries) == before_serialization
        started = perf_counter()
        body = JSONRenderer().render(payload)
        render_ms = (perf_counter() - started) * 1000
    orders_count, items_count = check_payload(payload, scenario, expected)
    assert len(queries) == (2 if scenario == "all" else 3)
    sample = {
        "materialize_ms": materialize_ms, "serialize_ms": serialize_ms, "render_ms": render_ms,
        "total_stage_ms": materialize_ms + serialize_ms + render_ms,
        "execute_sum_ms": sum(query["execute_ms"] for query in queries),
        "query_execute_ms": [query["execute_ms"] for query in queries],
        "query_count": len(queries), "orders": orders_count, "items": items_count,
        "response_bytes": len(body), "payload_sha256": hashlib.sha256(body).hexdigest(),
    }
    return sample, queries, payload


def collect_phase(user, expected):
    warm = {scenario: measure(user, scenario, expected) for scenario in SCENARIOS}
    result = {scenario: {"samples": [], "queries": warm[scenario][1]} for scenario in SCENARIOS}
    for repeat in range(SAMPLES):
        # Rotate the shapes so the full list is not always measured first.
        for scenario in SCENARIOS[repeat % 3:] + SCENARIOS[:repeat % 3]:
            sample, _, _ = measure(user, scenario, expected)
            result[scenario]["samples"].append(sample)
    client = APIClient()
    client.force_authenticate(user=user)
    for scenario in ("first", "deep"):
        captured = []
        with connection.execute_wrapper(capture_queries(captured)):
            response = client.get(reverse("orders:order-list"), {"page": PAGES[scenario]})
        assert response.status_code == 200
        check_payload(response.json(), scenario, expected)
        assert [(q["sql"], q["params"]) for q in captured] == [
            (q["sql"], q["params"]) for q in result[scenario]["queries"]
        ]
        assert response.content == JSONRenderer().render(warm[scenario][2])
    for scenario, value in result.items():
        value["plans"] = plans(value["queries"])
        value["summary"] = {
            key: {"median": statistics.median(sample[key] for sample in value["samples"]),
                  "min": min(sample[key] for sample in value["samples"]),
                  "max": max(sample[key] for sample in value["samples"])}
            for key in ("materialize_ms", "serialize_ms", "render_ms", "total_stage_ms", "execute_sum_ms")
        }
        value["query_execute_summary"] = [
            {"median": statistics.median(sample["query_execute_ms"][i] for sample in value["samples"]),
             "min": min(sample["query_execute_ms"][i] for sample in value["samples"]),
             "max": max(sample["query_execute_ms"][i] for sample in value["samples"])}
            for i in range(len(value["queries"]))
        ]
    return result


def test_order_query_baseline():
    assert connection.vendor == "mysql"
    assert connection.settings_dict["NAME"] == TEST_DATABASE
    assert connection.get_autocommit()
    output = Path(os.environ["PROJECT_ONE_ORDER_QUERY_OUTPUT"])
    assert not output.exists(), "Choose a new evidence filename; do not overwrite an earlier run."
    owner, expected = create_read_fixture()
    analyze_tables()
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION(), @@innodb_buffer_pool_size, @@sort_buffer_size, @@transaction_isolation")
        mysql_version, buffer_pool, sort_buffer, isolation = cursor.fetchone()
    result = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "experiment_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {"python": sys.version, "django": django.get_version(), "drf": rest_framework.VERSION,
                        "os": platform.platform(), "machine": platform.machine(), "mysql": mysql_version,
                        "database": TEST_DATABASE, "innodb_buffer_pool_bytes": buffer_pool,
                        "sort_buffer_bytes": sort_buffer, "isolation": isolation},
        "method": {"warmups_per_shape_per_phase": 1, "samples_per_shape_per_phase": SAMPLES,
                   "concurrency": 1, "cache": "warm, no flush", "forced_index": False,
                   "order": "baseline then candidate; shape order rotated in each phase"},
        "fixture": {"owner_orders": OWNER_ORDERS, "other_orders": OWNER_ORDERS,
                    "address_rows": OWNER_ORDERS * 2, "item_rows": OWNER_ORDERS * 4,
                    "created_at": "2026-01-01 UTC + ((id * 37) % 503) seconds; ties included",
                    "deep_page": PAGES["deep"], "offset": 4000, "page_size": OrderPagination.page_size},
        "baseline_indexes": indexes(),
        "baseline": collect_phase(owner, expected),
    }
    print("Baseline collected; candidate index changes only the disposable test schema.", flush=True)
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE INDEX `{INDEX_NAME}` ON ordering_order (user_id, created_at, id)")
    try:
        analyze_tables()
        result["candidate_indexes"] = indexes()
        result["candidate"] = collect_phase(owner, expected)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP INDEX `{INDEX_NAME}` ON ordering_order")
    analyze_tables()
    result["restored_indexes"] = indexes()
    assert not any(row["Key_name"] == INDEX_NAME for row in result["restored_indexes"][Order._meta.db_table])
    assert Order.objects.count() == OWNER_ORDERS * 2
    assert AddressSnapshot.objects.count() == OWNER_ORDERS * 2
    assert OrderItem.objects.count() == OWNER_ORDERS * 4
    sample, queries, _ = measure(owner, "first", expected)
    result["after_drop_first"] = {"sample": sample, "plans": plans(queries)}
    result["correctness"] = "all samples: exact owner order/time oracle, full address/items; API queries and bytes match"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for phase in ("baseline", "candidate"):
        for scenario, value in result[phase].items():
            print(phase, scenario, json.dumps(value["summary"], ensure_ascii=False), flush=True)
