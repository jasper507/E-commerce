"""Ordered evidence for healthy-Redis misses sharing process-local admission."""

import json
import os
import platform
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock, current_thread

import django
import pytest
from django.db import DatabaseError, connection
from django.urls import reverse

from apps.catalog import cache
from apps.catalog.models import Product, SKU
from apps.catalog.views import PublicProductDetailView
from apps.inventory.models import Inventory

from .test_detail_cache import catalog_cache, current_key, get_detail_with_queries
from .test_fallback_limit import failure_only_admission, limiter, request_with_sql


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]
ROUNDS = 3


def catalog_items(data):
    pairs = [(data.product, data.sku)]
    for number in range(2, 7):
        product = Product.objects.create(
            category=data.category, name=f"Mouse M{number}", slug=f"mouse-m{number}",
            description="A test mouse", is_active=True,
        )
        sku = SKU.objects.create(
            product=product, code=f"M{number}-BLACK", price=Decimal("129.00"),
            attributes={"connection": "bluetooth"}, is_active=True,
        )
        Inventory.objects.create(sku=sku, available_stock=10)
        pairs.append((product, sku))
    items = []
    for product, sku in pairs:
        expected = {
            "slug": product.slug, "name": product.name, "description": product.description,
            "category": {"slug": "mice", "name": "Mice"}, "in_stock": True,
            "skus": [{"code": sku.code, "color": sku.color,
                      "attributes": {"connection": "bluetooth"}, "price": "129.00",
                      "in_stock": True}],
        }
        url = reverse("catalog:product-detail", kwargs={"slug": product.slug})
        items.append((product, url, expected))
    return items


def ordered_wave(items, monkeypatch):
    release = Event()
    two_entered = Event()
    extra_decisions = Event()
    lock = Lock()
    counters = {"active": 0, "peak": 0}
    decisions = set()
    extra_ids = {product.pk for product, _, _ in items[2:]}
    original_load = PublicProductDetailView.load_detail

    def note_decision(product_id):
        if product_id in extra_ids:
            decisions.add(product_id)
            if len(decisions) == 4:
                extra_decisions.set()

    def paused_load(view, product):
        with lock:
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
            if counters["active"] == 2:
                two_entered.set()
            note_decision(product.pk)
        try:
            assert release.wait(timeout=15), "Experiment did not release admitted loaders"
            return original_load(view, product)
        finally:
            with lock:
                counters["active"] -= 1

    def read(item):
        product, url, expected = item
        result = request_with_sql(url)
        with lock:
            if result["status"] == 503:
                note_decision(product.pk)
        return {
            "slug": product.slug, "status": result["status"],
            "payload_matches": result["payload"] == expected if result["status"] == 200 else None,
            "guard_sql": result["guard_sql"], "sku_sql": result["reload_sql"],
            "total_sql": result["sql"],
        }

    with monkeypatch.context() as wave_patch:
        wave_patch.setattr(PublicProductDetailView, "load_detail", paused_load)
        with ThreadPoolExecutor(max_workers=6) as executor:
            admitted = [executor.submit(read, item) for item in items[:2]]
            try:
                assert two_entered.wait(timeout=10), "First two readers did not enter loading"
                extra = [executor.submit(read, item) for item in items[2:]]
                assert extra_decisions.wait(timeout=10), "Extra readers did not load or reject"
                with lock:
                    observed_peak = counters["peak"]
                # Baseline readers are still loading; bounded readers finish before release.
                rejected_before_release = 0
                if observed_peak == 2:
                    rejected_before_release = sum(
                        future.result(timeout=10)["status"] == 503 for future in extra
                    )
                assert not release.is_set()
            finally:
                release.set()
            results = [future.result(timeout=10) for future in admitted + extra]
    return {
        "peak_active_loaders": observed_peak,
        "rejected_before_release": rejected_before_release,
        "requests": results,
    }


@pytest.mark.parametrize("strategy", ("failure_only_baseline", "shared_refill_limit"))
def test_distinct_cold_keys_share_refill_slots(
    catalog_cache, monkeypatch, limiter, request, strategy
):
    if strategy == "failure_only_baseline":
        request.getfixturevalue("failure_only_admission")
    data = catalog_cache
    items = catalog_items(data)
    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "strategy": strategy,
        "capacity_per_process": 2,
        "environment": {
            "python": platform.python_version(), "django": django.get_version(),
            "os": platform.system(), "database": connection.settings_dict["NAME"],
            "mysql": connection.mysql_server_info,
            "redis": data.control.info("server")["redis_version"],
        },
        "method": {
            "planned_rounds": ROUNDS, "products": 6,
            "client": "six DRF APIClient threads; real isolated MySQL and Redis",
            "trigger": "normal Product.save rotates global generation after warmup",
            "schedule": "hold first two loaders with Event; four more load or reject before release",
            "delay": "Python loader Event before SKU SQL; not a slow database or benchmark",
            "baseline": "test-only historical policy ignores healthy cache misses",
            "sql": "request statements only; setup, save and prewarm excluded",
        },
        "rounds": [],
    }
    for number in range(1, ROUNDS + 1):
        for _, url, expected in items:
            warmup = request_with_sql(url)
            assert warmup["status"] == 200 and warmup["payload"] == expected
        generation_before = cache.get_catalog_generation()
        items[0][0].name = f"Mouse M1 refill round {number}"
        items[0][0].save(update_fields=("name",))
        items[0][2]["name"] = items[0][0].name
        generation_after = cache.get_catalog_generation()
        assert generation_after != generation_before
        assert all(
            data.control.ttl(cache.product_detail_key(product.pk, generation_before)) > 0
            and not data.control.exists(cache.product_detail_key(product.pk, generation_after))
            for product, _, _ in items
        )
        wave = ordered_wave(items, monkeypatch)
        recovery = [request_with_sql(url) for _, url, _ in items]
        warm = [request_with_sql(url) for _, url, _ in items]
        for phase in (recovery, warm):
            assert all(
                result["status"] == 200 and result["payload"] == item[2]
                for result, item in zip(phase, items)
            )
        summary = {
            "requests": 6, "successes": sum(r["status"] == 200 for r in wave["requests"]),
            "rejected_503": sum(r["status"] == 503 for r in wave["requests"]),
            "peak_active_loaders": wave["peak_active_loaders"],
            "rejected_before_release": wave["rejected_before_release"],
            "guard_sql": sum(r["guard_sql"] for r in wave["requests"]),
            "sku_sql": sum(r["sku_sql"] for r in wave["requests"]),
            "total_sql": sum(r["total_sql"] for r in wave["requests"]),
            "recovery_sku_sql": sum(r["reload_sql"] for r in recovery),
            "warm_sku_sql": sum(r["reload_sql"] for r in warm),
        }
        report["rounds"].append({"round": number, "summary": summary, **wave})
        print("refill_limit " + json.dumps({"strategy": strategy, "round": number, **summary}))
        output = os.getenv("PROJECT_ONE_REFILL_OUTPUT_DIR")
        if output:
            path = Path(output) / f"phase-4j-refill-{strategy}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        successes = 6 if strategy == "failure_only_baseline" else 2
        assert summary["peak_active_loaders"] == successes, summary
        assert summary["successes"] == summary["sku_sql"] == successes, summary
        assert summary["rejected_503"] == 6 - successes, summary
        assert summary["rejected_before_release"] == 6 - successes, summary
        assert summary["guard_sql"] == 6 and summary["total_sql"] == 6 + successes
        assert summary["recovery_sku_sql"] == 6 - successes
        assert summary["warm_sku_sql"] == 0
        assert cache.detail_reads.state == "closed"
        assert all(r["guard_sql"] == 1 for r in wave["requests"])
        assert all(r["payload_matches"] for r in wave["requests"] if r["status"] == 200)
        assert all(r["sku_sql"] == 0 for r in wave["requests"] if r["status"] == 503)


def test_timed_out_same_key_follower_cannot_bypass_full_refill_budget(
    catalog_cache, monkeypatch, limiter
):
    data = catalog_cache
    entered = Event()
    release = Event()
    original_load = PublicProductDetailView.load_detail

    def paused_load(view, product):
        entered.set()
        assert release.wait(timeout=10)
        return original_load(view, product)

    monkeypatch.setattr(PublicProductDetailView, "load_detail", paused_load)
    with limiter.slot():
        with ThreadPoolExecutor(max_workers=1) as executor:
            owner = executor.submit(request_with_sql, data.url)
            try:
                assert entered.wait(timeout=10)
                follower = request_with_sql(data.url)
                assert follower["status"] == 503
                assert follower["sql"] == follower["guard_sql"] == 1
                assert follower["reload_sql"] == 0
                assert not owner.done()
            finally:
                release.set()
            assert owner.result(timeout=10)["status"] == 200
    assert get_detail_with_queries(data)[1] == 1


def test_healthy_load_exception_releases_slot_and_failed_flight(
    catalog_cache, monkeypatch, limiter
):
    original_load = PublicProductDetailView.load_detail

    def failed_load(view, product):
        raise DatabaseError("Injected isolated refill failure")

    with limiter.slot():
        monkeypatch.setattr(PublicProductDetailView, "load_detail", failed_load)
        with pytest.raises(DatabaseError, match="Injected isolated refill"):
            catalog_cache.client.get(catalog_cache.url)
        monkeypatch.setattr(PublicProductDetailView, "load_detail", original_load)
        requesting_thread = current_thread()
        original_result = Future.result

        def prohibit_failed_join(future, *args, **kwargs):
            if current_thread() is requesting_thread:
                raise AssertionError("Next request must not join a stale failed flight")
            return original_result(future, *args, **kwargs)

        monkeypatch.setattr(Future, "result", prohibit_failed_join)
        payload, queries = get_detail_with_queries(catalog_cache)
        assert payload["skus"][0]["price"] == "129.00" and queries == 2
    assert catalog_cache.control.get(current_key(catalog_cache.product)) is not None
