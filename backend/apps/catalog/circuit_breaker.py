"""本进程公开商品详情的最小 Redis 读取熔断。"""

from threading import Lock
from time import monotonic

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BusyLoadingError,
    ExternalAuthProviderError,
    MaxConnectionsError,
)


READ_COOLDOWN_SECONDS = 1.0
NON_NETWORK_CONNECTION_ERRORS = (
    AuthenticationError,
    AuthorizationError,
    BusyLoadingError,
    ExternalAuthProviderError,
    MaxConnectionsError,
)


class RedisReadSkipped(Exception):
    """读取暂时被熔断跳过，由调用方决定降级行为。"""


class RedisReadCircuitBreaker:
    """一次指定网络失败后冷却；半开只允许一个读取操作。"""

    def __init__(self, clock=monotonic):
        self._clock = clock
        self._lock = Lock()
        self._state = "closed"
        self._epoch = 0
        self._retry_at = 0.0

    @property
    def state(self):
        with self._lock:
            return self._state

    def _open(self):
        # 调用者已持锁；换轮次让在途旧操作失去修改当前状态的资格。
        self._state = "open"
        self._epoch += 1
        self._retry_at = self._clock() + READ_COOLDOWN_SECONDS

    def run(self, operation):
        with self._lock:
            if self._state == "open":
                if self._clock() < self._retry_at:
                    raise RedisReadSkipped()
                self._state = "half_open"
            elif self._state == "half_open":
                raise RedisReadSkipped()
            epoch = self._epoch
            probe = self._state == "half_open"

        try:
            result = operation()
        except BaseException as error:
            network_failure = isinstance(error, (RedisConnectionError, RedisTimeoutError)) and not isinstance(
                error, NON_NETWORK_CONNECTION_ERRORS
            )
            # 探测异常退出不能占住资格，也不能被当作通信成功。
            if network_failure or probe:
                with self._lock:
                    if epoch == self._epoch:
                        self._open()
            raise
        else:
            with self._lock:
                if probe and epoch == self._epoch:
                    self._state = "closed"
                    self._epoch += 1
            return result
