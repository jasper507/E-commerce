"""Bounded-memory query fixtures. No checkout/payment business flow is simulated."""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from loadtests.order_list.core import (
    DATABASE, created_second, page_ordinals, public_id, username, validate_plan,
)


BATCH_SIZE = 500
INDEX_NAME = "ordering_order_user_created_id"


def read_index_columns(connection):
    with connection.cursor() as cursor:
        cursor.execute("SHOW INDEX FROM ordering_order")
        parts = [(row[3], row[4]) for row in cursor.fetchall() if row[2] == INDEX_NAME]
    return [column for _, column in sorted(parts)]


def execute(plan, folder):
    if (folder / "dataset.json").exists() or (folder / "seed-state.json").exists():
        raise RuntimeError("Dataset or partial seed already exists; use a new run namespace.")
    os.environ["DJANGO_SETTINGS_MODULE"] = "loadtests.order_list.settings"
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.auth.hashers import make_password
    from django.core.management import call_command
    from django.db import connection, transaction
    from apps.catalog.models import Category, Product, SKU
    from apps.ordering.models import AddressSnapshot, Order, OrderItem
    from loadtests.order_list.urls import require_database

    if os.environ["ORDER_LOAD_RUN"] != plan["run_id"]:
        raise RuntimeError("Run namespace and plan disagree.")
    require_database()
    state = folder / "seed-state.json"
    with state.open("x", encoding="utf-8") as stream:
        json.dump({"status": "started", "run_id": plan["run_id"]}, stream)
    try:
        call_command("migrate", interactive=False, verbosity=0)
        User = get_user_model()
        if any(model.objects.exists() for model in (User, Order, Category, Product, SKU)):
            raise RuntimeError("The dedicated database is not empty; refusing to add fixtures.")
        if read_index_columns(connection) != ["user_id", "created_at", "id"]:
            raise RuntimeError("Expected migrated order-list index is missing or different.")
        if plan["index_mode"] == "without_read_index":
            # The one optional DDL change is confined to this disposable database.
            require_database()
            with connection.cursor() as cursor:
                cursor.execute(f"ALTER TABLE ordering_order DROP INDEX `{INDEX_NAME}`")
        category = Category.objects.create(name="合成查询分类", slug="order-load-category")
        product = Product.objects.create(category=category, name="合成查询商品", slug="order-load-product")
        skus = [SKU.objects.create(product=product, code=f"LOAD-SKU-{i}", price=Decimal("1.00"))
                for i in range(2)]
        encoded_password = make_password(os.environ["ORDER_LOAD_PASSWORD"])
        accounts = []
        per_account = plan["total_orders"] // plan["accounts"]
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for account_index in range(plan["accounts"]):
            require_database()
            name = username(plan["run_id"], account_index)
            user = User.objects.create(username=name, password=encoded_password, is_active=True)
            marker = f"order-load:{name}"
            for start in range(0, per_account, BATCH_SIZE):
                stop = min(start + BATCH_SIZE, per_account)
                orders = [Order(
                    user=user, public_id=public_id(plan["run_id"], account_index, ordinal),
                    status=Order.Status.COMPLETED,
                    subtotal=Decimal("2.00"), total=Decimal("2.00"),
                    idempotency_key=f"load-{ordinal}",
                    request_fingerprint=hashlib.sha256(f"{name}:{ordinal}".encode()).hexdigest(),
                ) for ordinal in range(start, stop)]
                with transaction.atomic():
                    Order.objects.bulk_create(orders, batch_size=BATCH_SIZE)
                    # MySQL does not populate AutoField PKs for every bulk_create.
                    ids = dict(Order.objects.filter(
                        user=user, idempotency_key__in=[o.idempotency_key for o in orders],
                    ).values_list("idempotency_key", "id"))
                    if len(ids) != len(orders):
                        raise RuntimeError("Inserted batch could not be identified exactly.")
                    for ordinal, order in zip(range(start, stop), orders):
                        order.pk = ids[order.idempotency_key]
                        order.created_at = base + timedelta(seconds=created_second(ordinal))
                        order.updated_at = order.created_at
                    # Explicitly replace auto_now_add only for this batch's PKs.
                    Order.objects.bulk_update(orders, ["created_at", "updated_at"], batch_size=BATCH_SIZE)
                    AddressSnapshot.objects.bulk_create([AddressSnapshot(
                        order=order, recipient_name=name, phone="19900000000",
                        province="合成省", city="合成市", district="合成区", detail="仅用于读取压测",
                    ) for order in orders], batch_size=BATCH_SIZE)
                    OrderItem.objects.bulk_create([OrderItem(
                        order=order, sku=sku, product_name_snapshot=marker,
                        sku_code_snapshot=sku.code, unit_price=Decimal("1.00"),
                        quantity=1, line_total=Decimal("1.00"),
                    ) for order in orders for sku in skus], batch_size=BATCH_SIZE)
            accounts.append({
                "username": name, "user_id": user.pk, "order_count": per_account,
                "marker": marker,
                "expected_ids": [public_id(plan["run_id"], account_index, ordinal)
                                 for ordinal in page_ordinals(per_account, plan["page"])],
            })
            print(f"seeded_accounts={account_index + 1} seeded_orders={(account_index + 1) * per_account}", flush=True)
        require_database()
        counts = {"orders": Order.objects.count(), "addresses": AddressSnapshot.objects.count(),
                  "items": OrderItem.objects.count(), "accounts": User.objects.count()}
        if counts != {"orders": plan["total_orders"], "addresses": plan["total_orders"],
                      "items": 2 * plan["total_orders"], "accounts": plan["accounts"]}:
            raise RuntimeError("Final fixture counts disagree with the plan.")
        columns = read_index_columns(connection)
        expected = ["user_id", "created_at", "id"] if plan["index_mode"] == "indexed" else []
        if columns != expected:
            raise RuntimeError("Actual index state disagrees with the selected mode.")
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE TABLE ordering_order, ordering_addresssnapshot, ordering_orderitem")
            cursor.fetchall()
        manifest = {"run_id": plan["run_id"], "database": DATABASE,
                    "total_orders": plan["total_orders"], "page": plan["page"],
                    "index_mode": plan["index_mode"], "counts": counts, "accounts": accounts}
        with (folder / "dataset.json").open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
        state.write_text(json.dumps({"status": "complete", "counts": counts}), encoding="utf-8")
    except Exception as exc:
        state.write_text(json.dumps({"status": "incomplete", "error_type": type(exc).__name__}), encoding="utf-8")
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path("/artifacts"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args()
    plan = validate_plan(json.loads((args.folder / "plan.json").read_text(encoding="utf-8")))
    if not args.execute:
        print(json.dumps({"dry_run": True, "plan": plan}, ensure_ascii=False, indent=2))
        return
    if args.confirm_database != DATABASE:
        parser.error(f"Execution requires --confirm-database {DATABASE}")
    execute(plan, args.folder)


if __name__ == "__main__":
    main()
