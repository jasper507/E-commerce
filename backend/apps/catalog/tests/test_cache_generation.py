"""Additional real-service evidence for global catalog generation namespaces."""

import time
from decimal import Decimal
from queue import Queue
from threading import Barrier, Event, Thread

import pytest
from django.db import close_old_connections
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalog.cache import (
    ProductDetailCache,
    catalog_generation_key,
    get_catalog_generation,
    get_redis_client,
    product_detail_key,
)
from apps.catalog.models import Product
from apps.catalog.views import PublicProductDetailView

from .test_detail_cache import catalog_cache, current_key, get_detail_with_queries


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]


def test_delete_only_baseline_can_refill_old_value_after_database_commit(catalog_cache):
    """Ordered protocol counterexample using real storage, not a full API race."""
    data = catalog_cache
    baseline_key = f"{catalog_generation_key()}:delete-only-baseline"
    assert data.control.get(baseline_key) is None
    # Reader A misses and obtains an old MySQL value, then pauses before refill.
    old_value = Product.objects.values_list("name", flat=True).get(pk=data.product.pk)
    # Writer B commits the new value and applies the old delete-only protocol.
    data.product.name = "Database value after delete-only invalidation"
    data.product.save(update_fields=("name",))
    data.control.delete(baseline_key)
    # Reader A's late write recreates the stale cache after that deletion.
    data.control.set(baseline_key, old_value, ex=30)
    cached_value = data.control.get(baseline_key).decode("utf-8")
    committed_value = Product.objects.values_list("name", flat=True).get(pk=data.product.pk)
    assert cached_value == "Mouse M1"
    assert committed_value == "Database value after delete-only invalidation"
    assert cached_value != committed_value
    print("delete_only_protocol_counterexample stale_refill=True (ordered storage experiment)")


def start_detail_reader(url):
    results = Queue()

    def read():
        close_old_connections()
        try:
            response = APIClient().get(url)
            results.put((response.status_code, response.json(), None))
        except Exception as exc:
            results.put((None, None, exc))
        finally:
            close_old_connections()

    thread = Thread(target=read)
    thread.start()
    return thread, results


def assert_reader_payload(thread, results):
    assert not thread.is_alive()
    status_code, payload, error = results.get_nowait()
    assert error is None
    assert status_code == 200
    return payload


def test_generation_captured_before_loaded_product_can_become_stale(
    catalog_cache, monkeypatch
):
    data = catalog_cache
    product_loaded = Event()
    release_reader = Event()
    original_get_object = PublicProductDetailView.get_object

    def pause_after_product_sql(view):
        product = original_get_object(view)
        product_loaded.set()
        assert release_reader.wait(timeout=10), "Writer did not release old reader"
        return product

    monkeypatch.setattr(PublicProductDetailView, "get_object", pause_after_product_sql)
    reader, results = start_detail_reader(data.url)
    try:
        assert product_loaded.wait(timeout=10)
        data.product.name = "Name committed after the first SQL"
        data.product.save(update_fields=("name",))
        new_generation = get_catalog_generation()
        assert new_generation != data.generation
    finally:
        release_reader.set()
        reader.join(timeout=10)
        monkeypatch.setattr(PublicProductDetailView, "get_object", original_get_object)
    old_payload = assert_reader_payload(reader, results)
    assert old_payload["name"] == "Mouse M1"
    assert data.control.exists(data.key)
    assert not data.control.exists(current_key(data.product))
    fresh_payload, queries = get_detail_with_queries(data)
    assert fresh_payload["name"] == "Name committed after the first SQL"
    assert queries == 2
    assert get_detail_with_queries(data) == (fresh_payload, 1)


def test_lost_generation_does_not_reuse_namespace_of_late_old_fill(
    catalog_cache, monkeypatch
):
    data = catalog_cache
    payload_ready = Event()
    release_reader = Event()
    original_set = ProductDetailCache.set

    def pause_old_set(cache, payload):
        payload_ready.set()
        assert release_reader.wait(timeout=10), "Writer did not release old refill"
        return original_set(cache, payload)

    monkeypatch.setattr(ProductDetailCache, "set", pause_old_set)
    reader, results = start_detail_reader(data.url)
    try:
        assert payload_ready.wait(timeout=10)
        data.product.name = "Name after generation recovery"
        data.product.save(update_fields=("name",))
        committed_generation = get_catalog_generation()
        assert committed_generation != data.generation
        # Delete only this fixture's marker, simulating eviction of generation state.
        assert data.control.delete(catalog_generation_key()) == 1
        recovered_generation = get_catalog_generation()
        assert recovered_generation not in (data.generation, committed_generation)
    finally:
        release_reader.set()
        reader.join(timeout=10)
        monkeypatch.setattr(ProductDetailCache, "set", original_set)
    assert assert_reader_payload(reader, results)["name"] == "Mouse M1"
    assert data.control.exists(data.key)
    assert not data.control.exists(product_detail_key(data.product.pk, recovered_generation))
    payload, queries = get_detail_with_queries(data)
    assert payload["name"] == "Name after generation recovery"
    assert queries == 2


def test_concurrent_generation_bootstrap_uses_actual_setnx_winner(
    catalog_cache, monkeypatch
):
    data = catalog_cache
    marker = catalog_generation_key()
    assert data.control.delete(marker) == 1
    redis_client = get_redis_client()
    original_get = redis_client.get
    original_set = redis_client.set
    both_read_missing = Barrier(2)
    proposed_generations = []
    results = Queue()

    def synchronized_get(key, *args, **kwargs):
        value = original_get(key, *args, **kwargs)
        if key == marker and value is None:
            both_read_missing.wait(timeout=10)
        return value

    def observe_setnx(key, value, *args, **kwargs):
        if key == marker and kwargs.get("nx"):
            proposed_generations.append(value)
        return original_set(key, value, *args, **kwargs)

    def bootstrap():
        close_old_connections()
        try:
            results.put((get_catalog_generation(), None))
        except Exception as exc:
            results.put((None, exc))
        finally:
            close_old_connections()

    monkeypatch.setattr(redis_client, "get", synchronized_get)
    monkeypatch.setattr(redis_client, "set", observe_setnx)
    threads = [Thread(target=bootstrap), Thread(target=bootstrap)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
    finally:
        monkeypatch.setattr(redis_client, "get", original_get)
        monkeypatch.setattr(redis_client, "set", original_set)
    assert all(not thread.is_alive() for thread in threads)
    outcomes = [results.get_nowait(), results.get_nowait()]
    assert all(error is None for _, error in outcomes)
    assert len(proposed_generations) == 2
    assert len(set(proposed_generations)) == 2
    winner = data.control.get(marker).decode("ascii")
    assert winner in proposed_generations
    assert all(generation == winner for generation, _ in outcomes)
    assert data.control.ttl(marker) == -1


def test_global_rotation_makes_unrelated_product_cold(catalog_cache):
    data = catalog_cache
    other = Product.objects.create(
        category=data.category, name="Unrelated mouse", slug="unrelated-mouse", is_active=True
    )
    other_url = reverse("catalog:product-detail", kwargs={"slug": other.slug})
    initial_payload, _ = get_detail_with_queries(data)
    assert data.client.get(other_url).status_code == 200
    old_generation = get_catalog_generation()
    old_first_key = product_detail_key(data.product.pk, old_generation)
    old_other_key = product_detail_key(other.pk, old_generation)
    assert data.control.exists(old_first_key)
    assert data.control.exists(old_other_key)
    other.name = "Changed unrelated mouse"
    other.save(update_fields=("name",))
    assert get_catalog_generation() != old_generation
    assert data.control.exists(old_first_key)
    assert data.control.exists(old_other_key)
    unchanged_payload, cold_queries = get_detail_with_queries(data)
    assert unchanged_payload == initial_payload
    assert cold_queries == 2
    assert get_detail_with_queries(data) == (unchanged_payload, 1)
    assert data.client.get(other_url).json()["name"] == "Changed unrelated mouse"


def test_old_details_expire_naturally_but_generation_marker_persists(
    catalog_cache, settings
):
    data = catalog_cache
    settings.CATALOG_CACHE_TTL = 1
    settings.CATALOG_CACHE_TTL_JITTER = 0
    get_detail_with_queries(data)
    old_key = current_key(data.product)
    data.product.name = "Changed before old cache expiry"
    data.product.save(update_fields=("name",))
    new_generation = get_catalog_generation()
    assert new_generation != data.generation
    assert data.control.exists(old_key)
    assert data.control.ttl(catalog_generation_key()) == -1
    deadline = time.monotonic() + 3
    while data.control.exists(old_key) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not data.control.exists(old_key)
    assert get_catalog_generation() == new_generation
    assert data.control.ttl(catalog_generation_key()) == -1


def test_sku_price_save_rotates_generation_and_refreshes_response(catalog_cache):
    data = catalog_cache
    old_payload, _ = get_detail_with_queries(data)
    assert old_payload["skus"][0]["price"] == "129.00"
    data.sku.price = Decimal("169.00")
    data.sku.save(update_fields=("price",))
    assert get_catalog_generation() != data.generation
    assert data.control.exists(data.key)
    payload, queries = get_detail_with_queries(data)
    assert payload["skus"][0]["price"] == "169.00"
    assert queries == 2
    assert get_detail_with_queries(data) == (payload, 1)


def test_invalid_generation_bypasses_cache_until_normal_save_repairs_marker(catalog_cache):
    data = catalog_cache
    get_detail_with_queries(data)
    old_bytes = data.control.get(data.key)
    marker = catalog_generation_key()
    data.control.set(marker, "not-a-32-character-hex-generation")
    assert get_catalog_generation() is None
    payload, queries = get_detail_with_queries(data)
    assert payload["name"] == "Mouse M1"
    assert queries == 2
    assert data.control.get(data.key) == old_bytes
    assert get_catalog_generation() is None
    data.product.name = "Normal save repairs invalid marker"
    data.product.save(update_fields=("name",))
    repaired_generation = get_catalog_generation()
    assert repaired_generation is not None
    assert repaired_generation != data.generation
    payload, queries = get_detail_with_queries(data)
    assert payload["name"] == "Normal save repairs invalid marker"
    assert queries == 2
    assert get_detail_with_queries(data) == (payload, 1)
