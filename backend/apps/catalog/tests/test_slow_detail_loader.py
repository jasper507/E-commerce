"""Replay historical failure-only admission for a slow loader with healthy Redis.

The delay is an Event in the Python business loader, before SKU SQL. It is not
a slow MySQL server experiment, HTTP benchmark, or response deadline assertion.
"""

import json
import os
import time
from concurrent.futures import Future, TimeoutError
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread, local

import pytest
from django.db import close_old_connections, connection
from rest_framework.test import APIClient

from apps.catalog import cache
from apps.catalog.views import PublicProductDetailView

from .test_detail_cache import catalog_cache, current_key, get_detail_with_queries
from .test_fallback_limit import failure_only_admission


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]
WORKERS = 5


def test_healthy_redis_slow_owner_allows_independent_timeout_loads(
    catalog_cache, monkeypatch, failure_only_admission
):
    data = catalog_cache
    expected, _ = get_detail_with_queries(data)
    cache.invalidate_catalog_details()
    assert data.control.get(current_key(data.product)) is None

    owner_entered = Event()
    all_loaders_entered = Event()
    release = Event()
    lock = Lock()
    observation = local()
    outcomes = Queue()
    threads = []
    loads = []
    counters = {"active": 0, "peak": 0, "fallback_slot_calls": 0}
    redis_client = cache.get_redis_client()
    original_command = redis_client.execute_command
    original_result = Future.result
    original_load = PublicProductDetailView.load_detail
    original_slot = cache.detail_fallbacks.slot

    def observe_command(*args, **kwargs):
        state = getattr(observation, "state", None)
        if state is not None:
            command = str(args[0]).upper()
            state["redis_commands"].append(command)
        return original_command(*args, **kwargs)

    def observe_result(future, timeout=None):
        state = getattr(observation, "state", None)
        if state is not None:
            state["future_wait_timeouts_s"].append(timeout)
        try:
            return original_result(future, timeout=timeout)
        except TimeoutError:
            if state is not None:
                state["future_wait_expirations"] += 1
            raise

    def observe_slot():
        if getattr(observation, "state", None) is not None:
            with lock:
                counters["fallback_slot_calls"] += 1
        return original_slot()

    def slow_load(view, product):
        state = observation.state
        with lock:
            loads.append(state["worker"])
            counters["active"] += 1
            counters["peak"] = max(counters["peak"], counters["active"])
            if state["worker"] == "owner":
                owner_entered.set()
            if len(loads) == WORKERS:
                all_loaders_entered.set()
        try:
            assert release.wait(timeout=10), "Main thread did not release business loaders"
            return original_load(view, product)
        finally:
            with lock:
                counters["active"] -= 1

    monkeypatch.setattr(redis_client, "execute_command", observe_command)
    monkeypatch.setattr(Future, "result", observe_result)
    monkeypatch.setattr(cache.detail_fallbacks, "slot", observe_slot)
    monkeypatch.setattr(PublicProductDetailView, "load_detail", slow_load)

    def request(worker):
        close_old_connections()
        try:
            connection.ensure_connection()
            client = APIClient()
            state = {
                "worker": worker,
                "redis_commands": [],
                "future_wait_timeouts_s": [],
                "future_wait_expirations": 0,
            }
            observation.state = state
            sql = []

            def record(execute, statement, params, many, context):
                sql.append(statement.lower())
                return execute(statement, params, many, context)

            with connection.execute_wrapper(record):
                started = time.perf_counter()
                response = client.get(data.url)
                payload = response.json()
                elapsed = (time.perf_counter() - started) * 1000
            state.update(
                status=response.status_code,
                payload_matches=payload == expected,
                sql=len(sql),
                guard_sql=sum("from `catalog_product`" in item for item in sql),
                reload_sql=sum("from `catalog_sku`" in item for item in sql),
                elapsed_ms=round(elapsed, 3),
            )
            outcomes.put((state, None))
        except BaseException as error:
            outcomes.put((None, error))
        finally:
            connection.close()

    def start(worker):
        thread = Thread(target=request, args=(worker,), name=f"slow-loader-{worker}", daemon=True)
        threads.append(thread)
        thread.start()

    try:
        start("owner")
        assert owner_entered.wait(timeout=10), "Owner did not enter the business loader"
        for number in range(1, WORKERS):
            start(f"follower-{number}")
        assert all_loaders_entered.wait(timeout=10), "Followers did not independently load after timeout"
        # Hold every loader before its SKU SQL so none can fill the cache early.
        with lock:
            assert counters["active"] == WORKERS
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads), "Reader did not finish"
    results = []
    for _ in range(WORKERS):
        result, error = outcomes.get_nowait()
        assert error is None, error
        results.append(result)
    results.sort(key=lambda result: result["worker"])
    for result in results:
        assert result["status"] == 200 and result["payload_matches"], result
        assert result["sql"] == 2, result
        assert result["guard_sql"] == result["reload_sql"] == 1, result
        assert result["redis_commands"].count("GET") == 4, result
        assert result["redis_commands"].count("SET") == 1, result
        if result["worker"] == "owner":
            assert result["future_wait_timeouts_s"] == [], result
            assert result["future_wait_expirations"] == 0, result
        else:
            assert result["future_wait_timeouts_s"] == [cache.DETAIL_WAIT_SECONDS], result
            assert result["future_wait_expirations"] == 1, result
    assert len(loads) == counters["peak"] == WORKERS
    assert counters["active"] == 0
    assert counters["fallback_slot_calls"] == 0
    assert data.control.get(current_key(data.product)) is not None
    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": "healthy_redis_slow_business_loader",
        "method": {
            "workers": WORKERS,
            "follower_wait_budget_s": cache.DETAIL_WAIT_SECONDS,
            "configured_failure_slots": cache.FALLBACK_CONCURRENCY,
            "delay": "Python load_detail Event before SKU SQL; release after all 5 enter",
            "redis": "Real isolated Redis; no injected network failure",
            "mysql": "Real isolated MySQL test database",
            "timing": "DRF APIClient, includes artificial Event delay; excludes DB connect",
            "scope": "Controlled branch evidence; not DB timeout or capacity benchmark",
            "admission": "historical failure-only admission replay; healthy timeout loads are unbounded",
        },
        "summary": {
            "requests": len(results),
            "successes": sum(result["status"] == 200 for result in results),
            "sql": sum(result["sql"] for result in results),
            "detail_loads": len(loads),
            "peak_active_loaders": counters["peak"],
            "fallback_slot_calls": counters["fallback_slot_calls"],
            "follower_wait_expirations": sum(result["future_wait_expirations"] for result in results),
            "redis_get_attempts": sum(result["redis_commands"].count("GET") for result in results),
            "redis_set_attempts": sum(result["redis_commands"].count("SET") for result in results),
        },
        "requests": results,
    }
    print("slow_detail_loader " + json.dumps(report["summary"], sort_keys=True))
    output = os.getenv("PROJECT_ONE_SLOW_LOADER_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
