"""No Django, database, HTTP, or service startup is needed for these tests."""

from copy import deepcopy
import json
import math
import unittest

from loadtests.order_list.metrics import analyze_stages, summarize_samples


def sample(elapsed_ms=10, *, ok=True, status=200, finished_in_window=True):
    return dict(elapsed_ms=elapsed_ms, ok=ok, status=status, finished_in_window=finished_in_window)


def stage(users=10, **changes):
    row = dict(
        users=users, total_orders=1_000_000, page=1, index_mode="indexed",
        status="complete", valid_for_capacity=True, resource_samples=10,
        client_cpu_saturated=False, p95_ms=100, success_rps=100, sample_count=1000,
    )
    row.update(changes)
    return row


class SampleSummaryTests(unittest.TestCase):
    def test_late_slow_failures_remain_in_latency_and_failure_cohort(self):
        rows = [sample(10), sample(20), sample(1000, ok=False, status=0, finished_in_window=False)]
        result = summarize_samples(rows, 2)
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["success_count"], 2)
        self.assertEqual(result["failure_count"], 1)
        self.assertAlmostEqual(result["failure_rate"], 1 / 3)
        self.assertEqual(result["p95_ms"], 1000)
        self.assertEqual(result["completed_rps"], 1)
        self.assertEqual(result["success_rps"], 1)
        self.assertEqual(result["late_completions"], 1)
        self.assertEqual(result["status_counts"], {"0": 1, "200": 2})

    def test_success_throughput_excludes_late_success_and_in_window_failure(self):
        rows = [sample(10), sample(30, ok=False, status=500), sample(60, finished_in_window=False)]
        result = summarize_samples(rows, 4)
        self.assertEqual(result["completed_rps"], 0.5)
        self.assertEqual(result["success_rps"], 0.25)
        self.assertEqual(result["success_count"], 2)

    def test_nearest_rank_is_computed_from_unsorted_raw_samples(self):
        rows = [sample(value) for value in reversed(range(1, 101))]
        original = deepcopy(rows)
        result = summarize_samples(rows, 10)
        self.assertEqual((result["p50_ms"], result["p95_ms"], result["p99_ms"]), (50, 95, 99))
        self.assertEqual(rows, original)

    def test_empty_samples_have_undefined_rate_and_percentiles(self):
        result = summarize_samples([], 1)
        self.assertIsNone(result["failure_rate"])
        self.assertIsNone(result["p50_ms"])
        self.assertIsNone(result["p95_ms"])
        self.assertIsNone(result["p99_ms"])
        self.assertEqual(result["completed_rps"], 0)
        self.assertEqual(result["success_rps"], 0)
        self.assertEqual(result["status_counts"], {})
        json.dumps(result, allow_nan=False)

    def test_zero_elapsed_time_is_valid(self):
        self.assertEqual(summarize_samples([sample(0)], 1)["p99_ms"], 0)

    def test_business_failure_is_not_inferred_from_http_success(self):
        result = summarize_samples([sample(ok=False, status=200)], 1)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["success_rps"], 0)

    def test_invalid_duration_is_rejected(self):
        for value in (0, -1, True, "1", math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                summarize_samples([], value)

    def test_overflowed_throughput_is_rejected_instead_of_emitting_infinity(self):
        with self.assertRaises(ValueError):
            summarize_samples([sample()], 5e-324)

    def test_bad_sample_fields_are_rejected(self):
        for field, value in (
            ("elapsed_ms", -1), ("elapsed_ms", math.nan), ("elapsed_ms", math.inf),
            ("elapsed_ms", True), ("ok", 1), ("finished_in_window", "false"),
            ("status", True), ("status", -1), ("status", 200.0),
        ):
            row = sample()
            row[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                summarize_samples([row], 1)
        for field in sample():
            row = sample()
            del row[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                summarize_samples([row], 1)
        for rows in (None, {}, "samples", [None]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                summarize_samples(rows, 1)


class StageAnalysisTests(unittest.TestCase):
    def test_exact_inclusive_thresholds_produce_only_a_candidate(self):
        rows = [stage(10), stage(15, success_rps=110, p95_ms=150)]
        original = deepcopy(rows)
        result = analyze_stages(rows)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertFalse(result["capacity_confirmed"])
        self.assertFalse(result["database_bottleneck_confirmed"])
        self.assertEqual(rows, original)
        json.dumps(result, allow_nan=False)

    def test_all_three_thresholds_are_required(self):
        for users, rps, p95 in ((14, 100, 150), (15, 110.01, 150), (15, 110, 149.99)):
            with self.subTest(users=users, rps=rps, p95=p95):
                result = analyze_stages([stage(), stage(users, success_rps=rps, p95_ms=p95)])
                self.assertEqual(result["status"], "not_found")
                self.assertEqual(result["candidates"], [])

    def test_invalid_middle_stage_must_not_be_bridged(self):
        for change, reason in (
            ({"valid_for_capacity": False}, "stage_marked_invalid"),
            ({"status": "aborted"}, "stage_not_complete"),
            ({"resource_samples": 1}, "insufficient_resource_samples"),
            ({"client_cpu_saturated": True}, "client_cpu_saturated"),
        ):
            with self.subTest(change=change):
                result = analyze_stages([
                    stage(10), stage(20, **change), stage(40, success_rps=101, p95_ms=500),
                ])
                self.assertEqual(result["comparisons"], [])
                self.assertEqual(result["status"], "not_found")
                self.assertIn(reason, result["excluded_stages"][0]["reasons"])

    def test_valid_neighbors_after_a_gap_can_be_compared(self):
        result = analyze_stages([
            stage(10, status="aborted"), stage(20), stage(40, success_rps=102, p95_ms=200),
        ])
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidates"][0]["from_stage"], 1)
        self.assertEqual(result["candidates"][0]["to_stage"], 2)

    def test_empty_cohort_is_not_capacity_evidence(self):
        result = analyze_stages([stage(sample_count=0, success_rps=0, p95_ms=None)])
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["eligible_stage_count"], 0)
        self.assertIn("no_request_samples", result["excluded_stages"][0]["reasons"])

    def test_zero_baselines_are_not_used_for_relative_comparison(self):
        for change in ({"success_rps": 0}, {"p95_ms": 0}):
            with self.subTest(change=change):
                result = analyze_stages([stage(**change), stage(20, p95_ms=200)])
                self.assertEqual(result["status"], "not_found")
                self.assertEqual(result["comparisons"], [])
                self.assertEqual(
                    result["skipped_comparisons"][0]["reason"],
                    "zero_baseline_for_relative_comparison",
                )

    def test_overflowed_relative_metrics_are_rejected(self):
        with self.assertRaises(ValueError):
            analyze_stages([stage(success_rps=5e-324), stage(20, success_rps=100)])
        with self.assertRaises(ValueError):
            analyze_stages([stage(), stage(10 ** 1000)])

    def test_mixed_configuration_is_rejected(self):
        for change in ({"total_orders": 10_000}, {"page": 201}, {"index_mode": "baseline"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                analyze_stages([stage(), stage(20, **change)])

    def test_load_order_must_increase_without_sorting_the_input(self):
        for users in ((10, 10), (20, 10)):
            with self.subTest(users=users), self.assertRaises(ValueError):
                analyze_stages([stage(value) for value in users])

    def test_missing_or_malformed_evidence_is_rejected(self):
        for field in stage():
            row = stage()
            del row[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                analyze_stages([row])
        for field, value in (
            ("users", True), ("total_orders", 0), ("page", -1),
            ("resource_samples", 2.5), ("sample_count", -1),
            ("valid_for_capacity", 1), ("client_cpu_saturated", "false"),
            ("success_rps", math.nan), ("success_rps", math.inf),
            ("p95_ms", -1), ("p95_ms", None), ("index_mode", ""), ("status", " complete "),
        ):
            row = stage()
            row[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                analyze_stages([row])
        with self.assertRaises(ValueError):
            analyze_stages([stage(sample_count=0, p95_ms=None, success_rps=1)])
        for rows in ([], None, {}, "stages", [None]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                analyze_stages(rows)


if __name__ == "__main__":
    unittest.main()
