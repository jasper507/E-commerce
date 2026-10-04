"""Phase 4F baseline evidence, with read breaking explicitly bypassed."""

import json
import os
import platform
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event, Lock, local

import django
import pytest
import redis
from django.db import close_old_connections, connection
from redis.backoff import NoBackoff
from redis.retry import Retry
from rest_framework.test import APIClient

from apps.catalog import cache
from apps.catalog.models import SKU

from .test_detail_cache import catalog_cache, get_detail_with_queries
from .test_fallback_limit import connection_refused


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]
WORKERS = 6
PAUSE_MS = 2500


def save_report(report, scenario):
    print("redis_recovery " + json.dumps({
        "scenario": scenario, "summary": report["summary"],
    }, sort_keys=True))
    output = os.getenv("PROJECT_ONE_SLOW_REDIS_OUTPUT")
    if output:
        target = Path(output)
        target = target.with_name(f"{target.stem}-{scenario}{target.suffix}")
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def base_report(data):
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "django": django.get_version(),
            "os": platform.system(),
            "database": connection.settings_dict["NAME"],
            "mysql": connection.mysql_server_info,
            "redis": data.control.info("server")["redis_version"],
        },
    }


class UnprotectedReads:
    """Preserve the short-timeout baseline for comparison, only in tests."""

    def run(self, operation):
        return operation()


def test_real_slow_redis_then_recovery(catalog_cache, monkeypatch):
    monkeypatch.setattr(cache, "detail_reads", UnprotectedReads())
    run_slow_redis_experiment(catalog_cache, monkeypatch)


def run_slow_redis_experiment(data, monkeypatch, before_recovered=None):
    expected, _ = get_detail_with_queries(data)
    report = base_report(data)
    application_client = cache.get_redis_client()
    # A successful request above has established a real application connection.
    original_execute = application_client.execute_command
    observation = local()
    observation_lock = Lock()
    active = 0
    peak = 0
    pause_started = None
    limiter = cache.FallbackLimiter(capacity=2)
    original_slot = limiter.slot
    monkeypatch.setattr(cache, "detail_fallbacks", limiter)

    def observed_execute(*args, **kwargs):
        commands = getattr(observation, "commands", None)
        attempt = {"command": str(args[0]).upper(), "outcome": "success"}
        if commands is not None:
            commands.append(attempt)
        started = time.perf_counter()
        try:
            return original_execute(*args, **kwargs)
        except Exception as error:
            attempt.update(outcome="error", error_type=type(error).__name__)
            raise
        finally:
            attempt["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)

    @contextmanager
    def observed_slot():
        nonlocal active, peak
        with original_slot():
            with observation_lock:
                active += 1
                peak = max(peak, active)
            try:
                yield
            finally:
                with observation_lock:
                    active -= 1

    monkeypatch.setattr(application_client, "execute_command", observed_execute)
    monkeypatch.setattr(limiter, "slot", observed_slot)

    def wave(name, before_release=None):
        nonlocal peak
        peak = 0
        ready = Barrier(WORKERS + 1)
        release = Event()

        def request(worker):
            close_old_connections()
            try:
                connection.ensure_connection()
                client = APIClient()
                observation.commands = []
                ready.wait(timeout=15)
                assert release.wait(timeout=15), "Coordinator did not release requests"
                statements = []

                def capture(execute, sql, params, many, context):
                    statements.append(sql.lower())
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(capture):
                    started = time.perf_counter()
                    response = client.get(data.url)
                    payload = response.json()
                    finished = time.perf_counter()
                    elapsed = (finished - started) * 1000
                return {
                    "worker": worker,
                    "status": response.status_code,
                    "payload_matches": payload == expected if response.status_code == 200 else None,
                    "sql": len(statements),
                    "guard_sql": sum("from `catalog_product`" in sql for sql in statements),
                    "reload_sql": sum("from `catalog_sku`" in sql for sql in statements),
                    "elapsed_ms": round(elapsed, 3),
                    "started_after_pause_ms": (
                        round((started - pause_started) * 1000, 3)
                        if pause_started is not None else None
                    ),
                    "finished_after_pause_ms": (
                        round((finished - pause_started) * 1000, 3)
                        if pause_started is not None else None
                    ),
                    "redis_commands": observation.commands,
                }
            finally:
                observation.commands = None
                connection.close()

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = [executor.submit(request, index) for index in range(WORKERS)]
            try:
                ready.wait(timeout=15)
                if before_release is not None:
                    before_release()
            finally:
                release.set()
            results = [future.result(timeout=20) for future in futures]
        errors = Counter(
            command["error_type"]
            for result in results for command in result["redis_commands"]
            if command["outcome"] == "error"
        )
        timings = sorted(result["elapsed_ms"] for result in results)
        summary = {
            "requests": len(results),
            "successes": sum(result["status"] == 200 for result in results),
            "rejected_503": sum(result["status"] == 503 for result in results),
            "sql": sum(result["sql"] for result in results),
            "reload_requests": sum(result["reload_sql"] for result in results),
            "application_command_attempts": sum(len(result["redis_commands"]) for result in results),
            "application_command_types": dict(Counter(
                command["command"]
                for result in results for command in result["redis_commands"]
            )),
            "get_attempts": sum(
                command["command"] == "GET"
                for result in results for command in result["redis_commands"]
            ),
            "redis_errors": dict(errors),
            "peak_admitted_requests": peak,
            "p50_ms": timings[(len(timings) - 1) // 2],
            "p95_ms": timings[-1],
            "max_ms": timings[-1],
        }
        assert active == 0
        assert peak <= 2
        for result in results:
            assert result["status"] in {200, 503}, result
            if result["status"] == 200:
                assert result["payload_matches"], result
                assert result["guard_sql"] == 1, result
                assert result["sql"] == 1 + result["reload_sql"], result
            else:
                assert result["sql"] == result["guard_sql"] == result["reload_sql"] == 0, result
        print("slow_redis_wave " + json.dumps({"name": name, **summary}, sort_keys=True))
        return {"name": name, "summary": summary, "requests": results}

    report["method"] = {
        "workers_per_wave": WORKERS,
        "failure": f"isolated real Redis CLIENT PAUSE {PAUSE_MS} ALL; automatically resumes",
        "application_timeout_ms": 100,
        "application_retries": 0,
        "timing": "DRF APIClient through response JSON; excludes DB connect and start rendezvous",
        "command_count": "application execute_command calls, not server execution or handshake wire commands",
        "concurrency": "observe admitted fallback scopes; no artificial holding of database queries",
        "percentiles": "nearest rank; six samples per wave, p95 equals maximum",
        "scope": "one process; controlled slow dependency experiment, not a capacity benchmark",
    }
    report["waves"] = [wave("healthy")]
    healthy = report["waves"][0]
    assert healthy["summary"]["successes"] == WORKERS
    assert healthy["summary"]["sql"] == WORKERS
    assert healthy["summary"]["reload_requests"] == 0
    assert healthy["summary"]["redis_errors"] == {}

    recovery_control = redis.Redis.from_url(
        data.redis_url, socket_connect_timeout=1, socket_timeout=3,
        retry=Retry(NoBackoff(), 0),
    )

    def start_pause():
        nonlocal pause_started
        assert recovery_control.execute_command("CLIENT", "PAUSE", PAUSE_MS, "ALL")
        pause_started = time.perf_counter()

    try:
        report["waves"].append(wave("slow_first", before_release=start_pause))
        report["waves"].append(wave("slow_continued"))
        report["slow_waves_completed_after_pause_ms"] = round(
            (time.perf_counter() - pause_started) * 1000, 3
        )
    finally:
        # Wait for automatic resume before fixture cleanup sends SCAN/DELETE.
        try:
            assert recovery_control.ping()
        finally:
            recovery_control.connection_pool.disconnect()

    if before_recovered is not None:
        before_recovered()
    report["waves"].append(wave("recovered"))
    recovered = report["waves"][-1]["summary"]
    assert recovered["successes"] == WORKERS
    assert recovered["sql"] == WORKERS
    assert recovered["reload_requests"] == 0
    assert recovered["redis_errors"] == {}
    slow = report["waves"][1:3]
    assert sum(item["summary"]["redis_errors"].get("TimeoutError", 0) for item in slow) >= 1
    for item in slow:
        item["completed_within_nominal_pause_window"] = all(
            result["finished_after_pause_ms"] < PAUSE_MS for result in item["requests"]
        )
        assert all(result["started_after_pause_ms"] < PAUSE_MS for result in item["requests"]), item
        for result in item["requests"]:
            if result["status"] == 200 and any(
                command["outcome"] == "error" for command in result["redis_commands"]
            ):
                assert result["reload_sql"] == 1, result
    report["summary"] = {item["name"]: item["summary"] for item in report["waves"]}
    save_report(report, "slow")


def test_real_failed_invalidation_recovers_with_old_price(catalog_cache, settings, caplog, monkeypatch):
    monkeypatch.setattr(cache, "detail_reads", UnprotectedReads())
    data = catalog_cache
    report = base_report(data)
    warmed, warm_sql = get_detail_with_queries(data)
    original_generation = cache.get_catalog_generation()
    original_key = cache.product_detail_key(data.product.pk, original_generation)
    original_ttl = data.control.ttl(original_key)
    assert warmed["skus"][0]["price"] == "129.00"
    assert warm_sql == 2

    with connection_refused(data, settings):
        data.sku.price = Decimal("169.00")
        data.sku.save(update_fields=["price"])
        assert any(
            record.getMessage() == "catalog_cache_invalidation_failed"
            for record in caplog.records
        ), "The real post-commit invalidation attempt must fail, not be skipped"
        during_failure, failure_sql = get_detail_with_queries(data)
        assert during_failure["skus"][0]["price"] == "169.00"
        assert failure_sql == 2

    restored_generation = cache.get_catalog_generation()
    restored, recovery_sql = get_detail_with_queries(data)
    authoritative_price = str(SKU.objects.get(pk=data.sku.pk).price)
    assert restored_generation == original_generation
    assert authoritative_price == "169.00"
    assert restored["skus"][0]["price"] == "129.00"
    assert recovery_sql == 1
    remaining_ttl = data.control.ttl(original_key)
    assert remaining_ttl > 0

    # A subsequent normal save successfully rotates the namespace and repairs reads.
    data.sku.save(update_fields=["price"])
    repaired_generation = cache.get_catalog_generation()
    repaired, repaired_sql = get_detail_with_queries(data)
    subsequent, subsequent_sql = get_detail_with_queries(data)
    assert repaired_generation != original_generation
    assert repaired["skus"][0]["price"] == "169.00"
    assert repaired_sql == 2
    assert subsequent == repaired
    assert subsequent_sql == 1

    report["method"] = {
        "failure": "application connection refused at a reserved non-listening socket; real Redis remains running",
        "write": "normal isolated MySQL SKU.save, committed; on_commit generation SET cannot connect",
        "recovery": "restore original application Redis URL while old detail has remaining TTL",
        "separation": "independent of slow pause experiment; timeout never proves a Redis write was not executed",
        "scope": "existing stale recovery boundary reproduced, not fixed",
        "sql": "detail request SQL only; setup and direct authoritative price check excluded",
    }
    report["scenarios"] = [
        {"name": "warm", "status": 200, "price": "129.00", "sql": warm_sql},
        {"name": "failure_after_committed_write", "status": 200, "price": "169.00", "sql": failure_sql},
        {"name": "redis_recovered_same_generation", "status": 200, "price": "129.00", "sql": recovery_sql},
        {"name": "successful_rotation", "status": 200, "price": "169.00", "sql": repaired_sql},
        {"name": "repaired_warm_hit", "status": 200, "price": "169.00", "sql": subsequent_sql},
    ]
    report["summary"] = {
        "database_price_after_failure": authoritative_price,
        "display_price_after_recovery": restored["skus"][0]["price"],
        "generation_unchanged_after_failure": restored_generation == original_generation,
        "generation_changed_after_successful_save": repaired_generation != original_generation,
        "ttl_before_failure_seconds": original_ttl,
        "ttl_on_stale_recovery_seconds": remaining_ttl,
        "display_price_after_successful_rotation": repaired["skus"][0]["price"],
    }
    save_report(report, "stale")
