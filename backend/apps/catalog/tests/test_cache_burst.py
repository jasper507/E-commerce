"""Replay failure-only admission in small bursts, not a capacity benchmark."""

import json
import math
import os
import platform
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, local

import django
import pytest
from django.db import close_old_connections, connection
from rest_framework.test import APIClient

from apps.catalog.cache import (
    ProductDetailCache,
    get_redis_client,
    invalidate_catalog_details,
)
from .test_detail_cache import catalog_cache, current_key, get_detail_with_queries
from .test_fallback_limit import failure_only_admission


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]
WORKERS = 10
ROUNDS = 3


def percentile(samples, fraction):
    """Nearest-rank percentile; tiny samples are descriptive only."""
    ordered = sorted(samples)
    return round(ordered[math.ceil(len(ordered) * fraction) - 1], 3)


def run_burst(data, baseline, observation):
    gate = Barrier(WORKERS)

    def request(worker):
        close_old_connections()
        try:
            # Exclude new DB connections and thread rendezvous from request timing.
            connection.ensure_connection()
            client = APIClient()
            observation.state = None
            gate.wait(timeout=10)
            sql = []

            def capture(execute, statement, params, many, context):
                sql.append(statement.lower())
                return execute(statement, params, many, context)

            # A thread-local wrapper avoids CaptureQueriesContext's global signal changes.
            with connection.execute_wrapper(capture):
                started = time.perf_counter()
                response = client.get(data.url)
                payload = response.json()
                elapsed = (time.perf_counter() - started) * 1000
            result = {
                "worker": worker,
                "status": response.status_code,
                "payload_matches": payload == baseline,
                "cache_state": observation.state,
                "sql": len(sql),
                "guard_sql": sum("from `catalog_product`" in item for item in sql),
                "reload_sql": sum("from `catalog_sku`" in item for item in sql),
                "elapsed_ms": round(elapsed, 3),
            }
            return result
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [executor.submit(request, worker) for worker in range(WORKERS)]
        results = [future.result(timeout=30) for future in futures]

    for result in results:
        if result["status"] == 503:
            assert not result["payload_matches"], result
            assert result["cache_state"] is None, result
            assert result["sql"] == result["guard_sql"] == result["reload_sql"] == 0, result
            continue
        assert result["status"] == 200, result
        assert result["payload_matches"], result
        assert result["cache_state"] in {"hit", "miss", "unavailable"}, result
        assert result["guard_sql"] == 1, result
        assert result["reload_sql"] in {0, 1}, result
        if result["cache_state"] == "hit":
            assert result["reload_sql"] == 0, result
        elif result["cache_state"] == "unavailable":
            assert result["reload_sql"] == 1, result
        assert result["sql"] == result["guard_sql"] + result["reload_sql"], result
    timings = [result["elapsed_ms"] for result in results]
    summary = {
        "requests": len(results),
        "successes": sum(result["status"] == 200 for result in results),
        "rejections": sum(result["status"] == 503 for result in results),
        "hits": sum(result["cache_state"] == "hit" for result in results),
        "misses": sum(result["cache_state"] == "miss" for result in results),
        "unavailable": sum(result["cache_state"] == "unavailable" for result in results),
        "sql": sum(result["sql"] for result in results),
        "reload_requests": sum(result["reload_sql"] for result in results),
        "miss_without_reload": sum(
            result["cache_state"] == "miss" and result["reload_sql"] == 0
            for result in results
        ),
        "p50_ms": percentile(timings, 0.5),
        "p95_ms": percentile(timings, 0.95),
        "max_ms": max(timings),
    }
    return {"summary": summary, "requests": results}


@pytest.mark.parametrize("strategy", ("unmerged", "singleflight"))
def test_real_cache_bursts(catalog_cache, settings, monkeypatch, strategy, failure_only_admission):
    data = catalog_cache
    if strategy == "unmerged":
        # Preserve the Phase 4C get/load/set path as a comparison candidate.
        def unmerged(cache, product_id, load):
            payload = cache.get(product_id)
            if payload is None:
                payload = load()
                cache.set(payload)
            return payload

        monkeypatch.setattr(ProductDetailCache, "get_or_load", unmerged)
    baseline, _ = get_detail_with_queries(data)
    observation = local()
    original_get = ProductDetailCache.get

    def observe_get(cache, product_id):
        payload = original_get(cache, product_id)
        if getattr(observation, "state", None) is None:
            observation.state = (
                "hit" if payload is not None else "miss" if cache.available else "unavailable"
            )
        return payload

    monkeypatch.setattr(ProductDetailCache, "get", observe_get)
    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "environment": {
            "python": platform.python_version(),
            "django": django.get_version(),
            "os": platform.system(),
            "mysql": connection.mysql_server_info,
            "redis": data.control.info("server")["redis_version"],
            "database": connection.settings_dict["NAME"],
        },
        "method": {
            "workers": WORKERS,
            "rounds_per_scenario": ROUNDS,
            "client": "DRF APIClient; no HTTP server/network",
            "timing": "request through response JSON; excludes DB connect and start barrier",
            "cold": "real generation rotation, natural scheduling; no forced miss barrier",
            "failure": "connection refused at bound non-listening local TCP socket",
            "percentiles": "nearest rank; each round p95 of 10 samples equals maximum",
            "fallback": "historical failure-only admission replay for both strategies; 503 has zero SQL",
        },
        "runs": [],
    }
    for scenario in ("warm", "cold", "connection_refused"):
        failed_socket = None
        if scenario == "connection_refused":
            failed_socket = socket.socket()
            failed_socket.bind(("127.0.0.1", 0))
            settings.CATALOG_CACHE_REDIS_URL = (
                f"redis://127.0.0.1:{failed_socket.getsockname()[1]}/0"
            )
            get_redis_client().connection_pool.disconnect()
            get_redis_client.cache_clear()
            get_redis_client()
        try:
            for round_number in range(1, ROUNDS + 1):
                if scenario == "warm":
                    get_detail_with_queries(data)
                    assert data.control.get(current_key(data.product)) is not None
                elif scenario == "cold":
                    invalidate_catalog_details()
                    assert data.control.get(current_key(data.product)) is None
                result = run_burst(data, baseline, observation)
                result.update(scenario=scenario, round=round_number)
                summary = result["summary"]
                if scenario == "warm":
                    assert summary["successes"] == WORKERS
                    assert summary["hits"] == WORKERS
                elif scenario == "cold":
                    assert summary["successes"] == WORKERS
                    assert 1 <= summary["misses"] <= WORKERS
                    assert summary["unavailable"] == 0
                    if strategy == "unmerged":
                        assert summary["reload_requests"] == summary["misses"]
                    else:
                        # Natural scheduling can exceed the wait budget and fall back.
                        # Controlled overlap tests separately require exactly one load.
                        assert 1 <= summary["reload_requests"] <= summary["misses"]
                else:
                    assert summary["successes"] + summary["rejections"] == WORKERS
                    assert summary["successes"] >= 1
                    assert summary["unavailable"] == summary["successes"]
                    assert summary["reload_requests"] == summary["successes"]
                    assert summary["sql"] == 2 * summary["successes"]
                report["runs"].append(result)
                print("cache_burst " + json.dumps({
                    "strategy": strategy, "scenario": scenario, "round": round_number,
                    **summary
                }, sort_keys=True))
        finally:
            if failed_socket is not None:
                get_redis_client().connection_pool.disconnect()
                get_redis_client.cache_clear()
                settings.CATALOG_CACHE_REDIS_URL = data.redis_url
                failed_socket.close()
    output = os.getenv("PROJECT_ONE_CACHE_EXPERIMENT_OUTPUT")
    if output:
        target = Path(output)
        target = target.with_name(f"{target.stem}-{strategy}{target.suffix}")
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
