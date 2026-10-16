"""Offline checks; no Django, Locust, database or HTTP service is imported."""

from copy import deepcopy
import random
import unittest
from uuid import UUID

from loadtests.order_list.core import (
    DATABASE,
    DB_USER,
    PAGE_SIZE,
    created_second,
    page_ordinals,
    public_id,
    username,
    validate_plan,
    validate_response,
    validate_target,
)


def plan():
    return {
        "run_id": "load-a1", "total_orders": 10_000, "accounts": 10,
        "users": [1, 5, 10], "page": 1, "warmup_seconds": 10,
        "measure_seconds": 30, "startup_seconds": 60, "spawn_rate": 2,
        "request_timeout_seconds": 5, "p95_limit_ms": 500,
        "failure_limit": 0.01, "index_mode": "indexed",
    }


def response_fixture(count=45, page=2):
    ids = [public_id("load-a1", 0, value) for value in page_ordinals(count, page)]
    account = {"order_count": count, "expected_ids": ids, "marker": "synthetic-owner-0"}
    response = {
        "count": count, "next": None, "previous": None,
        "results": [
            {
                "public_id": value,
                "items": [{"product_name_snapshot": account["marker"]} for _ in range(2)],
            }
            for value in ids
        ],
    }
    return account, response


class PlanTests(unittest.TestCase):
    def test_valid_plan_returns_an_independent_copy(self):
        original = plan()
        result = validate_plan(original)
        self.assertEqual(result, original)
        self.assertIsNot(result, original)
        result["users"].append(20)
        self.assertEqual(original["users"], [1, 5, 10])
        self.assertEqual((DATABASE, DB_USER, PAGE_SIZE), ("project_one_order_load", "project_one_order_load", 20))

    def test_invalid_bounds_types_and_choices(self):
        cases = {
            "run_id": ["ab", "UPPER", "a_b", "a" * 17, "a00\n", None],
            "total_orders": [99, 3_000_001, True, 100.0],
            "accounts": [0, 1_001, True, 2.0],
            "users": [[], [0], [True], [2, 1], [1, 1], [11], [1.0], (1,)],
            "page": [0, 51, True, 1.0],
            "warmup_seconds": [0, 121, True, 1.5],
            "measure_seconds": [4, 301, True],
            "startup_seconds": [4, 601, True],
            "spawn_rate": [0, -1, 51, True, "2"],
            "request_timeout_seconds": [0, 31, True, 1.5],
            "p95_limit_ms": [0, -1, True, "500"],
            "failure_limit": [-0.01, 1.01, True, "0"],
            "index_mode": ["unindexed", None, True],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    candidate = plan()
                    candidate[field] = value
                    with self.assertRaises(ValueError):
                        validate_plan(candidate)

    def test_nonfinite_numeric_values_are_rejected(self):
        for field in ("spawn_rate", "p95_limit_ms", "failure_limit"):
            for value in (float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    candidate = plan()
                    candidate[field] = value
                    with self.assertRaises(ValueError):
                        validate_plan(candidate)

    def test_distribution_and_users_have_separate_limits(self):
        for changes in (
            {"total_orders": 101, "accounts": 10},
            {"total_orders": 100, "accounts": 10},
            {"total_orders": 10_000, "accounts": 500, "users": [201]},
        ):
            with self.subTest(changes=changes):
                candidate = plan()
                candidate.update(changes)
                with self.assertRaises(ValueError):
                    validate_plan(candidate)
        candidate = plan()
        candidate.update(total_orders=3_000_000, accounts=1_000, users=[1, 200], page=150)
        self.assertEqual(validate_plan(candidate), candidate)

    def test_incomplete_or_nonobject_plan_is_rejected(self):
        for candidate in (None, [], {}, {"run_id": "load-a1"}):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_plan(candidate)


class TargetTests(unittest.TestCase):
    def test_only_exact_dedicated_origins_are_accepted(self):
        for target in ("http://app:8000", "http://127.0.0.1:18080"):
            self.assertEqual(validate_target(target), target)
            self.assertEqual(validate_target(target + "/"), target)

    def test_target_credentials_paths_and_alternate_hosts_are_rejected(self):
        for target in (
            "http://127.0.0.1:8000", "http://localhost:18080", "https://app:8000",
            "http://app:8000/orders", "http://app:8000//", "http://app:8000?x=1",
            "http://app:8000#x", "http://user:password@app:8000", "http://app:8000@evil.example",
            "http://app:8000.evil.example", "http://127.0.0.1:18080/%2e%2e/",
            "http://APP:8000", " http://app:8000", "http://app:8000\n", None,
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_target(target)


class FixtureTests(unittest.TestCase):
    def test_names_and_ids_are_stable_and_account_scoped(self):
        self.assertEqual(username("load-a1", 0), "load_load_a1_0000")
        value = public_id("load-a1", 0, 1)
        self.assertEqual(value, public_id("load-a1", 0, 1))
        self.assertEqual(UUID(value).version, 5)
        self.assertEqual(len({value, public_id("load-a1", 1, 1), public_id("load-a1", 0, 2), public_id("load-a2", 0, 1)}), 4)
        self.assertEqual(created_second(503), 0)
        self.assertEqual(created_second(1_005), 502)

    def test_every_page_matches_independent_full_sort_including_time_ties(self):
        rng = random.Random(17)
        for count in [1, 19, 20, 21, 502, 503, 504, 1_006] + [rng.randint(1, 2_000) for _ in range(5)]:
            expected = sorted(range(count), key=lambda ordinal: (ordinal % 503, ordinal), reverse=True)
            actual = []
            for page in range(1, (count + 19) // 20 + 1):
                with self.subTest(count=count, page=page):
                    result = page_ordinals(count, page)
                    self.assertEqual(result, expected[(page - 1) * 20:page * 20])
                    actual.extend(result)
            self.assertEqual(actual, expected)

    def test_large_dataset_deep_page_has_bounded_output(self):
        count = 3_000_000
        last_page = (count + 19) // 20
        result = page_ordinals(count, last_page)
        self.assertEqual(len(result), 20)
        self.assertEqual(result, list(range(19 * 503, -1, -503)))
        huge = page_ordinals(10**12, 50_000_000_000)
        self.assertEqual(len(huge), 20)
        self.assertEqual(huge[-1], 0)

    def test_invalid_fixture_arguments_are_rejected(self):
        for count, page in ((0, 1), (20, 0), (20, 2), (True, 1), (20, True)):
            with self.subTest(count=count, page=page), self.assertRaises(ValueError):
                page_ordinals(count, page)
        for value in (True, -1, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                created_second(value)
        for index in (True, -1, 1_000):
            with self.subTest(index=index), self.assertRaises(ValueError):
                username("load-a1", index)


class ResponseTests(unittest.TestCase):
    def test_actual_serializer_field_names_and_final_partial_page(self):
        for count, page in ((45, 2), (45, 3), (20, 1)):
            account, response = response_fixture(count, page)
            self.assertIsNone(validate_response(response, account, page))

    def test_wrong_counts_page_shape_order_or_identity_fail(self):
        account, source = response_fixture()
        mutations = (
            lambda body: body.update(count=44),
            lambda body: body.update(count=True),
            lambda body: body.pop("next"),
            lambda body: body.update(results={}),
            lambda body: body["results"].pop(),
            lambda body: body["results"].reverse(),
            lambda body: body["results"][0].update(public_id="other-order"),
            lambda body: body["results"][0].update(id=body["results"][0].pop("public_id")),
            lambda body: body["results"].__setitem__(0, None),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                response = deepcopy(source)
                mutate(response)
                with self.assertRaises(ValueError):
                    validate_response(response, account, 2)

    def test_cross_account_markers_and_invalid_items_fail(self):
        account, source = response_fixture()
        for items in (
            [], [{"product_name_snapshot": account["marker"]}],
            [{"product_name_snapshot": "another-account"}] * 2,
            [{"product_name": account["marker"]}] * 2,
            [None, None], {},
        ):
            with self.subTest(items=items):
                response = deepcopy(source)
                response["results"][0]["items"] = items
                with self.assertRaises(ValueError):
                    validate_response(response, account, 2)

    def test_invalid_expectation_or_nonobject_response_fails(self):
        account, response = response_fixture()
        for changes in ({"order_count": True}, {"expected_ids": []}, {"marker": ""}):
            with self.subTest(changes=changes):
                bad_account = {**account, **changes}
                with self.assertRaises(ValueError):
                    validate_response(response, bad_account, 2)
        with self.assertRaises(ValueError):
            validate_response(None, account, 2)


if __name__ == "__main__":
    unittest.main()
