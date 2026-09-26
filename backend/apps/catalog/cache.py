"""公开详情的可丢弃副本；交易逻辑不读取此模块。"""

import json
import logging
import random
from functools import lru_cache

from django.conf import settings
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


logger = logging.getLogger(__name__)
PUBLIC_DETAIL_FIELDS = {"slug", "name", "description", "category", "in_stock", "skus"}


@lru_cache(maxsize=1)
def get_redis_client():
    # 重用连接池；缓存故障不重试，避免一次浏览请求反复等待 Redis。
    return Redis.from_url(
        settings.CATALOG_CACHE_REDIS_URL,
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
        max_connections=16,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )


def product_detail_key(product_id):
    return f"{settings.CATALOG_CACHE_KEY_PREFIX}:catalog:detail:v1:{product_id}"


class ProductDetailCache:
    def __init__(self, product_id):
        self.key = product_detail_key(product_id)
        self.available = settings.CATALOG_CACHE_ENABLED

    def get(self):
        if not self.available:
            return None
        try:
            value = get_redis_client().get(self.key)
        except (RedisError, UnicodeError):
            self.available = False
            logger.warning("catalog_cache_read_failed")
            return None
        if value is None:
            return None
        try:
            payload = json.loads(value)
        except (ValueError, TypeError):
            logger.warning("catalog_cache_invalid_json")
            return None
        if not isinstance(payload, dict) or set(payload) != PUBLIC_DETAIL_FIELDS:
            logger.warning("catalog_cache_invalid_payload")
            return None
        return payload

    def set(self, payload):
        if not self.available:
            return
        ttl = settings.CATALOG_CACHE_TTL + random.randint(
            -settings.CATALOG_CACHE_TTL_JITTER, settings.CATALOG_CACHE_TTL_JITTER
        )
        try:
            get_redis_client().set(
                self.key, json.dumps(payload, ensure_ascii=False), ex=ttl
            )
        except RedisError:
            self.available = False
            logger.warning("catalog_cache_write_failed")


def invalidate_product_details(product_ids):
    if not settings.CATALOG_CACHE_ENABLED:
        return
    keys = [product_detail_key(pk) for pk in set(product_ids) if pk is not None]
    if not keys:
        return
    try:
        get_redis_client().delete(*keys)
    except RedisError:
        # 数据库已提交，失效失败不能把成功的 Admin 写入变成错误响应。
        logger.warning("catalog_cache_invalidation_failed")
