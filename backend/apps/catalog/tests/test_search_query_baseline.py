"""显式隔离实验：当前搜索API的完整COUNT/结果SQL，不制造慢SQL结论。"""

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


ENABLED = os.getenv("PROJECT_ONE_SEARCH_QUERY_BASELINE") == "1"
TEST_DATABASE = "test_project_one"
TABLES = (Category._meta.db_table, Product._meta.db_table, SKU._meta.db_table, Inventory._meta.db_table)
pytestmark = [pytest.mark.skipif(not ENABLED, reason="explicit product-search experiment only"), pytest.mark.django_db(transaction=True)]

if ENABLED and (
    settings.ENVIRONMENT != "test"
    or settings.DATABASES["default"]["TEST"]["NAME"] != TEST_DATABASE
    or settings.DATABASES["default"]["NAME"] == TEST_DATABASE
):
    raise RuntimeError("Search baseline requires DJANGO_ENV=test and isolated test_project_one.")


def rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def capture(records):
    def wrapper(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        records.append({"sql": sql, "params": list(params or ())})
        return result
    return wrapper


def explain(queries):
    result = []
    with connection.cursor() as cursor:
        for query in queries:
            assert query["sql"].startswith("SELECT")
            cursor.execute("EXPLAIN " + query["sql"], query["params"])
            estimated = rows(cursor)
            cursor.execute("EXPLAIN ANALYZE " + query["sql"], query["params"])
            result.append({**query, "estimated": estimated, "actual_tree": "\n".join(row[0] for row in cursor.fetchall())})
    return result


def test_actual_search_queries_and_plans():
    assert connection.vendor == "mysql" and connection.settings_dict["NAME"] == TEST_DATABASE
    assert connection.get_autocommit() and not connection.in_atomic_block
    path_text = os.getenv("PROJECT_ONE_SEARCH_BASELINE_REPORT")
    report_path = Path(path_text) if path_text else None
    if report_path:
        assert report_path.is_absolute() and report_path.parent.is_dir()
        assert not report_path.exists(), "Refuse to overwrite previous evidence."
    selected = Category.objects.create(name="搜索分类A", slug="search-a", is_active=True)
    other = Category.objects.create(name="搜索分类B", slug="search-b", is_active=True)
    hidden = Category.objects.create(name="搜索隐藏分类", slug="search-hidden", is_active=False)
    Product.objects.bulk_create([
        Product(
            category=selected if index % 4 < 2 else other, name=f"搜索商品 {index:04d}",
            slug=f"search-product-{index:04d}",
            description="无线鼠标 双模" if index % 10 == 0 else "USB有线配件", is_active=True,
        ) for index in range(5000)
    ], batch_size=500)
    Product.objects.create(category=selected, name="搜索商品 5000", slug="search-inactive", description="无线鼠标", is_active=False)
    Product.objects.create(category=hidden, name="搜索商品 5001", slug="search-hidden-category", description="无线鼠标", is_active=True)
    products = list(Product.objects.order_by("id"))
    SKU.objects.bulk_create([
        SKU(product=product, code=f"SEARCH-{index}-{kind}", price=Decimal("129.00") if kind == "active" else Decimal("1.00"), is_active=kind == "active")
        for index, product in enumerate(products) for kind in ("active", "hidden")
    ], batch_size=500)
    skus = list(SKU.objects.order_by("id"))
    Inventory.objects.bulk_create([
        Inventory(sku=sku, available_stock=10 if not sku.is_active or index // 2 % 20 == 0 else 0)
        for index, sku in enumerate(skus)
    ], batch_size=500)
    metadata = {}
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        for table in TABLES:
            cursor.execute(f"ANALYZE TABLE `{table}`")
            assert all(row[2:] == ("status", "OK") for row in cursor.fetchall())
            cursor.execute(f"SHOW INDEX FROM `{table}`")
            indexes = rows(cursor)
            cursor.execute(f"SHOW FULL COLUMNS FROM `{table}`")
            metadata[table] = {"indexes": indexes, "columns": rows(cursor)}
    scenarios = (
        ("keyword", {"q": "无线鼠标"}, [index for index in range(5000) if index % 10 == 0]),
        ("missing", {"q": "不可能匹配的测试词"}, []),
        ("keyword_category", {"q": "无线鼠标", "category": "search-a"}, [index for index in range(5000) if index % 10 == 0 and index % 4 < 2]),
    )
    observations = []
    client = APIClient()
    for label, params, expected in scenarios:
        queries = []
        with connection.execute_wrapper(capture(queries)):
            response = client.get(reverse("catalog:product-list"), params)
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == len(expected)
        assert payload["previous"] is None
        assert (payload["next"] is not None) == (len(expected) > 20)
        assert payload["results"] == [
            {
                "slug": f"search-product-{index:04d}", "name": f"搜索商品 {index:04d}",
                "category": {"slug": "search-a", "name": "搜索分类A"} if index % 4 < 2 else {"slug": "search-b", "name": "搜索分类B"},
                "lowest_price": "129.00", "in_stock": index % 20 == 0,
            } for index in expected[:20]
        ]
        assert len(queries) == (2 if expected else 1)
        observations.append({"scenario": label, "params": params, "count": payload["count"], "response": payload, "plans": explain(queries)})
        print("search_query_baseline " + json.dumps({"scenario": label, "count": payload["count"], "sql_count": len(queries)}, ensure_ascii=False))
    report = {
        "date": "2026-10-09", "mysql_version": version,
        "fixture": {"visible_products": 5000, "hidden_products": 2, "skus": 10004, "inventory": 10004},
        "metadata": metadata, "observations": observations,
        "boundary": "Current complete API SQL; one local plan per query; no latency or production-capacity benchmark.",
    }
    if report_path:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
