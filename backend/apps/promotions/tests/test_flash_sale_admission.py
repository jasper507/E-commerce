"""真实隔离 Redis/MySQL 的尝试预算，不是生产容量或并发槽实验。"""

import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
import redis
from django.contrib.auth import get_user_model
from django.conf import settings as django_settings
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from redis.backoff import NoBackoff
from redis.retry import Retry

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.models import AddressSnapshot, Order, OrderItem
from apps.promotions import admission
from apps.promotions.models import FlashSale, FlashSaleItem, FlashSalePurchase


pytestmark = pytest.mark.django_db(transaction=True)
FACT_MODELS = (Order, OrderItem, AddressSnapshot, FlashSalePurchase, InventoryReservation, InventoryMovement)


def make_customer(label):
    user = get_user_model().objects.create_user(username=label)
    address = Address.objects.create(
        user=user, recipient_name="测试顾客", phone="13800000001",
        province="广东省", city="深圳市", district="南山区", detail="测试路1号",
    )
    return user, address


@pytest.fixture
def sale():
    assert connection.vendor == "mysql"
    assert connection.settings_dict["NAME"].startswith("test_")
    category = Category.objects.create(name="Admission", slug="admission", is_active=True)
    product = Product.objects.create(category=category, name="Mouse", slug="mouse", is_active=True)
    sku = SKU.objects.create(product=product, code="ADMISSION-MOUSE", price=Decimal("129.00"), is_active=True)
    inventory = Inventory.objects.create(sku=sku, available_stock=24)
    now = timezone.now()
    flash_sale = FlashSale.objects.create(
        name="Admission test", starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(hours=1), is_active=True,
    )
    item = FlashSaleItem.objects.create(flash_sale=flash_sale, sku=sku, price=Decimal("89.00"), is_active=True)
    user, address = make_customer("admission_customer")
    client = APIClient()
    client.force_authenticate(user)
    return SimpleNamespace(
        user=user, address=address, client=client, item=item, inventory=inventory,
        url=reverse("promotions:flash-sale-orders"),
    )


@pytest.fixture
def budget(settings):
    url = os.getenv("PROJECT_ONE_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set PROJECT_ONE_TEST_REDIS_URL to an isolated real Redis service.")
    assert connection.vendor == "mysql"
    assert connection.settings_dict["NAME"].startswith("test_")
    control = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    control.ping()
    settings.FLASH_SALE_ADMISSION_ENABLED = True
    settings.FLASH_SALE_ADMISSION_REDIS_URL = url
    settings.FLASH_SALE_ADMISSION_KEY_PREFIX = f"project-one:test:admission:{uuid4().hex}"
    settings.FLASH_SALE_ADMISSION_LIMIT = 4
    settings.FLASH_SALE_ADMISSION_WINDOW_MS = 10000
    admission.get_admission_redis_client.cache_clear()
    client = admission.get_admission_redis_client()
    key = admission.admission_key()
    try:
        yield SimpleNamespace(control=control, client=client, key=key, url=url)
    finally:
        # This fixture owns exactly one random key; never FLUSHDB or scan shared keys.
        control.delete(key)
        client.close()
        admission.get_admission_redis_client.cache_clear()
        control.close()


def post(sale, key="admission-original-key", *, address_id=None):
    return sale.client.post(
        sale.url,
        {"flash_sale_item_id": sale.item.pk, "address_id": address_id or sale.address.pk},
        format="json", HTTP_IDEMPOTENCY_KEY=key,
    )


def assert_facts(sale, count):
    assert {model.__name__: model.objects.count() for model in FACT_MODELS} == {
        model.__name__: count for model in FACT_MODELS
    }
    sale.inventory.refresh_from_db()
    assert (sale.inventory.available_stock, sale.inventory.reserved_stock) == (24 - count, count)
    assert list(InventoryReservation.objects.values_list("status", flat=True)) == [InventoryReservation.Status.RESERVED] * count
    assert list(InventoryMovement.objects.values_list("movement_type", flat=True)) == [InventoryMovement.Type.RESERVE] * count


def wait_for_expiry(budget):
    deadline = time.monotonic() + 2
    while budget.control.exists(budget.key) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not budget.control.exists(budget.key)


def test_disabled_gate_keeps_mysql_baseline_without_redis(sale, settings):
    settings.FLASH_SALE_ADMISSION_ENABLED = False
    with patch.object(admission, "get_admission_redis_client", side_effect=AssertionError("Redis must not run")):
        response = post(sale)
    assert response.status_code == 201
    assert_facts(sale, 1)


@pytest.mark.redis_integration
def test_atomic_budget_across_24_independent_clients(budget):
    barrier = Barrier(24)

    def attempt(_):
        client = redis.Redis.from_url(budget.url, socket_connect_timeout=1, socket_timeout=1)
        try:
            barrier.wait(timeout=5)
            return client.eval(admission.ADMISSION_SCRIPT, 1, budget.key, 4, 10000)
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(attempt, range(24)))
    assert sum(result[0] == 1 for result in results) == 4
    assert sum(result[0] == 0 for result in results) == 20
    assert sorted(result[1] for result in results if result[0] == 1) == [1, 2, 3, 4]
    assert budget.control.get(budget.key) == b"4"
    assert 0 < budget.control.pttl(budget.key) <= 10000
    print("admission_atomic " + json.dumps({"clients": 24, "limit": 4, "admitted": 4, "denied": 20}))


@pytest.mark.redis_integration
def test_rejections_preserve_counter_and_original_expiry(budget, settings):
    settings.FLASH_SALE_ADMISSION_LIMIT = 1
    settings.FLASH_SALE_ADMISSION_WINDOW_MS = 300
    admission.admit_flash_sale_attempt()
    expires_at = budget.control.pexpiretime(budget.key)
    with pytest.raises(admission.FlashSaleAdmissionError) as caught:
        admission.admit_flash_sale_attempt()
    assert (caught.value.http_status, caught.value.retry_after) == (429, 1)
    assert budget.control.get(budget.key) == b"1"
    assert budget.control.pexpiretime(budget.key) == expires_at
    wait_for_expiry(budget)
    admission.admit_flash_sale_attempt()
    assert budget.control.get(budget.key) == b"1"


@pytest.mark.redis_integration
@pytest.mark.parametrize("bad_state", ["persistent", "fraction", "zero", "wrong_type"])
def test_bad_redis_state_fails_closed_without_repair_or_checkout(budget, sale, bad_state):
    if bad_state == "wrong_type":
        budget.control.lpush(budget.key, "bad")
        budget.control.pexpire(budget.key, 10000)
    else:
        budget.control.set(budget.key, {"persistent": "1", "fraction": "1.5", "zero": "0"}[bad_state])
        if bad_state != "persistent":
            budget.control.pexpire(budget.key, 10000)
    before = budget.control.dump(budget.key)
    original_expiry = budget.control.pexpiretime(budget.key)
    with CaptureQueriesContext(connection) as queries:
        response = post(sale)
    assert response.status_code == 503
    assert response.json()["code"] == "flash_sale_admission_unavailable"
    assert response["Retry-After"] == "1"
    assert len(queries) == 0  # Fixed identity: excludes Session authentication SQL.
    assert budget.control.dump(budget.key) == before
    assert budget.control.pexpiretime(budget.key) == original_expiry
    assert_facts(sale, 0)


@pytest.mark.redis_integration
def test_unreachable_reserved_port_returns_503_before_checkout(budget, sale):
    first = post(sale)
    assert first.status_code == 201
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))  # Keep this non-listening port reserved, don't stop Redis.
        port = reserved.getsockname()[1]
        refusing = redis.Redis(
            host="127.0.0.1", port=port, socket_connect_timeout=0.1, socket_timeout=0.1,
            retry=Retry(NoBackoff(), 0),
        )
        try:
            with patch.object(admission, "get_admission_redis_client", return_value=refusing):
                for key in ("admission-original-key", "admission-new-key"):
                    with CaptureQueriesContext(connection) as queries:
                        response = post(sale, key=key)
                    assert response.status_code == 503
                    assert len(queries) == 0
        finally:
            refusing.close()
    assert budget.control.get(budget.key) == b"1"
    assert_facts(sale, 1)
    replay = post(sale)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert_facts(sale, 1)


@pytest.mark.redis_integration
def test_unknown_result_after_real_eval_does_not_retry_or_enter_mysql(budget, sale):
    real_eval = budget.client.eval

    def lose_result(*args):
        real_eval(*args)
        raise redis.exceptions.TimeoutError("Injected lost result after real EVAL")

    with patch.object(budget.client, "eval", side_effect=lose_result) as eval_call:
        with CaptureQueriesContext(connection) as queries:
            response = post(sale)
    assert response.status_code == 503
    assert eval_call.call_count == 1
    assert budget.control.get(budget.key) == b"1"
    assert len(queries) == 0
    assert_facts(sale, 0)
    assert post(sale).status_code == 201  # Same original key, one order after recovery.
    assert_facts(sale, 1)


@pytest.mark.redis_integration
def test_auth_csrf_payload_and_key_validation_do_not_consume_budget(budget, sale):
    client = APIClient(enforce_csrf_checks=True)
    payload = {"flash_sale_item_id": sale.item.pk, "address_id": sale.address.pk}
    assert client.post(sale.url, payload, format="json").status_code == 403
    client.force_login(sale.user)
    assert client.post(sale.url, payload, format="json", HTTP_IDEMPOTENCY_KEY="valid").status_code == 403
    assert client.get(reverse("accounts:csrf")).status_code == 204
    csrf = client.cookies[django_settings.CSRF_COOKIE_NAME].value
    for invalid_payload, key in (({}, "valid"), (payload, ""), (payload, "x" * 129), (payload, "bad\tkey")):
        response = client.post(sale.url, invalid_payload, format="json", HTTP_X_CSRFTOKEN=csrf, HTTP_IDEMPOTENCY_KEY=key)
        assert response.status_code == 400
    assert not budget.control.exists(budget.key)
    assert_facts(sale, 0)


@pytest.mark.redis_integration
def test_full_window_blocks_original_key_then_recovers_same_order_and_conflict(budget, sale, settings):
    settings.FLASH_SALE_ADMISSION_LIMIT = 1
    first = post(sale)
    assert first.status_code == 201
    for address_id in (sale.address.pk, sale.address.pk + 1):
        with CaptureQueriesContext(connection) as queries:
            blocked = post(sale, address_id=address_id)
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "flash_sale_rate_limited"
        assert int(blocked["Retry-After"]) >= 1
        assert len(queries) == 0
    # Shorten only this owned test key to observe expiry without a ten-second wait.
    budget.control.pexpire(budget.key, 30)
    wait_for_expiry(budget)
    replay = post(sale)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    budget.control.pexpire(budget.key, 30)
    wait_for_expiry(budget)
    conflict = post(sale, address_id=sale.address.pk + 1)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert_facts(sale, 1)


@pytest.mark.redis_integration
def test_business_rejection_consumes_attempt_without_refund(budget, sale, settings):
    settings.FLASH_SALE_ADMISSION_LIMIT = 1
    invalid_address = sale.address.pk + 1000000
    response = post(sale, address_id=invalid_address)
    assert response.status_code == 404
    assert response.json()["code"] == "address_not_found"
    assert budget.control.get(budget.key) == b"1"
    assert post(sale).status_code == 429
    assert_facts(sale, 0)


@pytest.mark.redis_integration
@pytest.mark.parametrize("enabled", [False, True], ids=["mysql-baseline", "lua-admission"])
def test_same_24_customer_burst_counts_actual_checkout_transactions(budget, sale, settings, enabled):
    settings.FLASH_SALE_ADMISSION_ENABLED = enabled
    customers = [make_customer(f"burst_{index}") for index in range(24)]

    def attempt(index):
        try:
            connection.ensure_connection()  # Exclude connection setup from request SQL count.
            client = APIClient()
            user, address = customers[index]
            client.force_authenticate(user)
            with CaptureQueriesContext(connection) as queries:
                response = client.post(
                    sale.url, {"flash_sale_item_id": sale.item.pk, "address_id": address.pk},
                    format="json", HTTP_IDEMPOTENCY_KEY=f"burst-key-{index}",
                )
            sql = [entry["sql"] for entry in queries]
            return {"status": response.status_code, "sql_count": len(sql), "begins": sql.count("BEGIN")}
        finally:
            connections.close_all()

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(24)))
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    admitted = 4 if enabled else 24
    assert sum(result["status"] == 201 for result in results) == admitted
    assert sum(result["status"] == 429 for result in results) == 24 - admitted
    assert sum(result["begins"] for result in results) == admitted
    assert all(result["sql_count"] == 0 for result in results if result["status"] == 429)
    assert_facts(sale, admitted)
    assert list(Order.objects.values_list("total", flat=True)) == [Decimal("89.00")] * admitted
    if enabled:
        assert elapsed_ms < 10000, "A crossed window invalidates this bounded burst comparison."
        assert budget.control.get(budget.key) == b"4"
        assert budget.control.pttl(budget.key) > 0
    else:
        assert not budget.control.exists(budget.key)
    print("admission_http_burst " + json.dumps({
        "enabled": enabled, "customers": 24, "workers": 8, "limit": 4, "window_ms": 10000,
        "transactions": admitted, "created": admitted, "rate_limited": 24 - admitted,
        "request_sql_total": sum(result["sql_count"] for result in results),
        "available": 24 - admitted, "reserved": admitted, "committed_reservations": 0,
        "elapsed_ms": elapsed_ms, "results": results,
        "boundary": "Fixed authentication; local synthetic burst; elapsed is not a throughput claim.",
    }))
