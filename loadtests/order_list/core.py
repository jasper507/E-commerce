"""Pure validation and deterministic fixtures for the order-list load tool."""

from collections.abc import Mapping
from copy import deepcopy
import math
import re
from uuid import UUID, uuid5


DATABASE = "project_one_order_load"
DB_USER = DATABASE
PAGE_SIZE = 20

_RUN_ID = re.compile(r"[a-z][a-z0-9-]{2,15}")
_NAMESPACE = UUID("4cc1ab28-b46e-5750-8a6b-f92dcc98d31c")
_TARGETS = {"http://app:8000", "http://127.0.0.1:18080"}


def _integer(value, name, minimum, maximum=None):
    if type(value) is not int or value < minimum or (
        maximum is not None and value > maximum
    ):
        raise ValueError(f"{name} must be an integer in the permitted range.")
    return value


def _number(value, name, minimum, maximum=None, *, exclusive_minimum=False):
    if type(value) not in (int, float) or (
        type(value) is float and not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number.")
    if value < minimum or (exclusive_minimum and value == minimum) or (
        maximum is not None and value > maximum
    ):
        raise ValueError(f"{name} is outside the permitted range.")
    return value


def _run_id(value):
    if not isinstance(value, str) or _RUN_ID.fullmatch(value) is None:
        raise ValueError("run_id must match [a-z][a-z0-9-]{2,15}.")
    return value


def validate_plan(plan):
    """Validate a bounded run description without accessing its target."""
    if not isinstance(plan, Mapping):
        raise ValueError("The load plan must be an object.")
    required = (
        "run_id", "total_orders", "accounts", "users", "page",
        "warmup_seconds", "measure_seconds", "startup_seconds", "spawn_rate",
        "request_timeout_seconds", "p95_limit_ms", "failure_limit", "index_mode",
    )
    missing = [name for name in required if name not in plan]
    if missing:
        raise ValueError("Missing plan fields: " + ", ".join(missing))

    _run_id(plan["run_id"])
    total = _integer(plan["total_orders"], "total_orders", 100, 3_000_000)
    accounts = _integer(plan["accounts"], "accounts", 1, 1_000)
    if total % accounts:
        raise ValueError("total_orders must be divisible by accounts.")
    per_account = total // accounts
    if per_account < PAGE_SIZE:
        raise ValueError("Each account must own at least 20 orders.")

    users = plan["users"]
    if not isinstance(users, list) or not users:
        raise ValueError("users must be a nonempty increasing list.")
    previous = 0
    for users_count in users:
        _integer(users_count, "users entry", 1, min(accounts, 200))
        if users_count <= previous:
            raise ValueError("users must be strictly increasing.")
        previous = users_count

    _integer(plan["page"], "page", 1, (per_account + PAGE_SIZE - 1) // PAGE_SIZE)
    _integer(plan["warmup_seconds"], "warmup_seconds", 1, 120)
    _integer(plan["measure_seconds"], "measure_seconds", 5, 300)
    _integer(plan["startup_seconds"], "startup_seconds", 5, 600)
    _number(plan["spawn_rate"], "spawn_rate", 0, 50, exclusive_minimum=True)
    _integer(plan["request_timeout_seconds"], "request_timeout_seconds", 1, 30)
    _number(plan["p95_limit_ms"], "p95_limit_ms", 0, exclusive_minimum=True)
    _number(plan["failure_limit"], "failure_limit", 0, 1)
    if plan["index_mode"] not in ("indexed", "without_read_index"):
        raise ValueError("index_mode must be indexed or without_read_index.")
    return deepcopy(dict(plan))


def username(run_id, account_index):
    _run_id(run_id)
    _integer(account_index, "account_index", 0, 999)
    return f"load_{run_id.replace('-', '_')}_{account_index:04d}"


def public_id(run_id, account_index, ordinal):
    _run_id(run_id)
    _integer(account_index, "account_index", 0, 999)
    _integer(ordinal, "ordinal", 0)
    return str(uuid5(_NAMESPACE, f"{run_id}:{account_index}:{ordinal}"))


def created_second(ordinal):
    return _integer(ordinal, "ordinal", 0) % 503


def page_ordinals(order_count, page):
    """Find one page in (ordinal % 503 DESC, ordinal DESC) order.

    Skip whole timestamp buckets so even a deep page needs at most 503 bucket
    checks and 20 output entries, without sorting the complete dataset.
    """
    _integer(order_count, "order_count", 1)
    _integer(page, "page", 1, (order_count + PAGE_SIZE - 1) // PAGE_SIZE)
    offset = (page - 1) * PAGE_SIZE
    result = []
    for second in range(min(502, order_count - 1), -1, -1):
        bucket_size = (order_count - 1 - second) // 503 + 1
        if offset >= bucket_size:
            offset -= bucket_size
            continue
        ordinal = second + (bucket_size - 1 - offset) * 503
        offset = 0
        while ordinal >= second and len(result) < PAGE_SIZE:
            result.append(ordinal)
            ordinal -= 503
        if len(result) == PAGE_SIZE:
            break
    return result


def validate_target(url):
    """Return a permitted origin, rejecting credentials and non-root paths."""
    if not isinstance(url, str):
        raise ValueError("The target must be a permitted HTTP origin.")
    origin = url[:-1] if url.endswith("/") else url
    if origin not in _TARGETS:
        raise ValueError("Only the dedicated app:8000 or loopback:18080 target is permitted.")
    return origin


def validate_response(payload, account, page):
    """Reject wrong pages or another account's synthetic snapshots."""
    if not isinstance(payload, Mapping) or not isinstance(account, Mapping):
        raise ValueError("The response and account must be objects.")
    count = _integer(account.get("order_count"), "account order_count", 1)
    _integer(page, "page", 1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    wanted_length = min(PAGE_SIZE, count - (page - 1) * PAGE_SIZE)
    expected = account.get("expected_ids")
    marker = account.get("marker")
    if (
        not isinstance(expected, list) or len(expected) != wanted_length
        or any(not isinstance(value, str) or not value for value in expected)
        or len(set(expected)) != len(expected)
        or not isinstance(marker, str) or not marker
    ):
        raise ValueError("The account's expected page or marker is invalid.")
    if type(payload.get("count")) is not int or payload["count"] != count:
        raise ValueError("The response count differs from the account dataset.")
    if "next" not in payload or "previous" not in payload:
        raise ValueError("The response must include pagination links.")
    rows = payload.get("results")
    if not isinstance(rows, list) or len(rows) != wanted_length:
        raise ValueError("The response page has an unexpected number of orders.")
    ids = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Each order must be an object.")
        ids.append(row.get("public_id"))
        items = row.get("items")
        if not isinstance(items, list) or len(items) != 2:
            raise ValueError("Each synthetic order must contain two item snapshots.")
        if any(
            not isinstance(item, Mapping) or item.get("product_name_snapshot") != marker
            for item in items
        ):
            raise ValueError("The response contains an unexpected account marker.")
    if ids != expected:
        raise ValueError("The response order identities or order differ from the expected page.")
