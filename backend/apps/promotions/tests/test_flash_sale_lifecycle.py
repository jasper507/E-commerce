"""隔离 MySQL 上的秒杀资格、支付和关闭事务合约；不代表 HTTP/MQ 验收。"""

import json
from datetime import timedelta
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.db import OperationalError, close_old_connections, connection, connections
from django.test import override_settings
from django.utils import timezone

from apps.inventory.models import InventoryMovement, InventoryReservation
from apps.ordering.lifecycle_services import (
    OrderLifecycleError,
    cancel_order,
    expire_due_orders,
    expire_pending_order,
)
from apps.ordering.models import AddressSnapshot, Order, OrderItem
from apps.ordering.services import OrderRuleError
from apps.payments.gateway import MockGateway, sign_payload
from apps.payments.models import (
    MockGatewayPayment,
    PaymentAttempt,
    PaymentEvent,
    RefundSimulation,
)
from apps.payments.services import (
    create_payment_attempt,
    mark_attempt_unknown,
    process_stored_payment_event,
    receive_payment_event,
    start_payment,
)
from apps.promotions.models import FlashSalePurchase
from apps.promotions.lifecycle_services import FlashSalePurchaseInvariantError
from apps.promotions.tests.test_flash_sale_checkout import FlashSaleCheckoutFixture


@override_settings(PAYMENT_EVENTS_ASYNC=False, CATALOG_CACHE_ENABLED=False)
class FlashSaleLifecycleTests(FlashSaleCheckoutFixture):
    def _snapshot(self, order):
        order.refresh_from_db()
        self.inventory.refresh_from_db()
        purchase = FlashSalePurchase.objects.get(order=order)
        reservation = InventoryReservation.objects.get(order_item__order=order)
        movements = InventoryMovement.objects.filter(reservation=reservation)
        return {
            "order_id": order.pk,
            "order_status": order.status,
            "purchase": {
                "order_id": purchase.order_id,
                "customer_id": purchase.customer_id,
                "flash_sale_item_id": purchase.flash_sale_item_id,
                "status": purchase.status,
                "blocking_marker": purchase.blocking_marker,
            },
            "reservation_status": reservation.status,
            "inventory": [self.inventory.available_stock, self.inventory.reserved_stock],
            "movements": {
                kind: movements.filter(movement_type=kind).count()
                for kind in ("RESERVE", "COMMIT", "RELEASE", "RESTORE")
            },
            "fact_counts": {
                "Order": Order.objects.filter(pk=order.pk).count(),
                "OrderItem": OrderItem.objects.filter(order=order).count(),
                "AddressSnapshot": AddressSnapshot.objects.filter(order=order).count(),
                "InventoryReservation": InventoryReservation.objects.filter(order_item__order=order).count(),
                "InventoryMovement": movements.count(),
                "FlashSalePurchase": FlashSalePurchase.objects.filter(order=order).count(),
            },
            "attempts": list(PaymentAttempt.objects.filter(order=order).order_by("pk").values(
                "id", "status", "active_marker", "attempt_number",
            )),
            "events": list(PaymentEvent.objects.filter(attempt__order=order).order_by("pk").values(
                "id", "processing_status", "error_code", "processing_attempt_count",
            )),
            "refunds": list(RefundSimulation.objects.filter(order=order).order_by("pk").values(
                "attempt_id", "reason", "status",
            )),
        }

    def _assert_state(self, order, *, order_status, purchase_status, marker,
                      reservation_status, stock, movements, refunds=0):
        snapshot = self._snapshot(order)
        self.assertEqual(snapshot["order_status"], order_status)
        self.assertEqual(snapshot["purchase"], {
            "order_id": order.pk,
            "customer_id": self.user.pk,
            "flash_sale_item_id": self.flash_sale_item.pk,
            "status": purchase_status,
            "blocking_marker": marker,
        })
        self.assertEqual(snapshot["reservation_status"], reservation_status)
        self.assertEqual(snapshot["inventory"], list(stock))
        expected_movements = dict.fromkeys(("RESERVE", "COMMIT", "RELEASE", "RESTORE"), 0)
        expected_movements.update(movements)
        self.assertEqual(snapshot["movements"], expected_movements)
        self.assertEqual(snapshot["fact_counts"], {
            "Order": 1, "OrderItem": 1, "AddressSnapshot": 1,
            "InventoryReservation": 1, "InventoryMovement": sum(expected_movements.values()),
            "FlashSalePurchase": 1,
        })
        self.assertEqual(len(snapshot["refunds"]), refunds)
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.unit_price, self.flash_sale_item.price)
        self.assertEqual(order.total, self.flash_sale_item.price)
        self._assert_cart_unchanged()
        return snapshot

    def _record(self, scenario, *orders, **details):
        print("FLASH_SALE_LIFECYCLE " + json.dumps({
            "scenario": scenario,
            "snapshots": [self._snapshot(order) for order in orders],
            **details,
        }, sort_keys=True))

    def _pay(self, order, *, key, outcome="SUCCEEDED"):
        gateway = MockGateway()
        gateway.default_outcome = outcome
        attempt, created = start_payment(
            user=self.user, order_public_id=order.public_id,
            idempotency_key=key, gateway=gateway,
        )
        self.assertIs(created, True)
        payload, signature = gateway.query_payment(attempt)
        self.assertTrue(gateway.verify_event(payload, signature))
        event = PaymentEvent.objects.get(provider="mock", event_id=payload["event_id"])
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        return attempt, event, payload, signature

    def _gateway_event(self, attempt, *, outcome):
        gateway = MockGateway()
        gateway.default_outcome = outcome
        MockGatewayPayment.objects.filter(attempt=attempt).update(outcome=outcome)
        payload, signature = gateway.query_payment(attempt)
        self.assertEqual(payload["status"], outcome)
        self.assertTrue(gateway.verify_event(payload, signature))
        return payload, signature

    def _distinct_event(self, payload):
        # 同一 Mock 供应商结果的另一事件编号；仍用项目真实签名函数验签。
        distinct = {**payload, "event_id": payload["event_id"] + "-second-delivery"}
        signature = sign_payload(distinct)
        self.assertTrue(MockGateway().verify_event(distinct, signature))
        return distinct, signature

    def _make_due(self, order):
        as_of = timezone.now()
        InventoryReservation.objects.filter(order_item__order=order).update(
            expires_at=as_of - timedelta(seconds=1),
        )
        return as_of

    def _close(self, order, *, method, as_of=None):
        if method == "cancel":
            return cancel_order(public_id=order.public_id, user=self.user)
        return expire_pending_order(order_id=order.pk, as_of=as_of)

    def _assert_held(self, order):
        return self._assert_state(
            order, order_status="PENDING_PAYMENT", purchase_status="HELD", marker=1,
            reservation_status="RESERVED", stock=(4, 1), movements={"RESERVE": 1},
        )

    def _assert_paid(self, order):
        return self._assert_state(
            order, order_status="PAID", purchase_status="CONSUMED", marker=1,
            reservation_status="COMMITTED", stock=(4, 0),
            movements={"RESERVE": 1, "COMMIT": 1},
        )

    def test_success_consumes_qualification_with_one_commit_and_replays(self):
        order, _ = self._checkout(key="lifecycle-paid")
        self._assert_held(order)
        attempt, event, payload, signature = self._pay(order, key="lifecycle-paid-payment")
        paid = self._assert_paid(order)
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertIsNone(attempt.active_marker)
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual(receive_payment_event(payload=payload, signature=signature).pk, event.pk)
        self.assertEqual(self._assert_paid(order), paid)
        second_payload, second_signature = self._distinct_event(payload)
        second = receive_payment_event(payload=second_payload, signature=second_signature)
        self.assertEqual(second.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(second.error_code, "")
        self._assert_paid(order)
        replay, created = self._checkout(key="lifecycle-paid")
        self.assertEqual(replay.pk, order.pk)
        self.assertIs(created, False)
        with self.assertRaises(OrderRuleError) as limit:
            self._checkout(key="lifecycle-paid-new-key")
        self.assertEqual((limit.exception.code, limit.exception.http_status), ("flash_sale_limit_reached", 409))
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 2)
        self._record("paid_consumed_and_replayed", order)

    def test_paid_cancel_restores_inventory_and_refunds_without_returning_qualification(self):
        order, _ = self._checkout(key="lifecycle-paid-cancel")
        attempt, event, payload, signature = self._pay(order, key="lifecycle-paid-cancel-payment")
        _, changed = cancel_order(public_id=order.public_id, user=self.user)
        self.assertIs(changed, True)
        final = self._assert_state(
            order, order_status="CANCELED", purchase_status="CONSUMED", marker=1,
            reservation_status="RESTORED", stock=(5, 0),
            movements={"RESERVE": 1, "COMMIT": 1, "RESTORE": 1}, refunds=1,
        )
        self.assertEqual(final["refunds"][0]["reason"], RefundSimulation.Reason.PAID_CANCEL)
        self.assertIs(cancel_order(public_id=order.public_id, user=self.user)[1], False)
        self.assertEqual(receive_payment_event(payload=payload, signature=signature).pk, event.pk)
        self.assertEqual(self._snapshot(order), final)
        second_payload, second_signature = self._distinct_event(payload)
        second = receive_payment_event(payload=second_payload, signature=second_signature)
        self.assertEqual(second.error_code, "late_success_refund_simulated")
        current = self._snapshot(order)
        self.assertEqual(current["purchase"], final["purchase"])
        self.assertEqual(current["inventory"], final["inventory"])
        self.assertEqual(current["movements"], final["movements"])
        self.assertEqual(current["refunds"], final["refunds"])
        self.assertEqual(RefundSimulation.objects.get().attempt_id, attempt.pk)
        self.assertEqual(self._checkout(key="lifecycle-paid-cancel")[0].pk, order.pk)
        with self.assertRaises(OrderRuleError) as limit:
            self._checkout(key="lifecycle-paid-cancel-rebuy")
        self.assertEqual(limit.exception.code, "flash_sale_limit_reached")
        self._record("paid_cancel_keeps_consumed", order)

    def test_unpaid_cancel_releases_qualification_once_and_old_key_keeps_old_order(self):
        order, _ = self._checkout(key="lifecycle-unpaid-cancel")
        self.assertEqual(PaymentAttempt.objects.count(), 0)
        _, changed = cancel_order(public_id=order.public_id, user=self.user)
        self.assertIs(changed, True)
        released = self._assert_state(
            order, order_status="CANCELED", purchase_status="RELEASED", marker=None,
            reservation_status="RELEASED", stock=(5, 0), movements={"RESERVE": 1, "RELEASE": 1},
        )
        self.assertIs(cancel_order(public_id=order.public_id, user=self.user)[1], False)
        self.assertEqual(self._snapshot(order), released)
        replay, created = self._checkout(key="lifecycle-unpaid-cancel")
        self.assertEqual(replay.pk, order.pk)
        self.assertIs(created, False)
        second, created = self._checkout(key="lifecycle-unpaid-cancel-rebuy")
        self.assertIs(created, True)
        self.assertNotEqual(second.pk, order.pk)
        self._assert_held(second)
        self._record("unpaid_cancel_released_and_rebuy", order, second)

    def test_due_scan_releases_unpaid_qualification_once(self):
        order, _ = self._checkout(key="lifecycle-unpaid-expiry")
        as_of = self._make_due(order)
        self.assertEqual(PaymentAttempt.objects.count(), 0)
        self.assertEqual(expire_due_orders(as_of=as_of), (1, 0))
        released = self._assert_state(
            order, order_status="EXPIRED", purchase_status="RELEASED", marker=None,
            reservation_status="RELEASED", stock=(5, 0), movements={"RESERVE": 1, "RELEASE": 1},
        )
        self.assertEqual(expire_due_orders(as_of=as_of), (0, 0))
        self.assertIs(expire_pending_order(order_id=order.pk, as_of=as_of)[1], False)
        self.assertEqual(self._snapshot(order), released)
        replay, created = self._checkout(key="lifecycle-unpaid-expiry")
        self.assertEqual(replay.pk, order.pk)
        self.assertIs(created, False)
        self._record("unpaid_expiry_released", order)

    def test_unresolved_attempt_blocks_cancel_expiry_and_scan_without_releasing(self):
        for status in ("CREATED", "PENDING", "UNKNOWN"):
            with self.subTest(attempt_status=status):
                order, _ = self._checkout(key=f"lifecycle-unresolved-{status}")
                attempt, _ = create_payment_attempt(
                    user=self.user, order_public_id=order.public_id,
                    idempotency_key=f"lifecycle-unresolved-payment-{status}",
                )
                if status == "PENDING":
                    payload, signature = self._gateway_event(attempt, outcome="PENDING")
                    receive_payment_event(payload=payload, signature=signature)
                elif status == "UNKNOWN":
                    mark_attempt_unknown(attempt_id=attempt.pk)
                attempt.refresh_from_db()
                self.assertEqual(attempt.status, status)
                self.assertEqual(attempt.active_marker, 1)
                as_of = self._make_due(order)
                held = self._assert_held(order)
                for method in ("cancel", "expiry"):
                    with self.subTest(close_method=method):
                        with self.assertRaises(OrderLifecycleError) as unresolved:
                            self._close(order, method=method, as_of=as_of)
                        self.assertEqual((unresolved.exception.code, unresolved.exception.http_status), ("payment_unresolved", 409))
                self.assertEqual(expire_due_orders(as_of=as_of), (0, 1))
                self.assertEqual(self._snapshot(order), held)
                self._record("unresolved_attempt_keeps_held", order, attempt_status=status)
                # 用可信失败事件结束本子场景，让下一种状态复用同一顾客与活动。
                payload, signature = self._gateway_event(attempt, outcome="FAILED")
                receive_payment_event(payload=payload, signature=signature)
                self.assertIs(cancel_order(public_id=order.public_id, user=self.user)[1], True)

    def _assert_old_success_protects_repurchase(self, method):
        old_key = f"lifecycle-old-{method}"
        old_order, _ = self._checkout(key=old_key)
        attempt, _, _, _ = self._pay(old_order, key=f"lifecycle-old-payment-{method}", outcome="FAILED")
        self.assertEqual(attempt.status, PaymentAttempt.Status.FAILED)
        self._assert_held(old_order)
        as_of = self._make_due(old_order) if method == "expiry" else None
        self.assertIs(self._close(old_order, method=method, as_of=as_of)[1], True)
        closed_status = "CANCELED" if method == "cancel" else "EXPIRED"
        self._assert_state(
            old_order, order_status=closed_status, purchase_status="RELEASED", marker=None,
            reservation_status="RELEASED", stock=(5, 0), movements={"RESERVE": 1, "RELEASE": 1},
        )
        new_order, created = self._checkout(key=f"lifecycle-new-{method}")
        self.assertIs(created, True)
        self.assertNotEqual(new_order.pk, old_order.pk)
        new_before = self._assert_held(new_order)
        replay, created = self._checkout(key=old_key)
        self.assertEqual(replay.pk, old_order.pk)
        self.assertIs(created, False)
        payload, signature = self._gateway_event(attempt, outcome="SUCCEEDED")
        late = receive_payment_event(payload=payload, signature=signature)
        self.assertEqual(late.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(late.error_code, "late_success_refund_simulated")
        old_after = self._assert_state(
            old_order, order_status=closed_status, purchase_status="RELEASED", marker=None,
            reservation_status="RELEASED", stock=(4, 1),
            movements={"RESERVE": 1, "RELEASE": 1}, refunds=1,
        )
        self.assertEqual(old_after["refunds"][0]["reason"], RefundSimulation.Reason.LATE_SUCCESS)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.SUCCEEDED)
        self.assertIsNone(attempt.active_marker)
        self.assertEqual(self._snapshot(new_order), new_before)
        self.assertEqual(receive_payment_event(payload=payload, signature=signature).pk, late.pk)
        self.assertEqual(self._snapshot(old_order), old_after)
        second_payload, second_signature = self._distinct_event(payload)
        second = receive_payment_event(payload=second_payload, signature=second_signature)
        self.assertEqual(second.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(second.error_code, "late_success_refund_simulated")
        final_old = self._snapshot(old_order)
        for key in ("order_status", "purchase", "reservation_status", "inventory", "movements", "refunds"):
            self.assertEqual(final_old[key], old_after[key])
        self.assertEqual(self._snapshot(new_order), new_before)
        self.assertEqual(RefundSimulation.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentEvent.objects.count(), 3)
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(FlashSalePurchase.objects.count(), 2)
        self.assertEqual(self._checkout(key=old_key)[0].pk, old_order.pk)
        self._record("late_old_success_preserves_new_held", old_order, new_order, close_method=method)

    def test_cancel_then_rebuy_late_old_success_and_replays_do_not_touch_new_order(self):
        self._assert_old_success_protects_repurchase("cancel")

    def test_expire_then_rebuy_late_old_success_and_replays_do_not_touch_new_order(self):
        self._assert_old_success_protects_repurchase("expiry")

    def test_purchase_write_then_payment_error_rolls_back_and_due_retry_consumes_once(self):
        order, _ = self._checkout(key="lifecycle-payment-write-failure")
        attempt, _ = create_payment_attempt(
            user=self.user, order_public_id=order.public_id,
            idempotency_key="lifecycle-payment-write-failure-attempt",
        )
        payload, signature = MockGateway().create_payment(attempt)
        original_save = FlashSalePurchase.save
        clock = timezone.now()

        def write_then_fail(purchase, *args, **kwargs):
            original_save(purchase, *args, **kwargs)
            if purchase.order_id == order.pk and purchase.status == "CONSUMED":
                self.assertTrue(connection.in_atomic_block)
                stored = FlashSalePurchase.objects.get(pk=purchase.pk)
                self.assertEqual((stored.status, stored.blocking_marker), ("CONSUMED", 1))
                self.assertEqual(Order.objects.get(pk=order.pk).status, "PAID")
                self.assertEqual(InventoryMovement.objects.filter(movement_type="COMMIT").count(), 1)
                raise OperationalError(1205, "TEST ONLY failure after qualification write")

        with patch.object(FlashSalePurchase, "save", autospec=True, side_effect=write_then_fail):
            with patch("apps.payments.services.timezone.now", return_value=clock):
                with self.assertRaises(OperationalError):
                    receive_payment_event(payload=payload, signature=signature)
        event = PaymentEvent.objects.get(provider="mock", event_id=payload["event_id"])
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.ERROR)
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.TRANSIENT)
        self.assertEqual(event.error_code, "database_transient_error")
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertEqual(event.next_retry_at, clock + timedelta(seconds=5))
        self.assertIsNone(event.processing_lease_until)
        self.assertIsNone(event.attempt_id)
        self._assert_held(order)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.active_marker), ("CREATED", 1))
        self.assertEqual(RefundSimulation.objects.count(), 0)
        before_due = event.next_retry_at - timedelta(microseconds=1)
        deferred = process_stored_payment_event(event_id=event.pk, as_of=before_due)
        self.assertEqual((deferred.processing_status, deferred.processing_attempt_count), ("ERROR", 1))
        self._assert_held(order)
        self._record("payment_write_rolled_back", order, event_id=event.pk,
                     event_status=event.processing_status, failure_kind=event.failure_kind,
                     error_code=event.error_code, processing_attempt_count=event.processing_attempt_count,
                     retry_at=event.next_retry_at.isoformat())
        processed = process_stored_payment_event(event_id=event.pk, as_of=event.next_retry_at)
        self.assertEqual(processed.processing_status, PaymentEvent.ProcessingStatus.PROCESSED)
        self.assertEqual(processed.processing_attempt_count, 2)
        self.assertEqual(processed.failure_kind, PaymentEvent.FailureKind.NONE)
        self.assertEqual(processed.error_code, "")
        self.assertIsNone(processed.next_retry_at)
        self.assertIsNone(processed.processing_lease_until)
        self._assert_paid(order)
        self.assertEqual(receive_payment_event(payload=payload, signature=signature).pk, event.pk)
        self._assert_paid(order)
        self._record("payment_retry_consumed_once", order)

    def test_purchase_write_then_close_error_rolls_back_and_retry_releases_once(self):
        original_save = FlashSalePurchase.save
        for method in ("cancel", "expiry"):
            with self.subTest(close_method=method):
                order, _ = self._checkout(key=f"lifecycle-close-write-failure-{method}")
                as_of = self._make_due(order) if method == "expiry" else None

                def write_then_fail(purchase, *args, **kwargs):
                    original_save(purchase, *args, **kwargs)
                    if purchase.order_id == order.pk and purchase.status == "RELEASED":
                        self.assertTrue(connection.in_atomic_block)
                        stored = FlashSalePurchase.objects.get(pk=purchase.pk)
                        self.assertEqual((stored.status, stored.blocking_marker), ("RELEASED", None))
                        raise RuntimeError("TEST ONLY failure after qualification release write")

                with patch.object(FlashSalePurchase, "save", autospec=True, side_effect=write_then_fail):
                    with self.assertRaisesRegex(RuntimeError, "TEST ONLY failure"):
                        self._close(order, method=method, as_of=as_of)
                self._assert_held(order)
                self.assertEqual(PaymentAttempt.objects.count(), 0)
                self._record("close_write_rolled_back", order, close_method=method)
                self.assertIs(self._close(order, method=method, as_of=as_of)[1], True)
                released = self._assert_state(
                    order, order_status="CANCELED" if method == "cancel" else "EXPIRED",
                    purchase_status="RELEASED", marker=None, reservation_status="RELEASED",
                    stock=(5, 0), movements={"RESERVE": 1, "RELEASE": 1},
                )
                self.assertIs(self._close(order, method=method, as_of=as_of)[1], False)
                self.assertEqual(self._snapshot(order), released)
                self._record("close_retry_released_once", order, close_method=method)

    def test_inconsistent_purchase_blocks_payment_and_close_without_inventory_effects(self):
        order, _ = self._checkout(key="lifecycle-inconsistent-purchase")
        attempt, _ = create_payment_attempt(
            user=self.user, order_public_id=order.public_id,
            idempotency_key="lifecycle-inconsistent-purchase-payment",
        )
        # 这对字段符合 CHECK，却与待付订单不一致，模拟需要调查的存储事实。
        FlashSalePurchase.objects.filter(order=order).update(status="CONSUMED", blocking_marker=1)
        before = self._assert_state(
            order, order_status="PENDING_PAYMENT", purchase_status="CONSUMED", marker=1,
            reservation_status="RESERVED", stock=(4, 1), movements={"RESERVE": 1},
        )
        payload, signature = MockGateway().create_payment(attempt)
        with self.assertRaises(FlashSalePurchaseInvariantError):
            receive_payment_event(payload=payload, signature=signature)
        self.assertEqual(self._snapshot(order), before)
        event = PaymentEvent.objects.get(provider="mock", event_id=payload["event_id"])
        self.assertEqual(event.processing_status, PaymentEvent.ProcessingStatus.ERROR)
        self.assertEqual(event.failure_kind, PaymentEvent.FailureKind.BLOCKED)
        self.assertEqual(event.error_code, "flash_sale_purchase_invariant_error")
        self.assertEqual(event.processing_attempt_count, 1)
        self.assertIsNone(event.next_retry_at)
        self.assertIsNone(event.processing_lease_until)
        self.assertIsNone(event.attempt_id)
        deferred = process_stored_payment_event(event_id=event.pk, as_of=timezone.now() + timedelta(days=1))
        self.assertEqual((deferred.processing_status, deferred.processing_attempt_count), ("ERROR", 1))
        self.assertEqual(receive_payment_event(payload=payload, signature=signature).pk, event.pk)
        self.assertEqual(self._snapshot(order), before)

        # 只为隔离关闭的不变量检查，将 Attempt 设成已确认失败夹具，避开未确认付款门槛。
        PaymentAttempt.objects.filter(pk=attempt.pk).update(status="FAILED", active_marker=None)
        close_before = self._snapshot(order)
        as_of = self._make_due(order)
        for method in ("cancel", "expiry"):
            with self.subTest(close_method=method):
                with self.assertRaises(FlashSalePurchaseInvariantError):
                    self._close(order, method=method, as_of=as_of)
                self.assertEqual(self._snapshot(order), close_before)
        self._record("inconsistent_purchase_blocked", order, event_id=event.pk,
                     event_status=event.processing_status, failure_kind=event.failure_kind,
                     error_code=event.error_code, processing_attempt_count=event.processing_attempt_count)

    def test_two_connections_payment_vs_expiry_end_paid_consumed_with_one_commit(self):
        order, _ = self._checkout(key="lifecycle-payment-expiry-race")
        attempt, _ = create_payment_attempt(
            user=self.user, order_public_id=order.public_id,
            idempotency_key="lifecycle-payment-expiry-race-attempt",
        )
        payload, signature = MockGateway().create_payment(attempt)
        # 暂停收件之后的处理，保留真实 receiver 已提交的合法 Inbox 夹具。
        with patch("apps.payments.services.process_stored_payment_event",
                   side_effect=RuntimeError("TEST ONLY pause after durable receipt")):
            with self.assertRaisesRegex(RuntimeError, "TEST ONLY pause"):
                receive_payment_event(payload=payload, signature=signature)
        event = PaymentEvent.objects.get(provider="mock", event_id=payload["event_id"])
        self.assertEqual((event.processing_status, event.processing_attempt_count), ("RECEIVED", 0))
        as_of = self._make_due(order)
        start, results = Barrier(2), Queue()

        def run(role):
            close_old_connections()
            connection_id = None
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
                    cursor.execute("SELECT CONNECTION_ID()")
                    connection_id = cursor.fetchone()[0]
                start.wait(timeout=10)
                if role == "payment":
                    processed = process_stored_payment_event(event_id=event.pk)
                    outcome = {"status": processed.processing_status, "attempt_count": processed.processing_attempt_count}
                else:
                    try:
                        _, changed = expire_pending_order(order_id=order.pk, as_of=as_of)
                        outcome = {"changed": changed, "code": None}
                    except OrderLifecycleError as error:
                        outcome = {"changed": False, "code": error.code}
                results.put({"role": role, "connection_id": connection_id, "outcome": outcome})
            except Exception as error:
                results.put({"role": role, "connection_id": connection_id, "unexpected_error": repr(error)})
            finally:
                connections.close_all()

        threads = [Thread(target=run, args=(role,), daemon=True) for role in ("payment", "expiry")]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
        finally:
            start.abort()
            for thread in threads:
                if thread.ident is not None and thread.is_alive():
                    thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.qsize(), 2)
        outcomes = [results.get_nowait() for _ in threads]
        self.assertTrue(all("unexpected_error" not in result for result in outcomes), outcomes)
        self.assertTrue(all(result["connection_id"] is not None for result in outcomes), outcomes)
        self.assertEqual(len({result["connection_id"] for result in outcomes}), 2)
        by_role = {result["role"]: result["outcome"] for result in outcomes}
        self.assertEqual(by_role["payment"], {"status": "PROCESSED", "attempt_count": 1})
        self.assertIn(by_role["expiry"], (
            {"changed": False, "code": "payment_unresolved"},
            {"changed": False, "code": None},
        ))
        paid = self._assert_paid(order)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.active_marker), ("SUCCEEDED", None))
        self.assertIs(expire_pending_order(order_id=order.pk, as_of=as_of)[1], False)
        self.assertEqual(receive_payment_event(payload=payload, signature=signature).pk, event.pk)
        self.assertEqual(self._snapshot(order), paid)
        self._record("two_connection_payment_vs_expiry", order, thread_results=outcomes)
