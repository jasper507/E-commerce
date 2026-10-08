"""秒杀 HTTP 入口的共享窗口预算；不承担订单或库存事实。"""

import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


logger = logging.getLogger(__name__)
ADMISSION_SCRIPT = Path(__file__).with_suffix(".lua").read_text(encoding="utf-8")


class FlashSaleAdmissionError(Exception):
    def __init__(self, code, detail, http_status, retry_after=1):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.retry_after = retry_after


@lru_cache(maxsize=1)
def get_admission_redis_client():
    return Redis.from_url(
        settings.FLASH_SALE_ADMISSION_REDIS_URL,
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
        max_connections=16,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


def admission_key():
    # One endpoint-wide key bounds total admitted attempts, including unknown item IDs.
    return f"{settings.FLASH_SALE_ADMISSION_KEY_PREFIX}:flash-sale:admission:v1"


def _unavailable():
    return FlashSaleAdmissionError(
        "flash_sale_admission_unavailable",
        "秒杀准入暂不可用，请稍后用原购买信息重试。",
        503,
    )


def admit_flash_sale_attempt():
    if not settings.FLASH_SALE_ADMISSION_ENABLED:
        return

    limit = settings.FLASH_SALE_ADMISSION_LIMIT
    window_ms = settings.FLASH_SALE_ADMISSION_WINDOW_MS
    try:
        result = get_admission_redis_client().eval(
            ADMISSION_SCRIPT, 1, admission_key(), limit, window_ms,
        )
    except RedisError as error:
        # A timeout may follow a consumed permit; do not retry or refund an unknown result.
        logger.warning("Flash-sale admission failed: %s", type(error).__name__)
        raise _unavailable() from error

    if (
        not isinstance(result, (list, tuple))
        or len(result) != 3
        or any(type(value) is not int for value in result)
    ):
        raise _unavailable()
    admitted, count, ttl_ms = result
    if not 0 <= ttl_ms <= window_ms:
        raise _unavailable()
    if admitted == 1 and 1 <= count <= limit:
        return
    if admitted == 0 and limit <= count <= 2147483647:
        raise FlashSaleAdmissionError(
            "flash_sale_rate_limited",
            "秒杀请求过多，请稍后用原购买信息重试。",
            429,
            retry_after=max(1, (ttl_ms + 999) // 1000),
        )
    raise _unavailable()
