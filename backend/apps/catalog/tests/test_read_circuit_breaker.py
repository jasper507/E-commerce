"""纯状态测试，不连接数据库或 Redis；时间由测试直接推进。"""

from concurrent.futures import TimeoutError as WaitTimeoutError
from queue import Queue
from threading import Event, Thread

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import (
    AuthenticationError, AuthorizationError, BusyLoadingError,
    ExternalAuthProviderError, MaxConnectionsError,
)
from redis.exceptions import TimeoutError as RedisTimeoutError

from apps.catalog.circuit_breaker import RedisReadCircuitBreaker, RedisReadSkipped


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_network_failure_skips_next_read_without_calling_redis():
    breaker = RedisReadCircuitBreaker(clock=FakeClock())
    attempts = []

    def timed_out_read():
        attempts.append(True)
        raise RedisTimeoutError("Isolated test timeout")

    with pytest.raises(RedisTimeoutError):
        breaker.run(timed_out_read)
    with pytest.raises(RedisReadSkipped):
        breaker.run(timed_out_read)
    assert len(attempts) == 1


def connection_failure():
    raise RedisConnectionError("Isolated test connection failure")


def open_breaker(breaker):
    with pytest.raises(RedisConnectionError):
        breaker.run(connection_failure)
    assert breaker.state == "open"


def start_call(operation):
    outcomes = Queue()

    def execute():
        try:
            outcomes.put((operation(), None))
        except BaseException as error:
            outcomes.put((None, error))

    thread = Thread(target=execute, daemon=True)
    thread.start()
    return thread, outcomes


def test_cooldown_boundary_and_successful_miss_restore_reads():
    clock = FakeClock()
    breaker = RedisReadCircuitBreaker(clock=clock)
    open_breaker(breaker)
    clock.advance(0.999)
    with pytest.raises(RedisReadSkipped):
        breaker.run(lambda: pytest.fail("Read ran before cooldown ended"))
    clock.advance(0.001)
    assert breaker.run(lambda: None) is None
    assert breaker.state == "closed"
    assert breaker.run(lambda: "next normal read") == "next normal read"


def test_half_open_has_one_probe_and_does_not_hold_lock_during_read():
    clock = FakeClock()
    breaker = RedisReadCircuitBreaker(clock=clock)
    open_breaker(breaker)
    clock.advance(1)
    entered = Event()
    release = Event()

    def probe():
        entered.set()
        assert release.wait(timeout=5)
        return "Redis replied"

    thread, outcomes = start_call(lambda: breaker.run(probe))
    try:
        assert entered.wait(timeout=5)
        assert breaker.state == "half_open"
        for _ in range(5):
            with pytest.raises(RedisReadSkipped):
                breaker.run(lambda: pytest.fail("A second probe was admitted"))
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcomes.get_nowait() == ("Redis replied", None)
    assert breaker.state == "closed"


def test_failed_probe_starts_a_new_full_cooldown():
    clock = FakeClock()
    breaker = RedisReadCircuitBreaker(clock=clock)
    open_breaker(breaker)
    clock.advance(1)
    open_breaker(breaker)
    clock.advance(0.5)
    with pytest.raises(RedisReadSkipped):
        breaker.run(lambda: pytest.fail("Failed probe did not restart cooldown"))
    clock.advance(0.5)
    assert breaker.run(lambda: "recovered") == "recovered"


@pytest.mark.parametrize("late_failure", (False, True))
def test_old_result_cannot_change_a_new_state(late_failure):
    clock = FakeClock()
    breaker = RedisReadCircuitBreaker(clock=clock)
    entered = Event()
    release = Event()

    def old_read():
        entered.set()
        assert release.wait(timeout=5)
        if late_failure:
            connection_failure()
        return "Old operation succeeded"

    thread, outcomes = start_call(lambda: breaker.run(old_read))
    try:
        assert entered.wait(timeout=5)
        open_breaker(breaker)
        if late_failure:
            clock.advance(1)
            assert breaker.run(lambda: "new probe succeeded") == "new probe succeeded"
        expected_state = "closed" if late_failure else "open"
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    result, error = outcomes.get_nowait()
    if late_failure:
        assert isinstance(error, RedisConnectionError)
    else:
        assert result == "Old operation succeeded"
        assert error is None
    assert breaker.state == expected_state


@pytest.mark.parametrize("error", (ValueError("bad JSON"), UnicodeError("bad text"),
                                  ResponseError("Redis command error"),
                                  WaitTimeoutError("SingleFlight wait ended"),
                                  AuthenticationError("wrong credentials"),
                                  AuthorizationError("not authorized"),
                                  BusyLoadingError("server loading"),
                                  ExternalAuthProviderError("auth provider failed"),
                                  MaxConnectionsError("local connection pool full")))
def test_other_errors_in_closed_do_not_open_the_breaker(error):
    breaker = RedisReadCircuitBreaker(clock=FakeClock())

    def failed_operation():
        raise error

    with pytest.raises(type(error)) as raised:
        breaker.run(failed_operation)
    assert raised.value is error
    assert breaker.state == "closed"
    assert breaker.run(lambda: None) is None


@pytest.mark.parametrize("error", (ValueError("unexpected probe error"), KeyboardInterrupt()))
def test_aborted_probe_releases_qualification_without_claiming_recovery(error):
    clock = FakeClock()
    breaker = RedisReadCircuitBreaker(clock=clock)
    open_breaker(breaker)
    clock.advance(1)

    def interrupted_probe():
        raise error

    with pytest.raises(type(error)) as raised:
        breaker.run(interrupted_probe)
    assert raised.value is error
    assert breaker.state == "open"
    clock.advance(1)
    assert breaker.run(lambda: "next probe") == "next probe"
