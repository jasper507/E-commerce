"""从负载实验原始输出独立重算指标；不连接数据库，也不平均分位数。"""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path


NS_PER_SECOND = 1_000_000_000
NS_PER_MS = 1_000_000


def percentiles(values):
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    return {"n": len(ordered), **{
        f"p{p}_ms": ordered[math.ceil(p * len(ordered) / 100) - 1] / NS_PER_MS
        for p in (50, 95, 99)
    }}


def peak_intervals(intervals):
    events = [(start, 1) for start, end in intervals if end > start]
    events += [(end, -1) for start, end in intervals if end > start]
    active = peak = 0
    for _, change in sorted(events):  # 同时刻先结束再开始，采用半开区间。
        active += change
        assert active >= 0
        peak = max(peak, active)
    assert active == 0
    return peak


def analyze_run(row, number):
    assert row["valid"] is True and row["diagnostics"] == []
    assert row["retry_backoff_ns"] == [5_000_000, 10_000_000]
    if row["warmup"]:
        assert row["profile"] == "warmup" and row["jobs_count"] == row["worker_count"] == 1
    else:
        assert row["profile"] in {"ample", "scarce"}
        assert row["initial_stock"] == {"ample": 24, "scarce": 6}[row["profile"]]
        assert row["jobs_count"] == 24 and row["worker_count"] == 8
        assert row["max_attempts"] in {1, 3} and row["repeat"] in {1, 2, 3}
    assert row["threads_all_joined"] is True and row["not_started_jobs"] == []
    assert row["observed_worker_connection_ids_after_close"] == []
    jobs, workers = row["jobs"], row["workers"]
    assert len(jobs) == row["jobs_count"] and len(workers) == row["worker_count"]
    by_worker = {worker["worker_id"]: worker for worker in workers}
    assert len(by_worker) == len(workers)
    ids = {worker["connection_id"] for worker in workers}
    assert len(ids) == len(workers)
    assert ids == set(row["observed_worker_connection_ids_before_release"])
    assert len({job["job_id"] for job in jobs}) == len(jobs)
    assert len({job["user_id"] for job in jobs}) == len(jobs)
    terminal = max(job["terminal_ns"] for job in jobs)
    assert terminal == row["last_terminal_ns"] and terminal > 0
    active_intervals, queue_ns, journey_ns, end_to_end_ns = [], [], [], []
    successful_journeys, attempts, backoff_ns = [], [], 0
    per_worker = defaultdict(list)
    for job in jobs:
        worker = by_worker[job["worker_id"]]
        assert job["connection_id"] == worker["connection_id"]
        assert job["submitted_ns"] == 0 <= job["started_ns"] <= job["terminal_ns"]
        assert 1 <= len(job["attempts"]) <= row["max_attempts"]
        assert job["terminal_code"] == job["attempts"][-1]["code"]
        queue_ns.append(job["started_ns"])
        duration = job["terminal_ns"] - job["started_ns"]
        journey_ns.append(duration)
        end_to_end_ns.append(job["terminal_ns"])
        if job["terminal_code"] == "created":
            assert job["created"] is True
            successful_journeys.append(duration)
        assert len(job["backoffs"]) == len(job["attempts"]) - 1
        for index, attempt in enumerate(job["attempts"]):
            start, end = attempt["started_ns"], attempt["ended_ns"]
            assert job["started_ns"] <= start <= end <= job["terminal_ns"]
            assert attempt["number"] == index + 1
            assert attempt["code"] in {"created", "stock_busy", "insufficient_stock"}
            if attempt["code"] == "stock_busy":
                assert attempt["mysql_error_number"] == 3572
            if index:
                before = job["attempts"][index - 1]
                backoff = job["backoffs"][index - 1]
                assert before["code"] == "stock_busy"
                assert before["ended_ns"] <= backoff["started_ns"] <= backoff["ended_ns"] <= start
                assert backoff["planned_ns"] == row["retry_backoff_ns"][index - 1]
                backoff_ns += backoff["ended_ns"] - backoff["started_ns"]
            active_intervals.append((start, end))
            per_worker[job["worker_id"]].append((start, end))
            attempts.append(attempt)
    for intervals in per_worker.values():
        ordered = sorted(intervals)
        assert all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:]))
    assert min(attempt["ended_ns"] for attempt in attempts) == row["first_service_end_ns"]
    open_intervals = []
    setup_ns = []
    raw_holding_ns = 0
    for worker in workers:
        assert worker["connection_closed"] is True
        assert worker["setup_started_ns"] <= worker["session_open_ns"] <= worker["setup_finished_ns"] <= 0
        assert 0 <= worker["close_started_ns"] <= worker["closed_ns"]
        assert all(worker["session_open_ns"] <= start <= end <= worker["close_started_ns"]
                   for start, end in per_worker[worker["worker_id"]])
        start, end = max(0, worker["session_open_ns"]), min(terminal, worker["closed_ns"])
        assert end >= start
        open_intervals.append((start, end))
        setup_ns.append(worker["setup_finished_ns"] - worker["setup_started_ns"])
        raw_holding_ns += worker["closed_ns"] - worker["session_open_ns"]
    active_ns = sum(end - start for start, end in active_intervals)
    holding_ns = sum(end - start for start, end in open_intervals)
    assert 0 <= active_ns <= holding_ns <= len(workers) * terminal
    assert peak_intervals(active_intervals) <= peak_intervals(open_intervals) == len(workers)
    outcomes = Counter(job["terminal_code"] for job in jobs)
    created = outcomes["created"]
    final = row["final"]
    assert all(count == created for count in final["fact_counts"].values())
    assert final["inventory"] == {"available_stock": row["initial_stock"] - created,
                                  "reserved_stock": created}
    assert {order["user_id"] for order in final["orders"]} == {
        job["user_id"] for job in jobs if job["terminal_code"] == "created"
    }
    assert len(final["carts"]) == len(jobs)
    return {
        "run_number": number, "strategy": row["strategy"], "profile": row["profile"],
        "repeat": row["repeat"], "initial_stock": row["initial_stock"],
        "worker_count": len(workers), "max_attempts": row["max_attempts"], "valid": True,
        "jobs": len(jobs), "created": created, "stock_busy": outcomes["stock_busy"],
        "insufficient_stock": outcomes["insufficient_stock"], "attempts": len(attempts),
        "success_rate": created / len(jobs),
        "terminal_busy_rate": outcomes["stock_busy"] / len(jobs),
        "terminal_sold_out_rate": outcomes["insufficient_stock"] / len(jobs),
        "unexpected_error_rate": outcomes["unexpected_error"] / len(jobs),
        "retry_attempts": len(attempts) - len(jobs),
        "attempt_outcomes": dict(Counter(attempt["code"] for attempt in attempts)),
        "batch_wall_ms": terminal / NS_PER_MS,
        "completed_journeys_per_second": len(jobs) * NS_PER_SECOND / terminal,
        "created_orders_per_second": created * NS_PER_SECOND / terminal,
        "attempt_latency": percentiles([end - start for start, end in active_intervals]),
        "journey_latency": percentiles(journey_ns), "queue_latency": percentiles(queue_ns),
        "batch_end_to_end_latency": percentiles(end_to_end_ns),
        "successful_journey_latency": percentiles(successful_journeys),
        "actual_backoff_ms_total": backoff_ns / NS_PER_MS,
        "connection_holding_seconds_in_batch": holding_ns / NS_PER_SECOND,
        "connection_holding_seconds_with_setup_and_cleanup": raw_holding_ns / NS_PER_SECOND,
        "service_active_seconds": active_ns / NS_PER_SECOND,
        "mean_open_connections_in_batch": holding_ns / terminal,
        "mean_active_service_calls": active_ns / terminal,
        "peak_open_connections": peak_intervals(open_intervals),
        "peak_active_service_calls": peak_intervals(active_intervals),
        "connection_setup": percentiles(setup_ns),
        "connection_seconds_per_created_order": holding_ns / NS_PER_SECOND / created if created else None,
        "final_inventory": final["inventory"], "final_fact_counts": final["fact_counts"],
    }


def analyze(raw_path):
    records, warmups = [], []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        for marker, collection in (("FLASH_SALE_LOAD_RUN ", records),
                                   ("FLASH_SALE_LOAD_WARMUP ", warmups)):
            if marker in line:
                collection.append(json.loads(line.split(marker, 1)[1]))
                break
    summaries = []
    for number, row in enumerate(records, 1):
        try:
            summaries.append(analyze_run(row, number))
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            summaries.append({"run_number": number, "valid": False, "strategy": row.get("strategy"),
                              "profile": row.get("profile"), "repeat": row.get("repeat"),
                              "max_attempts": row.get("max_attempts"),
                              "diagnostics": row.get("diagnostics", []),
                              "analysis_error": f"{type(exc).__name__}: {exc}"})
    expected = {(strategy, profile, budget, repeat)
                for strategy in ("nowait", "conditional") for profile in ("ample", "scarce")
                for budget in (1, 3) for repeat in (1, 2, 3)}
    observed = {(r.get("strategy"), r.get("profile"), r.get("max_attempts"), r.get("repeat"))
                for r in records}
    warmup_ok = len(warmups) == 2 and {r.get("strategy") for r in warmups} == {"nowait", "conditional"}
    if warmup_ok:
        try:
            for row in warmups:
                analyze_run(row, 0)
        except (AssertionError, KeyError, TypeError, ValueError):
            warmup_ok = False
    complete = len(records) == 24 and observed == expected and warmup_ok
    groups = []
    grouped = defaultdict(list)
    for row in summaries:
        if row["valid"]:
            grouped[(row["profile"], row["max_attempts"], row["strategy"])].append(row)
    for (profile, budget, strategy), runs in sorted(grouped.items()):
        ranges = {}
        for metric in ("batch_wall_ms", "connection_holding_seconds_in_batch", "service_active_seconds",
                       "connection_seconds_per_created_order", "created_orders_per_second"):
            values = [run[metric] for run in runs if run[metric] is not None]
            ranges[metric] = [min(values), max(values)] if values else None
        for metric in ("journey_latency", "batch_end_to_end_latency"):
            values = [run[metric]["p95_ms"] for run in runs]
            ranges[f"{metric}_per_run_p95_ms"] = [min(values), max(values)]
        groups.append({"profile": profile, "max_attempts": budget, "strategy": strategy,
                       "runs": len(runs), "run_numbers": [run["run_number"] for run in runs],
                       "created_each_run": [run["created"] for run in runs],
                       "busy_each_run": [run["stock_busy"] for run in runs],
                       "sold_out_each_run": [run["insufficient_stock"] for run in runs],
                       "attempts_each_run": [run["attempts"] for run in runs],
                       "observed_ranges": ranges})
    return {
        "raw_output": str(raw_path), "complete_matrix": complete, "warmups_verified": warmup_ok,
        "measured_run_count": len(records), "invalid_run_count": sum(not r["valid"] for r in summaries),
        "measurement": "Finite closed batch; service-level clocks, not HTTP or production capacity",
        "percentile_method": "nearest rank ceil(p*n); journey n=24, p99 is observed maximum",
        "groups": groups, "runs": summaries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.raw_output)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("complete_matrix", "warmups_verified",
                                                 "measured_run_count", "invalid_run_count")}))
    if not report["complete_matrix"] or report["invalid_run_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
