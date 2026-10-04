"""Celery 只承担执行入口，不维护第二套支付状态机或独立重试预算。"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.payments.dispatch import publish_payment_event
from apps.payments.models import PaymentEvent
from apps.payments.services import MAX_EVENT_PROCESSING_ATTEMPTS, process_stored_payment_event


logger = logging.getLogger(__name__)


class PaymentEventTaskError(Exception):
    """Worker 只报告稳定失败码，原始异常可能包含 SQL 或交易输入。"""


@shared_task(
    name="payments.process_event",
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
    throws=(PaymentEventTaskError,),
)
def process_payment_event_task(event_id):
    if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
        raise PaymentEventTaskError("invalid_inbox_id")
    processing_failed = False
    try:
        process_stored_payment_event(event_id=event_id)
    except Exception:
        processing_failed = True
        logger.warning("Payment event task failed: inbox_id=%s", event_id)

    try:
        # 以失败事务结束后的事实为准；并发处理者可能已经提交了终态。
        event = PaymentEvent.objects.get(pk=event_id)
    except Exception:
        raise PaymentEventTaskError("event_processing_failed") from None

    if (
        event.processing_status == PaymentEvent.ProcessingStatus.ERROR
        and event.failure_kind == PaymentEvent.FailureKind.TRANSIENT
        and 0 < event.processing_attempt_count < MAX_EVENT_PROCESSING_ATTEMPTS
        and event.next_retry_at is not None
    ):
        # 提前/重复消息也留下到期执行机会；不在这里领取资格或增加次数。
        # Celery 的定时器可能提前几毫秒唤醒，至少延后 1 秒避免立即循环投递。
        retry_eta = max(event.next_retry_at, timezone.now() + timedelta(seconds=1))
        try:
            published = publish_payment_event(event_id=event.pk, eta=retry_eta)
        except Exception:
            published = False
        if not published:
            logger.warning("Payment retry publication failed: inbox_id=%s", event_id)
            raise PaymentEventTaskError("event_retry_publication_failed") from None
        logger.info("Payment retry scheduled: inbox_id=%s attempt_count=%s", event_id,
                    event.processing_attempt_count)
        return event.processing_status

    if processing_failed:
        raise PaymentEventTaskError("event_processing_failed") from None
    return event.processing_status
