"""Replay failure-only admission: six detail keys become cold after one save."""

import json
import math
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import django
import pytest
from django.db import close_old_connections, connection
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.cache import get_catalog_generation, product_detail_key
from apps.catalog.models import Product, SKU
from apps.inventory.models import Inventory

from .test_detail_cache import catalog_cache
from .test_fallback_limit import failure_only_admission


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]
PRODUCTS = 6
ROUNDS = 3


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 3)


def run_wave(urls, expected):
    started = [None]
    gate = Barrier(PRODUCTS, action=lambda: started.__setitem__(0, time.perf_counter()))

    def request(slug, url):
        close_old_connections()
        try:
            connection.ensure_connection()
            statements = []

            def capture(execute, sql, params, many, context):
                statements.append(sql.lower())
                return execute(sql, params, many, context)

            gate.wait(timeout=10)
            with connection.execute_wrapper(capture):
                request_started = time.perf_counter()
                response = APIClient().get(url)
                payload = response.json()
                elapsed_ms = (time.perf_counter() - request_started) * 1000
            return {
                "slug": slug,
                "status": response.status_code,
                "payload_matches": payload == expected[slug],
                "observed_name": payload.get("name"),
                "guard_sql": sum("from `catalog_product`" in sql for sql in statements),
                "sku_sql": sum("from `catalog_sku`" in sql for sql in statements),
                "total_sql": len(statements),
                "elapsed_ms": round(elapsed_ms, 3),
            }
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=PRODUCTS) as executor:
        futures = [executor.submit(request, slug, url) for slug, url in urls.items()]
        results = [future.result(timeout=30) for future in futures]
    duration_ms = (time.perf_counter() - started[0]) * 1000
    results.sort(key=lambda result: result["slug"])
    timings = [result["elapsed_ms"] for result in results]
    summary = {
        "requests": len(results),
        "successes": sum(result["status"] == 200 for result in results),
        "guard_sql": sum(result["guard_sql"] for result in results),
        "sku_sql": sum(result["sku_sql"] for result in results),
        "total_sql": sum(result["total_sql"] for result in results),
        "duration_ms": round(duration_ms, 3),
        "throughput_rps": round(PRODUCTS * 1000 / duration_ms, 3),
        "p50_ms": percentile(timings, 0.5),
        "p95_ms": percentile(timings, 0.95),
        "p99_ms": percentile(timings, 0.99),
        "max_ms": round(max(timings), 3),
    }
    return {"summary": summary, "requests": results}


def test_one_save_makes_six_distinct_detail_keys_cold(catalog_cache, failure_only_admission):
    data = catalog_cache
    products = [data.product]
    for number in range(2, PRODUCTS + 1):
        product = Product.objects.create(
            category=data.category,
            name=f"Mouse M{number}",
            slug=f"mouse-m{number}",
            is_active=True,
        )
        sku = SKU.objects.create(
            product=product,
            code=f"M{number}-BLACK",
            price=Decimal("129.00"),
            is_active=True,
        )
        Inventory.objects.create(sku=sku, available_stock=10)
        products.append(product)
    urls = {
        product.slug: reverse("catalog:product-detail", kwargs={"slug": product.slug})
        for product in products
    }
    assert len(urls) == PRODUCTS
    report = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "django": django.get_version(),
            "os": platform.system(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "database": connection.settings_dict["NAME"],
            "mysql": connection.mysql_server_info,
            "redis": data.control.info("server")["redis_version"],
        },
        "method": {
            "products": PRODUCTS,
            "concurrent_requests_per_wave": PRODUCTS,
            "rounds": ROUNDS,
            "client": "DRF APIClient in six threads; no HTTP server",
            "trigger": "normal Product.save changes one product and rotates global generation",
            "timing": "request to parsed JSON; excludes thread DB connection and start barrier",
            "percentiles": "nearest rank; with six samples p95 and p99 equal maximum",
            "database": "real isolated MySQL; fixture/setup/warmup SQL excluded",
            "redis": "real isolated Redis; UUID-prefixed keys only; no FLUSHDB",
            "admission": "historical failure-only admission replay; healthy cache misses are unbounded",
            "dataset": "six active mice, one active SKU and inventory per product",
        },
        "rounds": [],
    }

    for round_number in range(1, ROUNDS + 1):
        generation_before = get_catalog_generation()
        expected = {}
        for product in products:
            response = data.client.get(urls[product.slug])
            assert response.status_code == 200
            expected[product.slug] = response.json()
            assert data.control.get(product_detail_key(product.pk, generation_before))

        warm_before = run_wave(urls, expected)
        products[0].name = f"Mouse M1 round {round_number}"
        products[0].save(update_fields=("name",))
        generation_after = get_catalog_generation()
        assert generation_after != generation_before
        old_keys = [product_detail_key(product.pk, generation_before) for product in products]
        new_keys = [product_detail_key(product.pk, generation_after) for product in products]
        old_key_ttls = [data.control.ttl(key) for key in old_keys]
        assert all(ttl > 0 for ttl in old_key_ttls)
        assert all(not data.control.exists(key) for key in new_keys)

        expected[products[0].slug] = {
            **expected[products[0].slug],
            "name": products[0].name,
        }
        cold_after = run_wave(urls, expected)
        new_key_ttls = [data.control.ttl(key) for key in new_keys]
        assert all(0 < ttl <= 35 for ttl in new_key_ttls)
        warm_after = run_wave(urls, expected)

        for name, wave, expected_sku_sql in (
            ("warm_before_rotation", warm_before, 0),
            ("cold_after_rotation", cold_after, PRODUCTS),
            ("warm_after_refill", warm_after, 0),
        ):
            summary = wave["summary"]
            assert summary["successes"] == PRODUCTS, (name, wave)
            assert summary["guard_sql"] == PRODUCTS, (name, wave)
            assert summary["sku_sql"] == expected_sku_sql, (name, wave)
            assert summary["total_sql"] == PRODUCTS + expected_sku_sql, (name, wave)
            assert all(result["payload_matches"] for result in wave["requests"]), (name, wave)
            assert all(result["guard_sql"] == 1 for result in wave["requests"]), (name, wave)
            assert all(
                result["sku_sql"] == expected_sku_sql // PRODUCTS
                for result in wave["requests"]
            ), (name, wave)
            wave["scenario"] = name
            print("multikey_avalanche " + json.dumps({
                "round": round_number,
                "scenario": name,
                **summary,
            }, sort_keys=True))

        report["rounds"].append({
            "round": round_number,
            "generation_changed": generation_after != generation_before,
            "old_key_ttls_after_rotation": old_key_ttls,
            "new_key_ttls_after_refill": new_key_ttls,
            "waves": [warm_before, cold_after, warm_after],
        })

    output = os.getenv("PROJECT_ONE_AVALANCHE_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
