"""公开详情的可丢弃副本；交易逻辑不读取此模块。"""

import json
import logging
import random
from concurrent.futures import Future, TimeoutError
from contextlib import contextmanager
from functools import lru_cache
from threading import BoundedSemaphore, Lock
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from rest_framework.exceptions import APIException

from apps.catalog.circuit_breaker import RedisReadCircuitBreaker, RedisReadSkipped


logger = logging.getLogger(__name__)
PUBLIC_DETAIL_FIELDS = {"slug", "name", "description", "category", "in_stock", "skus"}
DETAIL_WAIT_SECONDS = 0.2
FALLBACK_CONCURRENCY = 2
MISSING_PRODUCT_TTL_SECONDS = 5
_LOAD_FAILED = object()


class CatalogFallbackBusy(APIException):
    status_code = 503
    default_detail = "商品详情暂时繁忙，请稍后重试。"
    default_code = "catalog_fallback_busy"


class FallbackLimiter:
    """详情重建与 Redis 故障回源共用进程名额；满员立即拒绝。"""

    def __init__(self, capacity):
        self._slots = BoundedSemaphore(capacity)

    @contextmanager
    def slot(self):
        if not self._slots.acquire(blocking=False):
            raise CatalogFallbackBusy()
        try:
            yield
        finally:
            self._slots.release()


detail_fallbacks = FallbackLimiter(FALLBACK_CONCURRENCY)
detail_reads = RedisReadCircuitBreaker()


class SingleFlight:
    """只共享本进程正在进行的加载，不保存长期结果。"""

    def __init__(self):
        self._lock = Lock()
        self._flights = {}

    def run(self, key, load, wait_timeout=DETAIL_WAIT_SECONDS):
        with self._lock:
            flight = self._flights.get(key)
            owner = flight is None
            if owner:
                flight = Future()
                self._flights[key] = flight

        if not owner:
            try:
                result = flight.result(timeout=wait_timeout)
            except TimeoutError:
                # 超时不撤销加载者，避免第三个请求又创建同 Key 的加载者。
                logger.warning("catalog_detail_wait_timeout")
                return load()
            if result is _LOAD_FAILED:
                return load()
            return result

        try:
            result = load()
            flight.set_result(result)
            return result
        except BaseException:
            # 只传递失败通知；原始数据库异常仍由加载者自己的请求处理。
            flight.set_result(_LOAD_FAILED)
            raise
        finally:
            with self._lock:
                if self._flights.get(key) is flight:
                    del self._flights[key]


detail_flights = SingleFlight()


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


def catalog_generation_key():
    return f"{settings.CATALOG_CACHE_KEY_PREFIX}:catalog:generation:v2"


def get_catalog_generation():
    client = get_redis_client()
    key = catalog_generation_key()
    generation = client.get(key)
    if generation is None:
        # 并发初始化只允许一个提议获胜；丢失后用随机新代，不能复用计数器初值。
        client.set(key, uuid4().hex, nx=True)
        generation = client.get(key)
    if (
        not isinstance(generation, str)
        or len(generation) != 32
        or any(char not in "0123456789abcdef" for char in generation)
    ):
        logger.warning("catalog_cache_invalid_generation")
        return None
    return generation


def product_detail_key(product_id, generation):
    return f"{settings.CATALOG_CACHE_KEY_PREFIX}:catalog:detail:v2:{generation}:{product_id}"


def missing_product_key(slug, generation):
    return f"{settings.CATALOG_CACHE_KEY_PREFIX}:catalog:missing:v1:{generation}:{slug}"


class ProductDetailCache:
    def __init__(self):
        self.key = None
        self.generation = None
        self.degraded = False
        self._fallback_admitted = False
        # 捕获代号必须发生在任何详情数据库读取之前；事务内不使用共享缓存。
        self.available = (
            settings.CATALOG_CACHE_ENABLED
            and not transaction.get_connection().in_atomic_block
        )
        if self.available:
            try:
                self.generation = detail_reads.run(get_catalog_generation)
                self.available = self.generation is not None
                self.degraded = not self.available
            except (RedisReadSkipped, RedisError, UnicodeError):
                self.available = False
                self.degraded = True
                logger.warning("catalog_cache_generation_read_failed")

    @contextmanager
    def fallback_scope(self, *, for_load=False):
        if (not self.degraded and not for_load) or self._fallback_admitted:
            yield
            return
        with detail_fallbacks.slot():
            self._fallback_admitted = True
            try:
                yield
            finally:
                self._fallback_admitted = False

    def is_missing(self, slug):
        if not self.available:
            return False
        key = missing_product_key(slug, self.generation)
        try:
            value = detail_reads.run(lambda: get_redis_client().get(key))
        except (RedisReadSkipped, RedisError, UnicodeError):
            self.available = False
            self.degraded = True
            logger.warning("catalog_missing_marker_read_failed")
            return False
        return value == "missing"

    def remember_missing(self, slug):
        if not self.available:
            return
        try:
            get_redis_client().set(
                missing_product_key(slug, self.generation),
                "missing",
                ex=MISSING_PRODUCT_TTL_SECONDS,
            )
        except RedisError:
            logger.warning("catalog_missing_marker_write_failed")

    def get(self, product_id):
        if not self.available:
            return None
        self.key = product_detail_key(product_id, self.generation)
        try:
            value = detail_reads.run(lambda: get_redis_client().get(self.key))
        except (RedisReadSkipped, RedisError, UnicodeError):
            self.available = False
            self.degraded = True
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

    def get_or_load(self, product_id, load):
        payload = self.get(product_id)
        if payload is not None:
            return payload
        if not self.available:
            with self.fallback_scope():
                return load()

        def load_and_fill():
            # 注册前可能已有另一轮加载完成；仅复查捕获的同一代详情 Key。
            payload = self.get(product_id)
            if payload is None:
                # 不同冷 Key 和等待超时后的加载也共用名额；已降级请求不重复申请。
                with self.fallback_scope(for_load=True):
                    payload = load()
                    self.set(payload)
            return payload

        return detail_flights.run(self.key, load_and_fill)


def invalidate_catalog_details():
    if not settings.CATALOG_CACHE_ENABLED:
        return
    try:
        # 全目录逻辑失效；旧读只能回填旧代，旧副本由原 TTL 回收。
        get_redis_client().set(catalog_generation_key(), uuid4().hex)
    except RedisError:
        # 数据库已提交，失效失败不能把成功的 Admin 写入变成错误响应。
        logger.warning("catalog_cache_invalidation_failed")
