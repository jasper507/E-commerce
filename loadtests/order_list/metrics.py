"""Offline statistics for order-list load runs; this module performs no I/O.

Samples form the cohort started inside a steady-state window. Late completions
remain in latency and failure statistics; only throughput uses window finishes.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
import math
from numbers import Real


def _records(value, name):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be a sequence of objects")
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ValueError(f"{name}[{index}] must be an object")
    return value


def _required(row, field, context):
    if field not in row:
        raise ValueError(f"{context}.{field} is required")
    return row[field]


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        condition = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be finite and {condition}")
    return result


def _integer(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string without outer whitespace")
    return value


def _percentile(ordered, percentile):
    if not ordered:
        return None
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def _ratio(numerator, denominator, name):
    try:
        result = numerator / denominator
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError(f"{name} must have a finite ratio") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must have a finite ratio")
    return result


def summarize_samples(samples, duration_seconds):
    """Summarize a steady-state start cohort, including its drained completions.

    ``status=0`` can represent a transport failure without an HTTP response.
    ``ok`` is the caller's business validation result, not inferred from status.
    Empty-cohort failure rate and percentiles are undefined and return ``None``.
    """
    duration = _number(duration_seconds, "duration_seconds", positive=True)
    samples = _records(samples, "samples")
    elapsed = []
    statuses = Counter()
    successes = completed = successful_completions = late = 0
    for index, row in enumerate(samples):
        context = f"samples[{index}]"
        elapsed.append(_number(_required(row, "elapsed_ms", context), f"{context}.elapsed_ms"))
        ok = _boolean(_required(row, "ok", context), f"{context}.ok")
        status = _integer(_required(row, "status", context), f"{context}.status")
        in_window = _boolean(
            _required(row, "finished_in_window", context), f"{context}.finished_in_window",
        )
        statuses[str(status)] += 1
        successes += int(ok)
        completed += int(in_window)
        successful_completions += int(in_window and ok)
        late += int(not in_window)
    elapsed.sort()
    count = len(samples)
    failures = count - successes
    return {
        "sample_count": count,
        "success_count": successes,
        "failure_count": failures,
        "failure_rate": failures / count if count else None,
        "completed_rps": _ratio(completed, duration, "completed_rps"),
        "success_rps": _ratio(successful_completions, duration, "success_rps"),
        "p50_ms": _percentile(elapsed, 50),
        "p95_ms": _percentile(elapsed, 95),
        "p99_ms": _percentile(elapsed, 99),
        "status_counts": dict(sorted(statuses.items(), key=lambda pair: int(pair[0]))),
        "late_completions": late,
    }


def _stage(row, index):
    context = f"stages[{index}]"
    value = lambda field: _required(row, field, context)
    normalized = {
        field: _integer(value(field), f"{context}.{field}", minimum=1)
        for field in ("users", "total_orders", "page")
    }
    for field in ("index_mode", "status"):
        normalized[field] = _text(value(field), f"{context}.{field}")
    for field in ("valid_for_capacity", "client_cpu_saturated"):
        normalized[field] = _boolean(value(field), f"{context}.{field}")
    for field in ("resource_samples", "sample_count"):
        normalized[field] = _integer(value(field), f"{context}.{field}")
    normalized["success_rps"] = _number(value("success_rps"), f"{context}.success_rps")
    p95 = value("p95_ms")
    if p95 is None and normalized["sample_count"] == 0:
        normalized["p95_ms"] = None
    else:
        normalized["p95_ms"] = _number(p95, f"{context}.p95_ms")
    if normalized["sample_count"] == 0 and normalized["success_rps"] != 0:
        raise ValueError(f"{context} cannot report success_rps without request samples")
    return normalized


def _exclusions(row):
    reasons = []
    if row["status"] != "complete":
        reasons.append("stage_not_complete")
    if not row["valid_for_capacity"]:
        reasons.append("stage_marked_invalid")
    if row["resource_samples"] < 2:
        reasons.append("insufficient_resource_samples")
    if row["client_cpu_saturated"]:
        reasons.append("client_cpu_saturated")
    if row["sample_count"] == 0:
        reasons.append("no_request_samples")
    return reasons


def analyze_stages(stages):
    """Find a load-knee candidate, never assert a database bottleneck.

    Inputs must retain run order with strictly increasing user counts and the
    same dataset size, page, and index mode. An invalid middle stage is a gap:
    its neighbors must not be compared across that missing evidence.
    """
    normalized = [_stage(row, index) for index, row in enumerate(_records(stages, "stages"))]
    if not normalized:
        raise ValueError("stages must not be empty")
    config = {field: normalized[0][field] for field in ("total_orders", "page", "index_mode")}
    for index, row in enumerate(normalized):
        if any(row[field] != expected for field, expected in config.items()):
            raise ValueError(f"stages[{index}] mixes dataset, page, or index mode")
        if index and row["users"] <= normalized[index - 1]["users"]:
            raise ValueError("stage user counts must be strictly increasing in run order")

    exclusions = [_exclusions(row) for row in normalized]
    comparisons = []
    candidates = []
    skipped_comparisons = []
    for index in range(1, len(normalized)):
        previous, current = normalized[index - 1:index + 1]
        if exclusions[index - 1] or exclusions[index]:
            skipped_comparisons.append({
                "from_stage": index - 1, "to_stage": index,
                "reason": "ineligible_neighbor",
            })
            continue
        # Zero denominators cannot provide a meaningful relative plateau/rise.
        if previous["success_rps"] == 0 or previous["p95_ms"] == 0:
            skipped_comparisons.append({
                "from_stage": index - 1, "to_stage": index,
                "reason": "zero_baseline_for_relative_comparison",
            })
            continue
        users_ratio = _ratio(current["users"], previous["users"], "users_ratio")
        throughput_ratio = _ratio(current["success_rps"], previous["success_rps"], "success_rps_ratio")
        latency_ratio = _ratio(current["p95_ms"], previous["p95_ms"], "p95_ratio")
        candidate = users_ratio >= 1.5 and throughput_ratio <= 1.10 and latency_ratio >= 1.5
        comparison = {
            "from_stage": index - 1,
            "to_stage": index,
            "from_users": previous["users"],
            "to_users": current["users"],
            "users_ratio": users_ratio,
            "success_rps_ratio": throughput_ratio,
            "p95_ratio": latency_ratio,
            "candidate": candidate,
        }
        comparisons.append(comparison)
        if candidate:
            candidates.append(dict(comparison))

    return {
        "status": "candidate" if candidates else "not_found",
        "capacity_confirmed": False,
        "database_bottleneck_confirmed": False,
        "configuration": config,
        "stage_count": len(normalized),
        "eligible_stage_count": sum(not reasons for reasons in exclusions),
        "excluded_stages": [
            {"stage": index, "users": normalized[index]["users"], "reasons": reasons}
            for index, reasons in enumerate(exclusions) if reasons
        ],
        "comparisons": comparisons,
        "skipped_comparisons": skipped_comparisons,
        "candidates": candidates,
    }
