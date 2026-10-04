"""提交 Inbox 后只发布内部 ID；发布失败不伪装成支付业务失败。"""

import logging

from amqp.exceptions import AMQPError
from django.db import connection
from kombu.exceptions import OperationalError


logger = logging.getLogger(__name__)


def publish_payment_event(*, event_id, eta=None):
    if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
        raise ValueError("event_id 必须为正整数的 Inbox 数据库 ID。")
    if connection.in_atomic_block:
        raise ValueError("消息发布必须在数据库事务之外运行。")

    from apps.payments.tasks import process_payment_event_task

    options = {} if eta is None else {"eta": eta}
    try:
        # 不在 HTTP 入口反复等 Broker；原文已提交，漏投可由 Inbox 补投命令恢复。
        process_payment_event_task.apply_async(
            args=(event_id,), retry=False, timeout=3, confirm_timeout=3, **options
        )
    except (OperationalError, AMQPError, OSError):
        logger.warning("Payment event publication failed: inbox_id=%s", event_id)
        return False
    return True
