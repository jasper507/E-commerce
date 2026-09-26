"""Real Redis evidence; a unique key prefix confines cleanup to this test.

The stale-refill test deliberately records the first slice's consistency limit.
It is not a claim that commit-time deletion makes cache reads strongly consistent.
"""

import os
import socket
import time
from decimal import Decimal
from queue import Queue
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
import redis
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Address
from apps.catalog.cache import (
    ProductDetailCache,
    get_redis_client,
    product_detail_key,
)
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory
from apps.ordering.models import Cart, CartItem, Order
from apps.ordering.services import OrderRuleError, create_order_from_cart


pytestmark = [pytest.mark.redis_integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def catalog_cache(settings):
    url = os.getenv("PROJECT_ONE_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set PROJECT_ONE_TEST_REDIS_URL to an isolated real Redis service.")
    assert connection.vendor == "mysql", "Cache evidence requires real MySQL."
    assert connection.settings_dict["NAME"].startswith("test_"), "Refuse a non-test database."
    control = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    # An explicitly configured but unreachable service is a failure, not a skip.
    control.ping()
    prefix = f"project-one:test:detail:{uuid4().hex}"
    settings.CATALOG_CACHE_ENABLED = True
    settings.CATALOG_CACHE_REDIS_URL = url
    settings.CATALOG_CACHE_KEY_PREFIX = prefix
    settings.CATALOG_CACHE_TTL = 30
    settings.CATALOG_CACHE_TTL_JITTER = 5
    get_redis_client.cache_clear()
    client = get_redis_client()
    try:
        category = Category.objects.create(name="Mice", slug="mice", is_active=True)
        product = Product.objects.create(
            category=category,
            name="Mouse M1",
            slug="mouse-m1",
            description="A test mouse",
            is_active=True,
        )
        sku = SKU.objects.create(
            product=product,
            code="M1-BLACK",
            price=Decimal("129.00"),
            attributes={"connection": "bluetooth"},
            is_active=True,
        )
        inventory = Inventory.objects.create(sku=sku, available_stock=10)
        yield SimpleNamespace(
            category=category,
            product=product,
            sku=sku,
            inventory=inventory,
            control=control,
            client=APIClient(),
            url=reverse("catalog:product-detail", kwargs={"slug": product.slug}),
            key=product_detail_key(product.pk),
            redis_url=url,
        )
    finally:
        # Never use FLUSHDB; scan/delete only the unguessable prefix owned above.
        keys = list(control.scan_iter(match=f"{prefix}:*"))
        if keys:
            control.delete(*keys)
        client.connection_pool.disconnect()
        get_redis_client().connection_pool.disconnect()
        get_redis_client.cache_clear()
        control.connection_pool.disconnect()


def get_detail_with_queries(data):
    with CaptureQueriesContext(connection) as queries:
        response = data.client.get(data.url)
    assert response.status_code == 200
    return response.json(), len(queries)


def checkout_fixture(data):
    user = get_user_model().objects.create_user(username="cache_customer")
    address = Address.objects.create(
        user=user,
        recipient_name="测试用户",
        phone="13800138000",
        province="广东省",
        city="深圳市",
        district="南山区",
        detail="测试路 1 号",
    )
    cart = Cart.objects.create(user=user)
    CartItem.objects.create(cart=cart, sku=data.sku, quantity=1)
    return user, address


def test_cold_miss_then_hit_preserves_response_and_reduces_sql(catalog_cache):
    data = catalog_cache
    assert data.control.get(data.key) is None
    started = time.perf_counter()
    cold_payload, cold_queries = get_detail_with_queries(data)
    cold_ms = (time.perf_counter() - started) * 1000
    assert data.control.get(data.key) is not None
    started = time.perf_counter()
    warm_payload, warm_queries = get_detail_with_queries(data)
    warm_ms = (time.perf_counter() - started) * 1000
    print(
        f"detail_cache_sample miss_sql={cold_queries} hit_sql={warm_queries} "
        f"miss_ms={cold_ms:.3f} hit_ms={warm_ms:.3f} "
        "(local single sample; not a performance benchmark)"
    )
    assert cold_payload == warm_payload
    assert cold_payload["skus"][0]["price"] == "129.00"
    assert cold_queries == 2
    assert warm_queries == 1
    assert 25 <= data.control.ttl(data.key) <= 35


def test_disabled_cache_retains_two_query_baseline(catalog_cache, settings):
    settings.CATALOG_CACHE_ENABLED = False
    _, queries = get_detail_with_queries(catalog_cache)
    assert queries == 2
    assert catalog_cache.control.get(catalog_cache.key) is None


def test_real_redis_expiration_returns_to_mysql(catalog_cache, settings):
    data = catalog_cache
    settings.CATALOG_CACHE_TTL = 1
    settings.CATALOG_CACHE_TTL_JITTER = 0
    old_payload, _ = get_detail_with_queries(data)
    Product.objects.filter(pk=data.product.pk).update(name="Renamed without signal")
    assert 0 < data.control.pttl(data.key) <= 1000
    deadline = time.monotonic() + 3
    while data.control.exists(data.key) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not data.control.exists(data.key)
    payload, queries = get_detail_with_queries(data)
    assert payload["name"] != old_payload["name"]
    assert payload["name"] == "Renamed without signal"
    assert queries == 2


@pytest.mark.parametrize("cached_value", [b"{broken-json", b"[]", b"null", b'{}'])
def test_invalid_cache_payload_returns_to_mysql(catalog_cache, cached_value):
    data = catalog_cache
    data.control.set(data.key, cached_value, ex=30)
    payload, queries = get_detail_with_queries(data)
    assert payload["name"] == data.product.name
    assert queries == 2
    refreshed_payload, refreshed_queries = get_detail_with_queries(data)
    assert refreshed_payload == payload
    assert refreshed_queries == 1


@pytest.mark.parametrize("target", ["product", "category"])
def test_bulk_unpublish_still_hides_warm_detail(catalog_cache, target):
    data = catalog_cache
    get_detail_with_queries(data)
    instance = getattr(data, target)
    type(instance).objects.filter(pk=instance.pk).update(is_active=False)
    assert data.control.exists(data.key)
    response = data.client.get(data.url)
    assert response.status_code == 404


def test_save_invalidates_only_after_commit(catalog_cache):
    data = catalog_cache
    get_detail_with_queries(data)
    with transaction.atomic():
        data.product.name = "Changed in transaction"
        data.product.save(update_fields=("name",))
        assert data.control.exists(data.key)
    assert not data.control.exists(data.key)
    payload, queries = get_detail_with_queries(data)
    assert payload["name"] == "Changed in transaction"
    assert queries == 2


def test_rollback_keeps_existing_cache(catalog_cache):
    data = catalog_cache
    old_payload, _ = get_detail_with_queries(data)
    with transaction.atomic():
        data.product.name = "Rolled back"
        data.product.save(update_fields=("name",))
        transaction.set_rollback(True)
    assert data.control.exists(data.key)
    payload, queries = get_detail_with_queries(data)
    assert payload == old_payload
    assert queries == 1


def test_category_rename_refreshes_all_related_products(catalog_cache):
    data = catalog_cache
    other = Product.objects.create(
        category=data.category, name="Mouse M2", slug="mouse-m2", is_active=True
    )
    other_url = reverse("catalog:product-detail", kwargs={"slug": other.slug})
    get_detail_with_queries(data)
    assert data.client.get(other_url).status_code == 200
    other_key = product_detail_key(other.pk)
    assert data.control.exists(other_key)
    data.category.name = "Wireless mice"
    data.category.save(update_fields=("name",))
    assert not data.control.exists(data.key)
    assert not data.control.exists(other_key)
    assert data.client.get(data.url).json()["category"]["name"] == "Wireless mice"
    assert data.client.get(other_url).json()["category"]["name"] == "Wireless mice"


def test_sku_move_invalidates_old_and_new_product(catalog_cache):
    data = catalog_cache
    other = Product.objects.create(
        category=data.category, name="Mouse M2", slug="mouse-m2", is_active=True
    )
    other_url = reverse("catalog:product-detail", kwargs={"slug": other.slug})
    get_detail_with_queries(data)
    assert data.client.get(other_url).json()["skus"] == []
    other_key = product_detail_key(other.pk)
    data.sku.product = other
    data.sku.save(update_fields=("product",))
    assert not data.control.exists(data.key)
    assert not data.control.exists(other_key)
    assert data.client.get(data.url).json()["skus"] == []
    assert data.client.get(other_url).json()["skus"][0]["code"] == data.sku.code


def test_transaction_reads_mysql_without_reading_or_filling_cache(catalog_cache):
    data = catalog_cache
    get_detail_with_queries(data)
    cached_bytes = data.control.get(data.key)
    with transaction.atomic():
        data.product.name = "Uncommitted name"
        data.product.save(update_fields=("name",))
        payload, queries = get_detail_with_queries(data)
        assert payload["name"] == "Uncommitted name"
        assert queries == 2
        assert data.control.get(data.key) == cached_bytes
        transaction.set_rollback(True)
    data.control.delete(data.key)
    with transaction.atomic():
        get_detail_with_queries(data)
        assert not data.control.exists(data.key)


def test_real_connection_failure_falls_back_and_commit_still_succeeds(
    catalog_cache, settings
):
    data = catalog_cache
    old_payload, _ = get_detail_with_queries(data)
    healthy_client = get_redis_client()
    healthy_client.connection_pool.disconnect()
    # Reserve a local port without listening; no unrelated Redis is contacted.
    unused_port = socket.socket()
    unused_port.bind(("127.0.0.1", 0))
    port = unused_port.getsockname()[1]
    settings.CATALOG_CACHE_REDIS_URL = f"redis://127.0.0.1:{port}/0"
    get_redis_client.cache_clear()
    failed_client = get_redis_client()
    try:
        payload, queries = get_detail_with_queries(data)
        assert payload == old_payload
        assert queries == 2
        data.product.name = "Committed while Redis unavailable"
        data.product.save(update_fields=("name",))
        data.product.refresh_from_db()
        assert data.product.name == "Committed while Redis unavailable"
        payload, queries = get_detail_with_queries(data)
        assert payload["name"] == data.product.name
        assert queries == 2
    finally:
        failed_client.connection_pool.disconnect()
        unused_port.close()
        settings.CATALOG_CACHE_REDIS_URL = data.redis_url
        get_redis_client.cache_clear()
    # Failed invalidation leaves a stale key after recovery until TTL/another write.
    recovered_payload, queries = get_detail_with_queries(data)
    assert recovered_payload == old_payload
    assert queries == 1


@pytest.mark.parametrize("operation", ["set", "delete"])
def test_injected_write_failures_do_not_break_mysql(catalog_cache, monkeypatch, operation):
    """Supplementary injection, distinct from the real connection-failure evidence."""
    data = catalog_cache
    redis_client = get_redis_client()
    if operation == "delete":
        get_detail_with_queries(data)

    def timeout(*args, **kwargs):
        raise redis.TimeoutError("Injected cache write timeout")

    monkeypatch.setattr(redis_client, operation, timeout)
    if operation == "set":
        payload, queries = get_detail_with_queries(data)
        assert payload["name"] == data.product.name
        assert queries == 2
        assert not data.control.exists(data.key)
    else:
        data.product.name = "Committed despite DEL timeout"
        data.product.save(update_fields=("name",))
        data.product.refresh_from_db()
        assert data.product.name == "Committed despite DEL timeout"


def test_stale_stock_hint_cannot_authorize_checkout(catalog_cache):
    data = catalog_cache
    user, address = checkout_fixture(data)
    payload, _ = get_detail_with_queries(data)
    assert payload["in_stock"] is True
    data.inventory.available_stock = 0
    data.inventory.save(update_fields=("available_stock",))
    warm_payload, queries = get_detail_with_queries(data)
    assert warm_payload["in_stock"] is True
    assert queries == 1
    with pytest.raises(OrderRuleError) as error:
        create_order_from_cart(
            user=user, address_id=address.pk, idempotency_key="stale-stock-cache"
        )
    assert error.value.code == "insufficient_stock"
    assert not Order.objects.exists()
    assert CartItem.objects.filter(cart__user=user).exists()


def test_stale_cached_price_cannot_set_order_amount(catalog_cache):
    data = catalog_cache
    user, address = checkout_fixture(data)
    get_detail_with_queries(data)
    SKU.objects.filter(pk=data.sku.pk).update(price=Decimal("159.00"))
    assert data.client.get(data.url).json()["skus"][0]["price"] == "129.00"
    order, created = create_order_from_cart(
        user=user, address_id=address.pk, idempotency_key="stale-price-cache"
    )
    assert created is True
    assert order.total == Decimal("159.00")
    assert order.items.get().unit_price == Decimal("159.00")


def test_delayed_old_refill_exposes_documented_consistency_window(
    catalog_cache, monkeypatch
):
    data = catalog_cache
    serialized = Event()
    release_reader = Event()
    results = Queue()
    original_set = ProductDetailCache.set

    def delayed_set(cache, payload):
        serialized.set()
        if not release_reader.wait(timeout=10):
            raise AssertionError("Writer did not release the paused cache reader")
        return original_set(cache, payload)

    def old_reader():
        close_old_connections()
        try:
            response = APIClient().get(data.url)
            results.put((response.status_code, response.json(), None))
        except Exception as exc:
            results.put((None, None, exc))
        finally:
            close_old_connections()

    monkeypatch.setattr(ProductDetailCache, "set", delayed_set)
    reader = Thread(target=old_reader)
    reader.start()
    try:
        assert serialized.wait(timeout=10)
        data.product.name = "New committed name"
        data.product.save(update_fields=("name",))
        assert not data.control.exists(data.key)
    finally:
        release_reader.set()
        reader.join(timeout=10)
        monkeypatch.setattr(ProductDetailCache, "set", original_set)
    assert not reader.is_alive()
    status_code, old_payload, error = results.get_nowait()
    assert error is None
    assert status_code == 200
    assert old_payload["name"] == "Mouse M1"
    warm_payload, queries = get_detail_with_queries(data)
    assert warm_payload["name"] == "Mouse M1"
    assert queries == 1
    data.product.refresh_from_db()
    assert data.product.name == "New committed name"
    assert data.control.ttl(data.key) > 0
