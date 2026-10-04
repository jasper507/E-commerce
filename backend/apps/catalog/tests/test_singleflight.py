"""Ordered concurrency evidence for process-local, in-flight detail sharing."""

from concurrent.futures import Future
from contextlib import nullcontext
from decimal import Decimal
from queue import Queue
from threading import Event, Lock, Thread, current_thread

import pytest
from django.db import close_old_connections, connection, transaction
from rest_framework.test import APIClient

from apps.catalog import cache
from apps.catalog.cache import SingleFlight, catalog_generation_key, get_catalog_generation
from apps.catalog.views import PublicProductDetailView

from .test_detail_cache import catalog_cache, current_key, get_detail_with_queries


def start_call(call, name):
    outcomes = Queue()

    def execute():
        try:
            outcomes.put((call(), None))
        except BaseException as error:
            outcomes.put((None, error))

    thread = Thread(target=execute, name=name, daemon=True)
    thread.start()
    return thread, outcomes


def outcome(call):
    thread, outcomes = call
    thread.join(timeout=10)
    assert not thread.is_alive(), f"Reader {thread.name} did not finish"
    return outcomes.get_nowait()


def value(call):
    result, error = outcome(call)
    assert error is None, error
    return result


def observe_followers(monkeypatch, count):
    """Observe a follower after it selected an existing Future, without sleeps."""
    joined = Event()
    lock = Lock()
    observed = set()
    original_result = Future.result

    def result(future, *args, **kwargs):
        name = current_thread().name
        if name.startswith("singleflight-follower"):
            with lock:
                observed.add(name)
                if len(observed) == count:
                    joined.set()
        return original_result(future, *args, **kwargs)

    monkeypatch.setattr(Future, "result", result)
    return joined


def test_same_key_shares_only_an_in_progress_load(monkeypatch):
    flights = SingleFlight()
    entered = Event()
    release = Event()
    joined = observe_followers(monkeypatch, 3)
    loads = []
    shared = {"price": "129.00"}

    def load():
        loads.append(True)
        entered.set()
        assert release.wait(timeout=10)
        return shared

    owner = start_call(lambda: flights.run("G1:product", load), "singleflight-owner")
    followers = []
    try:
        assert entered.wait(timeout=10)
        followers = [
            start_call(
                lambda: flights.run("G1:product", load, wait_timeout=5),
                f"singleflight-follower-{number}",
            )
            for number in range(3)
        ]
        assert joined.wait(timeout=10)
    finally:
        release.set()
        owner[0].join(timeout=10)
        for thread, _ in followers:
            thread.join(timeout=10)
    assert value(owner) is shared
    assert all(value(follower) is shared for follower in followers)
    assert len(loads) == 1
    # The registry is not a second permanent cache after completion.
    assert flights.run("G1:product", lambda: "fresh load") == "fresh load"


def test_different_keys_load_independently():
    flights = SingleFlight()
    entered = [Event(), Event()]
    release = Event()

    def load(number):
        entered[number].set()
        assert release.wait(timeout=10)
        return number

    readers = [
        start_call(lambda number=number: flights.run(f"key-{number}", lambda: load(number)),
                   f"singleflight-owner-{number}")
        for number in range(2)
    ]
    try:
        assert all(event.wait(timeout=10) for event in entered)
    finally:
        release.set()
        for thread, _ in readers:
            thread.join(timeout=10)
    assert [value(reader) for reader in readers] == [0, 1]


def test_failed_owner_notifies_followers_and_allows_new_owner(monkeypatch):
    flights = SingleFlight()
    entered = Event()
    release = Event()
    joined = observe_followers(monkeypatch, 2)
    original_error = ValueError("Owner database load failed")
    fallbacks = []

    def failed_load():
        entered.set()
        assert release.wait(timeout=10)
        raise original_error

    def fallback():
        fallbacks.append(True)
        return "independent recovery"

    owner = start_call(lambda: flights.run("key", failed_load), "singleflight-owner")
    followers = []
    try:
        assert entered.wait(timeout=10)
        followers = [
            start_call(lambda: flights.run("key", fallback, wait_timeout=5),
                       f"singleflight-follower-{number}")
            for number in range(2)
        ]
        assert joined.wait(timeout=10)
    finally:
        release.set()
        owner[0].join(timeout=10)
        for thread, _ in followers:
            thread.join(timeout=10)
    result, error = outcome(owner)
    assert result is None
    assert error is original_error
    assert [value(follower) for follower in followers] == ["independent recovery"] * 2
    assert len(fallbacks) == 2
    assert flights.run("key", lambda: "next owner") == "next owner"


@pytest.mark.parametrize("wait_timeout", (0, cache.DETAIL_WAIT_SECONDS))
def test_timed_out_follower_does_not_remove_running_owner(monkeypatch, wait_timeout):
    flights = SingleFlight()
    entered = Event()
    release = Event()
    joined = observe_followers(monkeypatch, 1)
    loads = []

    def load():
        loads.append(True)
        entered.set()
        assert release.wait(timeout=10)
        return "original owner"

    owner = start_call(lambda: flights.run("key", load), "singleflight-owner")
    later = None
    try:
        assert entered.wait(timeout=10)
        # Owner remains blocked until fallback finishes; no scheduling sleep is needed.
        assert flights.run("key", lambda: "timeout fallback", wait_timeout=wait_timeout) == "timeout fallback"
        later = start_call(lambda: flights.run("key", load, wait_timeout=5),
                           "singleflight-follower-later")
        assert joined.wait(timeout=10)
    finally:
        release.set()
        owner[0].join(timeout=10)
        if later is not None:
            later[0].join(timeout=10)
    assert value(owner) == "original owner"
    assert value(later) == "original owner"
    assert len(loads) == 1


def test_new_owner_rechecks_redis_before_reloading(monkeypatch):
    # Protocol-only test: another request filled Redis between the initial miss
    # and this request registering as a new owner. No service availability claim.
    detail_cache = cache.ProductDetailCache.__new__(cache.ProductDetailCache)
    detail_cache.available = True
    detail_cache.key = "G1:product-7"
    payload = {"name": "Already filled by the previous owner"}
    responses = iter((None, payload))
    checked_products = []

    def get(product_id):
        checked_products.append(product_id)
        return next(responses)

    def unexpected_load():
        raise AssertionError("Owner double-check hit must skip database loading")

    def unexpected_set(value):
        raise AssertionError("Owner double-check hit must not rewrite Redis")

    monkeypatch.setattr(detail_cache, "get", get)
    monkeypatch.setattr(detail_cache, "set", unexpected_set)
    monkeypatch.setattr(cache, "detail_flights", SingleFlight())
    assert detail_cache.get_or_load(7, unexpected_load) is payload
    assert checked_products == [7, 7]


def detail_request(url):
    close_old_connections()
    try:
        connection.ensure_connection()
        statements = []

        def record(execute, sql, params, many, context):
            statements.append(sql.lower())
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record):
            response = APIClient().get(url)
            payload = response.json()
        assert response.status_code == 200
        return {
            "payload": payload,
            "sql": len(statements),
            "guard_sql": sum("from `catalog_product`" in sql for sql in statements),
            "reload_sql": sum("from `catalog_sku`" in sql for sql in statements),
        }
    finally:
        connection.close()


def allow_ordered_wait(monkeypatch):
    original_run = SingleFlight.run

    def longer_wait(flights, key, load, wait_timeout=0.2):
        # This ordered correctness experiment is not a measurement of the 200 ms default.
        return original_run(flights, key, load, wait_timeout=5)

    monkeypatch.setattr(SingleFlight, "run", longer_wait)


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
def test_same_generation_api_readers_share_one_sku_load(catalog_cache, monkeypatch):
    data = catalog_cache
    entered = Event()
    release = Event()
    joined = observe_followers(monkeypatch, 3)
    allow_ordered_wait(monkeypatch)
    original_load = PublicProductDetailView.load_detail
    loads = []

    def paused_load(view, product):
        loads.append(True)
        entered.set()
        assert release.wait(timeout=10)
        return original_load(view, product)

    monkeypatch.setattr(PublicProductDetailView, "load_detail", paused_load)
    owner = start_call(lambda: detail_request(data.url), "singleflight-owner")
    followers = []
    try:
        assert entered.wait(timeout=10)
        followers = [
            start_call(lambda: detail_request(data.url), f"singleflight-follower-{number}")
            for number in range(3)
        ]
        assert joined.wait(timeout=10)
    finally:
        release.set()
        owner[0].join(timeout=10)
        for thread, _ in followers:
            thread.join(timeout=10)
    results = [value(owner)] + [value(follower) for follower in followers]
    expected = {
        "slug": "mouse-m1", "name": "Mouse M1", "description": "A test mouse",
        "category": {"slug": "mice", "name": "Mice"}, "in_stock": True,
        "skus": [{"code": "M1-BLACK", "color": data.sku.color,
                  "attributes": {"connection": "bluetooth"}, "price": "129.00",
                  "in_stock": True}],
    }
    assert all(result["payload"] == expected for result in results)
    assert all(result["guard_sql"] == 1 for result in results)
    assert sum(result["reload_sql"] for result in results) == 1
    assert sum(result["sql"] for result in results) == 5
    assert len(loads) == 1
    assert get_detail_with_queries(data) == (expected, 1)
    print("singleflight_ordered_api requests=4 guard_sql=4 reload_sql=1 total_sql=5")


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
def test_new_generation_does_not_join_old_generation_owner(catalog_cache, monkeypatch):
    data = catalog_cache
    loaded_old_payload = Event()
    release = Event()
    original_load = PublicProductDetailView.load_detail
    original_result = Future.result
    new_request_thread = current_thread().name

    def prohibit_new_generation_wait(future, *args, **kwargs):
        if current_thread().name == new_request_thread:
            raise AssertionError("New generation must not wait on an old flight")
        return original_result(future, *args, **kwargs)

    def pause_old_generation(view, product):
        payload = original_load(view, product)
        if current_thread().name == "singleflight-old-generation":
            loaded_old_payload.set()
            assert release.wait(timeout=10)
        return payload

    monkeypatch.setattr(PublicProductDetailView, "load_detail", pause_old_generation)
    monkeypatch.setattr(Future, "result", prohibit_new_generation_wait)
    old = start_call(lambda: detail_request(data.url), "singleflight-old-generation")
    try:
        assert loaded_old_payload.wait(timeout=10)
        data.sku.price = Decimal("169.00")
        data.sku.save(update_fields=("price",))
        assert get_catalog_generation() != data.generation
        # This must finish while the old owner is still deliberately paused.
        new_payload, queries = get_detail_with_queries(data)
        assert not release.is_set()
        assert old[0].is_alive()
        assert new_payload["skus"][0]["price"] == "169.00"
        assert queries == 2
        new_key = current_key(data.product)
        assert new_key != data.key
        assert data.control.get(new_key) is not None
    finally:
        release.set()
        old[0].join(timeout=10)
    old_result = value(old)
    assert old_result["payload"]["skus"][0]["price"] == "129.00"
    assert old_result["guard_sql"] == old_result["reload_sql"] == 1
    assert data.control.get(data.key) is not None
    assert get_detail_with_queries(data) == (new_payload, 1)


@pytest.mark.redis_integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ("disabled", "atomic", "unavailable"))
def test_bypassed_cache_never_registers_shared_load(catalog_cache, settings, monkeypatch, mode):
    def unexpected_share(*args, **kwargs):
        raise AssertionError("Bypassed cache must not share transaction-local or unavailable data")

    monkeypatch.setattr(cache.detail_flights, "run", unexpected_share)
    if mode == "disabled":
        settings.CATALOG_CACHE_ENABLED = False
    elif mode == "unavailable":
        catalog_cache.control.set(catalog_generation_key(), "invalid-generation")
    with transaction.atomic() if mode == "atomic" else nullcontext():
        payload, queries = get_detail_with_queries(catalog_cache)
    assert payload["skus"][0]["price"] == "129.00"
    assert queries == 2
