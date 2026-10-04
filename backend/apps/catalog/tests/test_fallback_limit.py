"""Bounded, process-local admission during Redis failure; not a capacity test."""

import json
import os
import socket
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, current_thread

import pytest
import redis
from django.db import DatabaseError, close_old_connections, connection, transaction
from rest_framework.test import APIClient

from apps.catalog import cache
from apps.catalog.views import PublicProductDetailView

from .test_detail_cache import catalog_cache, get_detail_with_queries


@pytest.fixture
def limiter(monkeypatch):
    limiter = cache.FallbackLimiter(capacity=2)
    monkeypatch.setattr(cache, "detail_fallbacks", limiter)
    return limiter


@pytest.fixture
def failure_only_admission(monkeypatch):
    """Replay the historical policy for baseline evidence, not current protection."""
    original_scope = cache.ProductDetailCache.fallback_scope

    @contextmanager
    def historical_scope(detail_cache, **kwargs):
        with original_scope(detail_cache):
            yield

    monkeypatch.setattr(cache.ProductDetailCache, "fallback_scope", historical_scope)


@contextmanager
def connection_refused(data, settings):
    """Real failed connection: reserve a local TCP port without listening."""
    failed_socket = socket.socket()
    failed_socket.bind(("127.0.0.1", 0))
    healthy_url = settings.CATALOG_CACHE_REDIS_URL
    cache.get_redis_client().connection_pool.disconnect()
    cache.get_redis_client.cache_clear()
    settings.CATALOG_CACHE_REDIS_URL = (
        f"redis://127.0.0.1:{failed_socket.getsockname()[1]}/0"
    )
    cache.get_redis_client()
    try:
        yield
    finally:
        cache.get_redis_client().connection_pool.disconnect()
        cache.get_redis_client.cache_clear()
        settings.CATALOG_CACHE_REDIS_URL = healthy_url
        failed_socket.close()


def request_with_sql(url):
    close_old_connections()
    try:
        connection.ensure_connection()
        statements = []

        def record(execute, sql, params, many, context):
            statements.append(sql.lower())
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record):
            response = APIClient().get(url)
        return {
            "status": response.status_code,
            "payload": response.json(),
            "sql": len(statements),
            "guard_sql": sum("from `catalog_product`" in sql for sql in statements),
            "reload_sql": sum("from `catalog_sku`" in sql for sql in statements),
        }
    finally:
        connection.close()


def test_full_limiter_rejects_without_waiting_and_exception_releases():
    limiter = cache.FallbackLimiter(capacity=2)
    with limiter.slot(), limiter.slot():
        with pytest.raises(cache.CatalogFallbackBusy) as rejected:
            with limiter.slot():
                pytest.fail("Third caller must not enter the protected body")
        assert rejected.value.status_code == 503
    with pytest.raises(ValueError, match="failed load"):
        with limiter.slot():
            raise ValueError("failed load")
    with limiter.slot(), limiter.slot():
        pass


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
def test_real_failure_admits_two_rejects_eight_before_sql(
    catalog_cache, settings, monkeypatch, limiter
):
    data = catalog_cache
    expected, _ = get_detail_with_queries(data)
    original_load = PublicProductDetailView.load_detail
    entered = Event()
    release = Event()
    observation_lock = Lock()
    active = 0
    peak = 0

    def paused_load(view, product):
        nonlocal active, peak
        with observation_lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
        try:
            assert release.wait(timeout=15), "Test did not release the admitted readers"
            return original_load(view, product)
        finally:
            with observation_lock:
                active -= 1

    monkeypatch.setattr(PublicProductDetailView, "load_detail", paused_load)
    with connection_refused(data, settings):
        with ThreadPoolExecutor(max_workers=10) as executor:
            admitted = [executor.submit(request_with_sql, data.url) for _ in range(2)]
            try:
                assert entered.wait(timeout=10), "Two allowed requests must enter loading"
                rejected_futures = [
                    executor.submit(request_with_sql, data.url) for _ in range(8)
                ]
                rejected = [future.result(timeout=10) for future in rejected_futures]
                # All rejected requests finish before either admitted request is released.
                assert not release.is_set()
                assert all(result["status"] == 503 for result in rejected)
                assert all(result["sql"] == 0 for result in rejected)
                assert all(not future.done() for future in admitted)
            finally:
                release.set()
            successful = [future.result(timeout=10) for future in admitted]
        assert peak == 2
        assert all(result["status"] == 200 for result in successful)
        assert all(result["payload"] == expected for result in successful)
        assert all(result["sql"] == 2 for result in successful)
        assert all(result["guard_sql"] == result["reload_sql"] == 1 for result in successful)
        # Both successful requests released their admission before the next request.
        assert get_detail_with_queries(data) == (expected, 2)

    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "capacity_per_process": 2,
        "environment": {
            "database": connection.settings_dict["NAME"],
            "mysql": connection.mysql_server_info,
            "redis": data.control.info("server")["redis_version"],
        },
        "method": {
            "client": "DRF APIClient; isolated real MySQL and Redis",
            "failure": "real connection refused at a bound non-listening TCP socket",
            "schedule": "hold two admitted loaders with Event; run eight more before release",
            "scope": "ordered correctness experiment; artificial holding is not a benchmark",
            "sql": "ORM statements only; excludes pre-established DB connections and setup",
        },
        "summary": {
            "requests": 10,
            "successes": len(successful),
            "rejected_503": len(rejected),
            "sql": sum(result["sql"] for result in successful + rejected),
            "rejected_sql": sum(result["sql"] for result in rejected),
            "peak_protected_loaders": peak,
        },
        "requests": [
            {"worker": worker,
             "payload_matches": result["payload"] == expected if result["status"] == 200 else None,
             **{key: value for key, value in result.items() if key != "payload"}}
            for worker, result in enumerate(successful + rejected)
        ],
    }
    print("fallback_limit " + json.dumps(report["summary"], sort_keys=True))
    output = os.getenv("PROJECT_ONE_FALLBACK_EXPERIMENT_OUTPUT")
    if output:
        Path(output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure", ("not_found", "database_error"))
def test_failed_request_releases_admission(
    catalog_cache, settings, monkeypatch, limiter, failure
):
    data = catalog_cache
    with connection_refused(data, settings):
        with limiter.slot():
            if failure == "not_found":
                assert data.client.get(data.url.replace("mouse-m1", "missing-product")).status_code == 404
            else:
                original_load = PublicProductDetailView.load_detail

                def failed_load(view, product):
                    raise DatabaseError("Injected isolated database load failure")

                monkeypatch.setattr(PublicProductDetailView, "load_detail", failed_load)
                with pytest.raises(DatabaseError, match="Injected isolated"):
                    data.client.get(data.url)
                monkeypatch.setattr(PublicProductDetailView, "load_detail", original_load)
            # With one slot held, this request needs the same released second slot.
            payload, queries = get_detail_with_queries(data)
            assert payload["skus"][0]["price"] == "129.00"
            assert queries == 2


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ("warm", "cold", "disabled", "atomic"))
def test_full_budget_rejects_normal_cold_load_but_preserves_hits_and_bypasses(
    catalog_cache, settings, limiter, mode
):
    data = catalog_cache
    if mode == "warm":
        get_detail_with_queries(data)
    elif mode == "disabled":
        settings.CATALOG_CACHE_ENABLED = False
    with limiter.slot(), limiter.slot():
        with transaction.atomic() if mode == "atomic" else nullcontext():
            if mode == "cold":
                result = request_with_sql(data.url)
                assert result["status"] == 503
                assert result["sql"] == result["guard_sql"] == 1
                assert result["reload_sql"] == 0
            else:
                payload, queries = get_detail_with_queries(data)
                assert payload["skus"][0]["price"] == "129.00"
                assert queries == (1 if mode == "warm" else 2)


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failed_payload_get", (1, 2))
def test_payload_get_failure_rejects_after_guard_before_sku_sql(
    catalog_cache, monkeypatch, limiter, failed_payload_get
):
    """Late failure is injected; the SQL observation still uses real MySQL."""
    client = cache.get_redis_client()
    original_get = client.get
    detail_key = catalog_cache.key
    payload_gets = 0

    def fail_payload_get(key, *args, **kwargs):
        nonlocal payload_gets
        if key == detail_key:
            payload_gets += 1
            if payload_gets == failed_payload_get:
                raise redis.ConnectionError("Injected payload GET failure after generation GET")
        return original_get(key, *args, **kwargs)

    monkeypatch.setattr(client, "get", fail_payload_get)
    with limiter.slot(), limiter.slot():
        result = request_with_sql(catalog_cache.url)
    assert result["status"] == 503
    assert result["sql"] == result["guard_sql"] == 1
    assert result["reload_sql"] == 0
    assert payload_gets == failed_payload_get

    monkeypatch.setattr(client, "get", original_get)
    catalog_cache.breaker_clock.now += 1
    original_result = Future.result
    requesting_thread = current_thread()

    def prohibit_stale_join(future, *args, **kwargs):
        if current_thread() is requesting_thread:
            raise AssertionError("Recovered request must not join a stale failed Future")
        return original_result(future, *args, **kwargs)

    monkeypatch.setattr(Future, "result", prohibit_stale_join)
    # A rejection inside the owner's double-check must also remove that flight.
    payload, queries = get_detail_with_queries(catalog_cache)
    assert payload["skus"][0]["price"] == "129.00"
    assert queries == 2


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
def test_invalid_generation_rejects_before_any_sql(catalog_cache, limiter):
    catalog_cache.control.set(cache.catalog_generation_key(), "invalid-generation")
    with limiter.slot(), limiter.slot():
        result = request_with_sql(catalog_cache.url)
    assert result["status"] == 503
    assert result["sql"] == 0
