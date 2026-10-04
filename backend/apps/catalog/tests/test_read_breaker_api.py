"""API boundary evidence with isolated MySQL/Redis and controlled probe timing."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import redis
from django.db import DatabaseError

from apps.catalog import cache
from apps.catalog.views import PublicProductDetailView

from .test_detail_cache import catalog_cache, get_detail_with_queries
from .test_fallback_limit import limiter, request_with_sql
from .test_read_breaker_recovery import observe_commands


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]


def open_reads():
    def fail():
        raise redis.ConnectionError("Controlled breaker state for API boundary test")

    with pytest.raises(redis.ConnectionError):
        cache.detail_reads.run(fail)


def test_open_skips_generation_initialization_and_rejects_before_sql(catalog_cache, limiter, monkeypatch):
    data = catalog_cache
    expected, _ = get_detail_with_queries(data)
    data.control.delete(cache.catalog_generation_key())
    open_reads()
    commands = observe_commands(cache.get_redis_client(), monkeypatch)
    with limiter.slot(), limiter.slot():
        rejected = request_with_sql(data.url)
    assert rejected["status"] == 503
    assert rejected["sql"] == 0
    assert commands == []
    assert get_detail_with_queries(data) == (expected, 2)
    assert commands == []
    assert data.control.get(cache.catalog_generation_key()) is None


def test_half_open_one_real_probe_others_use_existing_admission(catalog_cache, limiter, monkeypatch):
    data = catalog_cache
    expected, _ = get_detail_with_queries(data)
    open_reads()
    data.breaker_clock.now += 1
    entered = Event()
    release = Event()
    client = cache.get_redis_client()
    original_get = client.get
    commands = observe_commands(client, monkeypatch)

    def pause_generation(key, *args, **kwargs):
        if key == cache.catalog_generation_key():
            entered.set()
            assert release.wait(timeout=10)
        return original_get(key, *args, **kwargs)

    monkeypatch.setattr(client, "get", pause_generation)
    with ThreadPoolExecutor(max_workers=6) as executor:
        probe = executor.submit(request_with_sql, data.url)
        try:
            assert entered.wait(timeout=10)
            assert cache.detail_reads.state == "half_open"
            with limiter.slot(), limiter.slot():
                others = [executor.submit(request_with_sql, data.url) for _ in range(5)]
                rejected = [future.result(timeout=10) for future in others]
            assert all(result["status"] == 503 and result["sql"] == 0 for result in rejected)
            assert commands == []
            assert not probe.done()
        finally:
            release.set()
        result = probe.result(timeout=10)
    assert result["status"] == 200
    assert result["payload"] == expected
    assert result["sql"] == 1
    assert [command["command"] for command in commands] == ["GET", "GET", "GET"]
    assert cache.detail_reads.state == "closed"


def test_generation_bootstrap_set_failure_is_inside_read_operation(catalog_cache, limiter, monkeypatch):
    data = catalog_cache
    data.control.delete(cache.catalog_generation_key())
    client = cache.get_redis_client()
    original_execute = client.execute_command
    attempts = []

    def fail_initialization(*args, **kwargs):
        command = str(args[0]).upper()
        attempts.append(command)
        if command == "SET":
            raise redis.TimeoutError("Injected generation SET NX timeout")
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(client, "execute_command", fail_initialization)
    with limiter.slot(), limiter.slot():
        first = request_with_sql(data.url)
        second = request_with_sql(data.url)
    assert first["status"] == second["status"] == 503
    assert first["sql"] == second["sql"] == 0
    assert attempts == ["GET", "SET"]
    assert cache.detail_reads.state == "open"
    assert data.control.get(cache.catalog_generation_key()) is None


def test_open_read_breaker_does_not_skip_successful_commit_invalidation(catalog_cache, monkeypatch):
    data = catalog_cache
    old_payload, _ = get_detail_with_queries(data)
    open_reads()
    commands = observe_commands(cache.get_redis_client(), monkeypatch)
    data.product.name = "Changed while read breaker is open"
    data.product.save(update_fields=["name"])
    assert [command["command"] for command in commands] == ["SET"]
    assert commands[0]["outcome"] == "success"
    assert data.control.get(cache.catalog_generation_key()) != data.generation
    assert cache.detail_reads.state == "open"
    fresh, sql = get_detail_with_queries(data)
    assert fresh["name"] == data.product.name != old_payload["name"]
    assert sql == 2
    assert len(commands) == 1
    data.breaker_clock.now += 1
    assert get_detail_with_queries(data) == (fresh, 2)
    assert get_detail_with_queries(data) == (fresh, 1)


@pytest.mark.parametrize("outcome", ("miss", "broken_json", "invalid_generation", "not_found", "database_error"))
def test_non_network_api_outcomes_do_not_open_reads(catalog_cache, monkeypatch, outcome):
    data = catalog_cache
    if outcome == "broken_json":
        data.control.set(data.key, "{broken-json", ex=30)
    elif outcome == "invalid_generation":
        data.control.set(cache.catalog_generation_key(), "invalid-generation")
    elif outcome == "not_found":
        result = request_with_sql(data.url.replace("mouse-m1", "missing-product"))
        assert result["status"] == 404
    elif outcome == "database_error":
        def fail_load(view, product):
            raise DatabaseError("Injected isolated database error, not a Redis failure")

        monkeypatch.setattr(PublicProductDetailView, "load_detail", fail_load)
        with pytest.raises(DatabaseError):
            data.client.get(data.url)
    if outcome in {"miss", "broken_json", "invalid_generation"}:
        payload, sql = get_detail_with_queries(data)
        assert payload["name"] == data.product.name
        assert sql == 2
    assert cache.detail_reads.state == "closed"
