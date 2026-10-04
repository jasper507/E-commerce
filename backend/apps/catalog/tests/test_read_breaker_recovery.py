"""One-process real Redis comparison; isolated fixtures own all data and keys."""

import json
import os
import time
from pathlib import Path

import pytest

from apps.catalog import cache
from apps.catalog.circuit_breaker import RedisReadCircuitBreaker

from . import test_redis_slow_recovery as slow_recovery
from .test_detail_cache import catalog_cache, get_detail_with_queries
from .test_fallback_limit import connection_refused
from .test_read_circuit_breaker import FakeClock


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]


class Passthrough:
    """Keep the previous short-timeout behavior for the comparison baseline."""

    def run(self, operation):
        return operation()


def observe_commands(client, monkeypatch):
    commands = []
    original = client.execute_command

    def execute(*args, **kwargs):
        command = {"command": str(args[0]).upper(), "outcome": "success"}
        commands.append(command)
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        except Exception as error:
            command.update(outcome="error", error_type=type(error).__name__)
            raise
        finally:
            command["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)

    monkeypatch.setattr(client, "execute_command", execute)
    return commands


def command_metrics(commands):
    return {
        "get_attempts": sum(command["command"] == "GET" for command in commands),
        "command_elapsed_ms": round(sum(command["elapsed_ms"] for command in commands), 3),
        "get_elapsed_ms": round(sum(
            command["elapsed_ms"] for command in commands if command["command"] == "GET"
        ), 3),
    }


def test_real_slow_redis_read_breaker_comparison(catalog_cache):
    reports = {}
    for name, strategy in (
        ("short_timeout_only", Passthrough()),
        ("read_breaker", RedisReadCircuitBreaker()),
    ):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cache, "detail_reads", strategy)
            captured = []
            patch.setattr(slow_recovery, "save_report", lambda report, scenario: captured.append(report))
            recovery_probe = {}

            def before_recovered():
                # The six warm requests follow this separate real recovery probe.
                # They do not assert that callers arriving during Half-open hit cache.
                with pytest.MonkeyPatch.context() as probe_patch:
                    commands = observe_commands(cache.get_redis_client(), probe_patch)
                    before = getattr(strategy, "state", None)
                    started = time.perf_counter()
                    generation = strategy.run(cache.get_catalog_generation)
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                assert generation == catalog_cache.generation
                recovery_probe.update(
                    state_before=before,
                    state_after=getattr(strategy, "state", None),
                    elapsed_ms=elapsed_ms,
                    redis_commands=commands,
                    **command_metrics(commands),
                )

            slow_recovery.run_slow_redis_experiment(
                catalog_cache, patch, before_recovered=before_recovered
            )
            assert len(captured) == 1
            report = captured[0]
            report["method"]["read_strategy"] = name
            report["method"]["recovery_order"] = (
                "control PING waits for real resume; one separately observed generation probe; "
                "then six warm requests"
            )
            report["recovery_probe"] = recovery_probe
            for wave in report["waves"]:
                commands = [command for request in wave["requests"]
                            for command in request["redis_commands"]]
                wave["summary"].update(command_metrics(commands))
                wave["summary"]["all_success_payloads_match"] = all(
                    request["payload_matches"] for request in wave["requests"]
                    if request["status"] == 200
                )
            reports[name] = report

    baseline = reports["short_timeout_only"]["summary"]
    protected = reports["read_breaker"]["summary"]
    for name in ("slow_first", "slow_continued"):
        assert baseline[name]["get_attempts"] == slow_recovery.WORKERS
        assert baseline[name]["redis_errors"] == {"TimeoutError": slow_recovery.WORKERS}
    assert 1 <= protected["slow_first"]["get_attempts"] <= slow_recovery.WORKERS
    assert protected["slow_continued"]["get_attempts"] == 0
    assert protected["slow_continued"]["application_command_attempts"] == 0
    assert protected["slow_continued"]["redis_errors"] == {}
    probe = reports["read_breaker"]["recovery_probe"]
    assert probe["state_before"] == "open"
    assert probe["state_after"] == "closed"
    assert probe["get_attempts"] == 1
    assert all(command["outcome"] == "success" for command in probe["redis_commands"])

    comparison = {
        "schema_version": 1,
        "scope": "real isolated MySQL/Redis; one process; controlled faults, not a load benchmark",
        "reports": reports,
    }
    print("read_breaker_comparison " + json.dumps({
        name: report["summary"] for name, report in reports.items()
    }, sort_keys=True))
    output = os.getenv("PROJECT_ONE_READ_BREAKER_OUTPUT")
    if output:
        Path(output).write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def test_real_failed_half_open_probe_reopens_then_recovers(catalog_cache, settings, monkeypatch):
    data = catalog_cache
    clock = FakeClock()
    breaker = RedisReadCircuitBreaker(clock=clock)
    monkeypatch.setattr(cache, "detail_reads", breaker)
    monkeypatch.setattr(cache, "detail_fallbacks", cache.FallbackLimiter(capacity=2))
    expected, _ = get_detail_with_queries(data)
    observations = []

    def request(name):
        with pytest.MonkeyPatch.context() as patch:
            commands = observe_commands(cache.get_redis_client(), patch)
            payload, sql = get_detail_with_queries(data)
        assert payload == expected
        observations.append({
            "name": name,
            "state_after": breaker.state,
            "sql": sql,
            "payload_matches": payload == expected,
            "redis_commands": commands,
            **command_metrics(commands),
        })
        return observations[-1]

    with connection_refused(data, settings):
        first = request("initial_connection_failure")
        assert first["state_after"] == "open"
        assert first["get_attempts"] == 1
        assert first["redis_commands"][0]["error_type"] in {"ConnectionError", "TimeoutError"}
        skipped = request("open_skips")
        assert skipped["redis_commands"] == []
        clock.advance(1)
        failed_probe = request("half_open_connection_failure")
        assert failed_probe["state_after"] == "open"
        assert failed_probe["get_attempts"] == 1
        assert failed_probe["redis_commands"][0]["error_type"] in {"ConnectionError", "TimeoutError"}
        skipped_again = request("new_cooldown_skips")
        assert skipped_again["redis_commands"] == []
    for observation in observations:
        assert observation["sql"] == 2

    clock.advance(1)
    restored = request("successful_probe_and_cached_detail")
    assert restored["state_after"] == "closed"
    assert restored["get_attempts"] == 3
    assert restored["sql"] == 1
    assert all(command["outcome"] == "success" for command in restored["redis_commands"])
    print("read_breaker_failed_probe " + json.dumps(observations, sort_keys=True))
    output = os.getenv("PROJECT_ONE_READ_BREAKER_OUTPUT")
    if output:
        target = Path(output)
        target = target.with_name(f"{target.stem}-failed-probe{target.suffix}")
        target.write_text(json.dumps({
            "schema_version": 1,
            "method": "real non-listening local port; Windows may report connection timeout; controlled clock",
            "requests": observations,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
