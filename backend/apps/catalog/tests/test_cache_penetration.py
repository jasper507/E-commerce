"""Real missing-slug baseline; no negative-cache business behavior yet."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
import redis
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.catalog import cache
from apps.catalog.models import Product

from .test_detail_cache import catalog_cache, get_detail_with_queries
from .test_read_breaker_recovery import observe_commands
from .test_redis_slow_recovery import base_report


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]
REQUESTS = 6


def test_same_missing_slug_only_queries_product_once(catalog_cache):
    data = catalog_cache
    slug = f"missing-{uuid4().hex}"
    assert not Product.objects.filter(slug=slug).exists()
    url = reverse("catalog:product-detail", kwargs={"slug": slug})
    product_queries = []

    for _ in range(REQUESTS):
        with CaptureQueriesContext(connection) as queries:
            response = data.client.get(url)
        assert response.status_code == 404
        product_queries.append(
            sum("from `catalog_product`" in query["sql"].lower() for query in queries)
        )

    assert product_queries == [1, 0, 0, 0, 0, 0]


def test_missing_marker_expires_and_rechecks_mysql(catalog_cache):
    data = catalog_cache
    slug = f"missing-{uuid4().hex}"
    url = reverse("catalog:product-detail", kwargs={"slug": slug})

    assert data.client.get(url).status_code == 404
    key = cache.missing_product_key(slug, cache.get_catalog_generation())
    assert data.control.get(key) == b"missing"
    assert 0 < data.control.ttl(key) <= cache.MISSING_PRODUCT_TTL_SECONDS
    assert data.control.pexpire(key, 1)
    deadline = monotonic() + 1
    while data.control.exists(key) and monotonic() < deadline:
        sleep(0.01)
    assert not data.control.exists(key)

    with CaptureQueriesContext(connection) as queries:
        response = data.client.get(url)
    assert response.status_code == 404
    assert sum("from `catalog_product`" in q["sql"].lower() for q in queries) == 1


def test_new_product_is_visible_after_generation_rotates(catalog_cache):
    data = catalog_cache
    slug = f"missing-{uuid4().hex}"
    url = reverse("catalog:product-detail", kwargs={"slug": slug})

    assert data.client.get(url).status_code == 404
    old_generation = cache.get_catalog_generation()
    assert data.control.exists(cache.missing_product_key(slug, old_generation))
    Product.objects.create(
        category=data.category,
        name="New mouse",
        slug=slug,
        is_active=True,
    )
    assert cache.get_catalog_generation() != old_generation

    response = data.client.get(url)
    assert response.status_code == 200
    assert response.json()["slug"] == slug


@pytest.mark.parametrize("hidden", ("product", "category"))
def test_hidden_item_is_not_marked_as_nonexistent(catalog_cache, hidden):
    data = catalog_cache
    instance = getattr(data, hidden)
    type(instance).objects.filter(pk=instance.pk).update(is_active=False)
    generation = cache.get_catalog_generation()

    with CaptureQueriesContext(connection) as queries:
        hidden_response = data.client.get(data.url)
    assert hidden_response.status_code == 404
    assert sum("from `catalog_product`" in q["sql"].lower() for q in queries) == 1
    assert not data.control.exists(cache.missing_product_key(data.product.slug, generation))

    type(instance).objects.filter(pk=instance.pk).update(is_active=True)
    assert cache.get_catalog_generation() == generation
    assert data.client.get(data.url).status_code == 200


def test_missing_marker_read_failure_uses_existing_fallback_limit(catalog_cache, monkeypatch):
    data = catalog_cache
    slug = f"missing-{uuid4().hex}"
    url = reverse("catalog:product-detail", kwargs={"slug": slug})
    client = cache.get_redis_client()
    original_get = client.get

    def fail_missing_marker(key):
        if ":catalog:missing:" in key:
            raise redis.TimeoutError("Injected missing marker timeout")
        return original_get(key)

    monkeypatch.setattr(client, "get", fail_missing_marker)
    with CaptureQueriesContext(connection) as queries:
        response = data.client.get(url)
    assert response.status_code == 404
    assert sum("from `catalog_product`" in q["sql"].lower() for q in queries) == 1
    assert cache.detail_reads.state == "open"

    with cache.detail_fallbacks.slot(), cache.detail_fallbacks.slot():
        with CaptureQueriesContext(connection) as rejected_queries:
            rejected = data.client.get(url)
    assert rejected.status_code == 503
    assert not rejected_queries


def test_missing_marker_write_failure_keeps_404(catalog_cache, monkeypatch):
    data = catalog_cache
    slug = f"missing-{uuid4().hex}"
    url = reverse("catalog:product-detail", kwargs={"slug": slug})
    key = cache.missing_product_key(slug, cache.get_catalog_generation())
    client = cache.get_redis_client()
    original_set = client.set

    def fail_missing_marker(marker_key, *args, **kwargs):
        if marker_key == key:
            raise redis.ConnectionError("Injected missing marker write failure")
        return original_set(marker_key, *args, **kwargs)

    monkeypatch.setattr(client, "set", fail_missing_marker)
    product_queries = []
    for _ in range(2):
        with CaptureQueriesContext(connection) as queries:
            response = data.client.get(url)
        assert response.status_code == 404
        product_queries.append(
            sum("from `catalog_product`" in q["sql"].lower() for q in queries)
        )
    assert product_queries == [1, 1]
    assert not data.control.exists(key)
    assert cache.detail_reads.state == "closed"


def test_real_missing_slug_penetration_baseline(catalog_cache, monkeypatch):
    data = catalog_cache
    # Keep the pre-change control reproducible without altering the saved real baseline.
    monkeypatch.setattr(cache.ProductDetailCache, "is_missing", lambda self, slug: False)
    monkeypatch.setattr(cache.ProductDetailCache, "remember_missing", lambda self, slug: None)
    expected, _ = get_detail_with_queries(data)
    missing_slug = f"missing-{uuid4().hex}"
    different_slugs = [f"missing-{uuid4().hex}" for _ in range(REQUESTS)]
    assert not Product.objects.filter(slug__in=[missing_slug, *different_slugs]).exists()
    report = base_report(data)
    report["method"] = {
        "scope": "negative marker disabled for pre-change control; real isolated MySQL/Redis; sequential requests",
        "requests_per_scenario": REQUESTS,
        "client": "DRF APIClient through response JSON, no HTTP server",
        "sql": "request statements only; setup and warmup excluded",
        "commands": "application execute_command attempts, excludes control and handshake commands",
        "missing": "same missing slug versus six distinct missing slugs; no hidden product cached",
        "timing": "no latency or throughput claim; count SQL, Redis attempts, status and correctness",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    scenarios = []

    for name, slugs, expected_status in (
        ("same_missing_slug", [missing_slug] * REQUESTS, 404),
        ("different_missing_slugs", different_slugs, 404),
        ("warm_existing_product", [data.product.slug] * REQUESTS, 200),
    ):
        with pytest.MonkeyPatch.context() as patch:
            commands = observe_commands(cache.get_redis_client(), patch)
            results = []
            for number, slug in enumerate(slugs):
                before = len(commands)
                statements = []

                def capture(execute, sql, params, many, context):
                    statements.append(sql.lower())
                    return execute(sql, params, many, context)

                url = reverse("catalog:product-detail", kwargs={"slug": slug})
                with connection.execute_wrapper(capture):
                    response = data.client.get(url)
                    payload = response.json()
                request_commands = commands[before:]
                result = {
                    "request": number,
                    "slug": slug,
                    "status": response.status_code,
                    "sql": len(statements),
                    "guard_sql": sum("from `catalog_product`" in sql for sql in statements),
                    "reload_sql": sum("from `catalog_sku`" in sql for sql in statements),
                    "payload_matches": payload == expected if expected_status == 200 else None,
                    "redis_commands": request_commands,
                }
                assert result["status"] == expected_status, result
                assert result["sql"] == result["guard_sql"] == 1, result
                assert result["reload_sql"] == 0, result
                assert all(command["outcome"] == "success" for command in request_commands)
                if expected_status == 200:
                    assert result["payload_matches"], result
                results.append(result)
        summary = {
            "requests": len(results),
            "status": expected_status,
            "sql": sum(result["sql"] for result in results),
            "sku_sql": sum(result["reload_sql"] for result in results),
            "application_get_attempts": sum(command["command"] == "GET" for command in commands),
            "breaker_state": cache.detail_reads.state,
        }
        assert summary["breaker_state"] == "closed"
        assert summary["application_get_attempts"] == REQUESTS * (2 if expected_status == 200 else 1)
        scenarios.append({"name": name, "summary": summary, "requests": results})

    report["scenarios"] = scenarios
    print("cache_penetration_baseline " + json.dumps({
        scenario["name"]: scenario["summary"] for scenario in scenarios
    }, sort_keys=True))
    output = os.getenv("PROJECT_ONE_PENETRATION_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
