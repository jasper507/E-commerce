"""本地隔离 MySQL 的支付/到期锁竞争实验；必须观察真实的目标记录锁等待。"""

import hashlib
import json
import os
import platform
import subprocess
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from time import monotonic, sleep

import django
import pytest
from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.utils import timezone

from apps.accounts.models import Address
from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory, InventoryMovement, InventoryReservation
from apps.ordering.lifecycle_services import OrderLifecycleError, expire_pending_order
from apps.ordering.models import Cart, CartItem, Order
from apps.ordering.services import create_order_from_cart
from apps.payments.gateway import sign_payload
from apps.payments.models import PaymentAttempt, PaymentEvent, RefundSimulation
from apps.payments.services import (
    create_payment_attempt,
    mark_attempt_unknown,
    process_stored_payment_event,
    receive_payment_event,
)


ROOT = Path(__file__).resolve().parents[4]
EXPERIMENT_ENABLED = os.getenv("PROJECT_ONE_TEST_EXPIRATION_CONCURRENCY") == "true"
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not EXPERIMENT_ENABLED, reason="Requires explicit local MySQL lock observation experiment."),
]


@pytest.fixture(scope="module")
def observations():
    rows = []
    yield rows
    if os.getenv("PROJECT_ONE_WRITE_EXPIRATION_EVIDENCE") == "true" and len(rows) == 6:
        paths = (
            "backend/apps/ordering/tests/test_payment_expiration_concurrency.py",
            "backend/apps/ordering/lifecycle_services.py",
            "backend/apps/payments/services.py",
        )
        evidence = {
            "recorded_at": timezone.now().isoformat(),
            "environment": {"database": "test_project_one", "host": "127.0.0.1", "port": 3307,
                            "python": platform.python_version(), "django": django.get_version(),
                            "payment_events_async": False, "real_mq": False},
            "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths},
            "scenarios": rows,
        }
        (ROOT / "docs/verification/phase-5d-payment-expiration-concurrency-data.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


@pytest.fixture
def payment_order(settings):
    settings.PAYMENT_EVENTS_ASYNC = False
    settings.CATALOG_CACHE_ENABLED = False
    assert settings.ENVIRONMENT == "test"
    assert connection.vendor == "mysql"
    assert connection.settings_dict["NAME"] == "test_project_one"
    assert connection.settings_dict["HOST"] == "127.0.0.1"
    assert int(connection.settings_dict["PORT"]) == 3307
    user = get_user_model().objects.create_user(username="expiration_customer", password="S3cure-test-pass!")
    address = Address.objects.create(
        user=user, recipient_name="并发测试顾客", phone="13800138000", province="广东省",
        city="深圳市", district="南山区", detail="测试路1号", default_marker=1,
    )
    category = Category.objects.create(name="到期实验", slug="expiration-mice", is_active=True)
    product = Product.objects.create(category=category, name="实验鼠标", slug="expiration-mouse", is_active=True)
    sku = SKU.objects.create(product=product, code="EXPIRATION-MOUSE", price=Decimal("189.00"), is_active=True)
    inventory = Inventory.objects.create(sku=sku, available_stock=20)
    cart = Cart.objects.create(user=user)
    CartItem.objects.create(cart=cart, sku=sku, quantity=1)
    order, created = create_order_from_cart(user=user, address_id=address.pk, idempotency_key="expiration-order")
    assert created and order.total == Decimal("189.00")
    attempt, created = create_payment_attempt(
        user=user, order_public_id=order.public_id, idempotency_key="expiration-payment",
    )
    assert created
    return order, attempt, inventory


def _payload(order, attempt, *, outcome, external_id):
    return {"provider": "mock", "event_id": external_id, "provider_reference": str(attempt.provider_reference),
            "order_public_id": str(order.public_id), "amount": str(attempt.amount),
            "currency": attempt.currency, "status": outcome}


def _observe_wait(*, holder_id, contender_id, server_uuid, order_id):
    # 密码仅在容器内取环境变量；不向命令参数、输出或实验数据暴露其值。
    sql = f"""
        SELECT @@server_uuid, @@version;
        SELECT r.PROCESSLIST_ID, b.PROCESSLIST_ID, l.OBJECT_SCHEMA, l.OBJECT_NAME,
               l.INDEX_NAME, l.LOCK_TYPE, l.LOCK_STATUS, l.LOCK_DATA, l.LOCK_MODE,
               held.LOCK_STATUS, held.LOCK_DATA, held.LOCK_MODE
        FROM performance_schema.data_lock_waits AS w
        JOIN performance_schema.threads AS r ON r.THREAD_ID = w.REQUESTING_THREAD_ID
        JOIN performance_schema.threads AS b ON b.THREAD_ID = w.BLOCKING_THREAD_ID
        JOIN performance_schema.data_locks AS l
          ON l.ENGINE = w.ENGINE AND l.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
        JOIN performance_schema.data_locks AS held
          ON held.ENGINE = w.ENGINE AND held.ENGINE_LOCK_ID = w.BLOCKING_ENGINE_LOCK_ID
        WHERE r.PROCESSLIST_ID = {int(contender_id)} AND b.PROCESSLIST_ID = {int(holder_id)}
          AND l.OBJECT_SCHEMA = 'test_project_one' AND l.OBJECT_NAME = 'ordering_order'
          AND l.INDEX_NAME = 'PRIMARY' AND l.LOCK_TYPE = 'RECORD' AND l.LOCK_STATUS = 'WAITING'
          AND l.LOCK_DATA = '{int(order_id)}'
          AND held.OBJECT_SCHEMA = l.OBJECT_SCHEMA AND held.OBJECT_NAME = l.OBJECT_NAME
          AND held.INDEX_NAME = l.INDEX_NAME AND held.LOCK_TYPE = 'RECORD'
          AND held.LOCK_STATUS = 'GRANTED' AND held.LOCK_DATA = l.LOCK_DATA;
    """
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "sh", "-c",
         'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root --batch --raw --skip-column-names --execute="$1"',
         "sh", sql],
        cwd=ROOT, capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, "Unable to read isolated MySQL lock observation; no inferred fallback."
    lines = result.stdout.strip().splitlines()
    assert lines, "No MySQL server identity returned."
    observed_uuid, version = lines[0].split("\t")
    assert observed_uuid == server_uuid, "Docker observer and application connections target different servers."
    if len(lines) == 1:
        return None
    values = lines[1].split("\t")
    assert values[:8] == [str(contender_id), str(holder_id), "test_project_one", "ordering_order",
                           "PRIMARY", "RECORD", "WAITING", str(order_id)]
    assert values[9:11] == ["GRANTED", str(order_id)]
    return {"requesting_connection_id": contender_id, "blocking_connection_id": holder_id,
            "database": values[2], "table": values[3], "index": values[4], "lock_type": values[5],
            "request_status": values[6], "record_id": int(values[7]), "request_mode": values[8],
            "blocking_status": values[9], "blocking_mode": values[11], "mysql_version": version,
            "same_server_verified": True}


def _paid_snapshot(order, attempt, inventory, event):
    order.refresh_from_db()
    attempt.refresh_from_db()
    inventory.refresh_from_db()
    event.refresh_from_db()
    reservation = InventoryReservation.objects.get(order_item__order=order)
    movements = InventoryMovement.objects.filter(reservation=reservation)
    result = {
        "order": order.status, "payment": attempt.status, "event": event.processing_status,
        "event_attempt_count": event.processing_attempt_count, "reservation": reservation.status,
        "inventory": [inventory.available_stock, inventory.reserved_stock],
        "reserve_count": movements.filter(movement_type="RESERVE").count(),
        "commit_count": movements.filter(movement_type="COMMIT").count(),
        "release_count": movements.filter(movement_type="RELEASE").count(),
        "restore_count": movements.filter(movement_type="RESTORE").count(),
        "refund_count": RefundSimulation.objects.filter(attempt=attempt).count(),
        "attempt_count": PaymentAttempt.objects.filter(order=order).count(),
        "amount": str(attempt.amount), "currency": attempt.currency,
    }
    assert result == {
        "order": "PAID", "payment": "SUCCEEDED", "event": "PROCESSED", "event_attempt_count": 1,
        "reservation": "COMMITTED", "inventory": [19, 0], "reserve_count": 1, "commit_count": 1,
        "release_count": 0, "restore_count": 0, "refund_count": 0, "attempt_count": 1,
        "amount": "189.00", "currency": "CNY",
    }
    assert event.failure_kind == "NONE" and event.error_code == "" and event.processing_lease_until is None
    return result


@pytest.mark.parametrize("attempt_status", ("CREATED", "PENDING", "UNKNOWN"))
@pytest.mark.parametrize("first", ("payment", "expiration"))
def test_payment_and_expiration_share_order_lock(payment_order, observations, first, attempt_status):
    order, attempt, inventory = payment_order
    if attempt_status == "PENDING":
        pending = _payload(order, attempt, outcome="PENDING", external_id="expiration-pending")
        assert receive_payment_event(payload=pending, signature=sign_payload(pending)).processing_status == "PROCESSED"
    elif attempt_status == "UNKNOWN":
        mark_attempt_unknown(attempt_id=attempt.pk)
    attempt.refresh_from_db()
    assert attempt.status == attempt_status
    as_of = timezone.now()
    InventoryReservation.objects.filter(order_item__order=order).update(expires_at=as_of - timedelta(seconds=1))
    payload = _payload(order, attempt, outcome="SUCCEEDED", external_id="expiration-success")
    # 这是已提交的合法 Inbox fixture；实际领取、验签和业务事务仍调用原处理器。
    event = PaymentEvent.objects.create(provider="mock", event_id=payload["event_id"],
                                        raw_payload=payload, signature=sign_payload(payload))
    inventory.refresh_from_db()
    assert [inventory.available_stock, inventory.reserved_stock] == [19, 1]
    locked, release, contender_started, contender_done = Event(), Event(), Event(), Event()
    identities, outcomes = {}, Queue()
    contender = "expiration" if first == "payment" else "payment"

    def run(role):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT CONNECTION_ID(), @@server_uuid")
                identities[role] = cursor.fetchone()

            def trace(execute, sql, params, many, context):
                order_lock = "FOR UPDATE" in sql and "FROM `ordering_order`" in sql and order.pk in (params or ())
                if role == contender and order_lock:
                    contender_started.set()
                result = execute(sql, params, many, context)
                if role == first and order_lock:
                    assert context["connection"].in_atomic_block
                    locked.set()
                    if not release.wait(timeout=30):
                        raise TimeoutError("Controlled lock holder was not released.")
                return result

            with connection.execute_wrapper(trace):
                if role == "payment":
                    processed = process_stored_payment_event(event_id=event.pk)
                    result = {"status": processed.processing_status, "count": processed.processing_attempt_count}
                else:
                    try:
                        _, changed = expire_pending_order(order_id=order.pk, as_of=as_of)
                        result = {"changed": changed, "code": None}
                    except OrderLifecycleError as error:
                        result = {"changed": False, "code": error.code}
            outcomes.put((role, result, None))
        except Exception as error:
            outcomes.put((role, None, error))
        finally:
            connections.close_all()
            if role == contender:
                contender_done.set()

    holder_thread, contender_thread = Thread(target=run, args=(first,)), Thread(target=run, args=(contender,))
    holder_thread.start()
    try:
        assert locked.wait(timeout=10), "Holder did not acquire the real Order row lock."
        contender_thread.start()
        assert contender_started.wait(timeout=10), "Contender did not execute the original Order locking query."
        holder_id, holder_uuid = identities[first]
        contender_id, contender_uuid = identities[contender]
        assert holder_id != contender_id and holder_uuid == contender_uuid
        deadline, wait = monotonic() + 10, None
        while monotonic() < deadline and wait is None:
            assert not contender_done.is_set(), "Contender completed while the target Order lock was held."
            wait = _observe_wait(holder_id=holder_id, contender_id=contender_id,
                                 server_uuid=holder_uuid, order_id=order.pk)
            if wait is None:
                sleep(0.05)
        assert wait is not None, "No real waiting Order PRIMARY record lock was observed."
        assert not contender_done.is_set()
        order.refresh_from_db()
        inventory.refresh_from_db()
        assert order.status == "PENDING_PAYMENT" and [inventory.available_stock, inventory.reserved_stock] == [19, 1]
    finally:
        release.set()
        holder_thread.join(timeout=20)
        if contender_thread.ident is not None:
            contender_thread.join(timeout=20)
        assert not holder_thread.is_alive() and not contender_thread.is_alive(), "Experiment threads did not stop."
    assert outcomes.qsize() == 2
    results = {}
    for _ in range(2):
        role, result, error = outcomes.get_nowait()
        assert error is None, f"{role} failed: {type(error).__name__ if error else ''}"
        results[role] = result
    assert results["payment"] == {"status": "PROCESSED", "count": 1}
    assert results["expiration"] == {"changed": False, "code": "payment_unresolved" if first == "expiration" else None}
    final = _paid_snapshot(order, attempt, inventory, event)
    # 同一个原事件及已支付订单的到期操作重放，仍不得增加次数或库存效果。
    assert receive_payment_event(payload=payload, signature=sign_payload(payload)).pk == event.pk
    assert expire_pending_order(order_id=order.pk, as_of=as_of)[1] is False
    assert _paid_snapshot(order, attempt, inventory, event) == final
    assert PaymentEvent.objects.filter(provider="mock", event_id=payload["event_id"]).count() == 1
    row = {"first_lock_owner": first, "initial_attempt_status": attempt_status,
           "lock_wait": wait, "expiration_result": results["expiration"], "final": final,
           "duplicate_event_and_expiration_unchanged": True}
    observations.append(row)
    print("PAYMENT_EXPIRATION_LOCK " + json.dumps(row, ensure_ascii=False))
