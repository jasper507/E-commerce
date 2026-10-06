"""真实 kill 窗口观察器：只用于隔离 Worker，不替换业务异常或返回值。"""

import logging
import os
import time
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection
from celery.signals import task_prerun

from config.celery import app


logger = logging.getLogger(__name__)


def pause_for_kill(event, window, *, atomic):
    if connection.in_atomic_block != atomic:
        raise RuntimeError("crash_experiment_wrong_transaction_window")
    logger.info("Payment crash experiment paused: inbox_id=%s window=%s atomic=%s",
                event.pk, window, atomic)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(0.05)
    # 没有实际 kill 时失败，让实验不能悄悄继续并误认窗口成立。
    raise RuntimeError("crash_experiment_kill_not_observed")


def observe_delivery(sender, task_id, task, args, kwargs, **extra):
    if task.name == "payments.process_event" and args and type(args[0]) is int:
        logger.info("Payment crash experiment delivery: inbox_id=%s task_id=%s redelivered=%s",
                    args[0], task_id, bool(task.request.delivery_info.get("redelivered")))


def main():
    broker = urlsplit(settings.CELERY_BROKER_URL)
    if (
        os.getenv("PROJECT_ONE_MQ_CRASH_TEST_WORKER") != "true"
        or settings.ENVIRONMENT != "test"
        or settings.DATABASES["default"]["NAME"] != "test_project_one"
        or broker.hostname != "rabbitmq-test"
        or broker.path != "/project_one_test"
        or settings.CELERY_TASK_DEFAULT_QUEUE != "payment-events-test"
        or settings.PAYMENT_EVENT_PROCESSING_LEASE_SECONDS != 8
    ):
        raise RuntimeError("Crash experiment requires the isolated profile and test lease.")
    import django
    django.setup()
    from apps.payments import services

    original_claim = services._claim_event_processing
    original_process = services.process_payment_event
    original_claimed = services._process_claimed_payment_event

    def observe_claim(*, event_id, as_of):
        event, version = original_claim(event_id=event_id, as_of=as_of)
        if version is not None:
            logger.info("Payment crash experiment claim: inbox_id=%s count=%s lease_until=%s",
                        event.pk, version, event.processing_lease_until.isoformat())
        if version == 1 and event.event_id.startswith("crash-claim-"):
            pause_for_kill(event, "claim", atomic=False)
        return event, version

    def observe_transaction(*, event_id):
        event = original_process(event_id=event_id)
        if event.processing_attempt_count == 1 and event.event_id.startswith("crash-transaction-"):
            pause_for_kill(event, "transaction", atomic=True)
        return event

    def observe_committed(*, event_id, processing_attempt):
        event = original_claimed(event_id=event_id, processing_attempt=processing_attempt)
        if processing_attempt == 1 and event.event_id.startswith("crash-commit-"):
            pause_for_kill(event, "commit", atomic=False)
        return event

    services._claim_event_processing = observe_claim
    services.process_payment_event = observe_transaction
    services._process_claimed_payment_event = observe_committed
    task_prerun.connect(observe_delivery, weak=False)
    app.worker_main(["worker", "--loglevel=INFO", "--pool=prefork", "--concurrency=1",
                     "--queues=payment-events-test"])


if __name__ == "__main__":
    main()
