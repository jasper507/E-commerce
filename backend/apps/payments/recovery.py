"""人工运行一轮 Inbox 恢复；只选择首次处理或符合有界重试策略的事件。"""

import logging
from dataclasses import dataclass, field

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from apps.payments.dispatch import publish_payment_event
from apps.payments.models import PaymentEvent
from apps.payments.services import MAX_EVENT_PROCESSING_ATTEMPTS, process_stored_payment_event


logger = logging.getLogger(__name__)


@dataclass
class EventRecoveryResult:
    selected_ids: tuple[int, ...]
    processed: int = 0
    rejected: int = 0
    skipped: int = 0
    failed_ids: list[int] = field(default_factory=list)


@dataclass
class EventPublicationResult:
    selected_ids: tuple[int, ...]
    published_ids: list[int] = field(default_factory=list)
    failed_ids: list[int] = field(default_factory=list)
    skipped_ids: list[int] = field(default_factory=list)
    unattempted_ids: list[int] = field(default_factory=list)


def _publication_candidates(*, cutoff):
    return PaymentEvent.objects.filter(
        Q(processing_status=PaymentEvent.ProcessingStatus.RECEIVED,
          failure_kind=PaymentEvent.FailureKind.NONE, processing_attempt_count=0)
        | Q(processing_status=PaymentEvent.ProcessingStatus.ERROR,
            failure_kind=PaymentEvent.FailureKind.TRANSIENT,
            processing_attempt_count__gt=0,
            processing_attempt_count__lt=MAX_EVENT_PROCESSING_ATTEMPTS,
            next_retry_at__lte=cutoff)
    )


def republish_payment_events(*, limit=100, event_id=None, dry_run=False, as_of=None):
    """人工补投固定一批原事件 ID；不领取业务资格或修改数据库。

    无法判断早前消息是否到达，重复投递由消费者的事务门槛处理。
    Broker 首次失败就停发，剩余事件留到下一轮，避免逐条等待故障依赖。
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit 必须为正整数。")
    if event_id is not None and (
        not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1
    ):
        raise ValueError("event_id 必须为正整数的 Inbox 数据库 ID。")
    if connection.in_atomic_block:
        raise ValueError("发布补漏必须在外层事务之外运行。")

    cutoff = as_of if as_of is not None else timezone.now()
    candidates = _publication_candidates(cutoff=cutoff)
    if event_id is not None:
        candidates = candidates.filter(pk=event_id)
    event_ids = tuple(candidates.order_by("received_at", "pk").values_list("pk", flat=True)[:limit])
    result = EventPublicationResult(selected_ids=event_ids)
    if dry_run:
        return result

    for position, pending_id in enumerate(event_ids):
        # 扫描后的状态可能已被其他执行者改变；网络等待仍不持锁。
        current_cutoff = as_of if as_of is not None else timezone.now()
        if not _publication_candidates(cutoff=current_cutoff).filter(pk=pending_id).exists():
            result.skipped_ids.append(pending_id)
            continue
        if not publish_payment_event(event_id=pending_id):
            result.failed_ids.append(pending_id)
            result.unattempted_ids.extend(event_ids[position + 1:])
            break
        result.published_ids.append(pending_id)
    return result


def recover_payment_events(*, limit=100, event_id=None, dry_run=False, as_of=None):
    """固定选出一批未完成事件，每条至多尝试一次，失败后继续下一条。

    limit 是本轮批量上限；累计次数/失败分类/等待时间以数据库为准。
    选取时不持行锁，真正处理时重新锁定事件并检查资格。
    """

    if limit < 1:
        raise ValueError("limit 必须为正整数。")
    if event_id is not None and event_id < 1:
        raise ValueError("event_id 必须为正整数的 Inbox 数据库 ID。")
    if connection.in_atomic_block:
        raise ValueError("恢复扫描必须在外层事务之外运行。")

    cutoff = as_of if as_of is not None else timezone.now()
    candidates = PaymentEvent.objects.filter(
        Q(processing_status=PaymentEvent.ProcessingStatus.RECEIVED,
          failure_kind=PaymentEvent.FailureKind.NONE)
        | Q(processing_status=PaymentEvent.ProcessingStatus.ERROR,
            failure_kind=PaymentEvent.FailureKind.TRANSIENT,
            next_retry_at__lte=cutoff)
    ).filter(
        processing_attempt_count__lt=MAX_EVENT_PROCESSING_ATTEMPTS
    )
    if event_id is not None:
        candidates = candidates.filter(pk=event_id)
    event_ids = tuple(
        candidates.order_by("received_at", "pk").values_list("pk", flat=True)[:limit]
    )
    result = EventRecoveryResult(selected_ids=event_ids)
    if dry_run:
        return result

    for pending_id in event_ids:
        try:
            event = process_stored_payment_event(event_id=pending_id, as_of=as_of)
        except Exception:
            result.failed_ids.append(pending_id)
            # 异常原文可能含 SQL/交易输入；只输出内部 ID，原事件保留在 Inbox。
            logger.warning("Payment Inbox recovery failed: inbox_id=%s", pending_id)
            continue
        if event.processing_status == PaymentEvent.ProcessingStatus.PROCESSED:
            result.processed += 1
        elif event.processing_status == PaymentEvent.ProcessingStatus.REJECTED:
            result.rejected += 1
        else:
            result.skipped += 1

    return result
