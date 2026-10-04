"""隔离实验专用 Worker；真实锁等待故障不改变全局 MySQL 或生产连接设置。"""

import os
import logging
import socket
import time
from urllib.parse import urlsplit

from django.conf import settings
from django.db.backends.signals import connection_created
from django.db import OperationalError

from config.celery import app


def observe_database_error(execute, sql, params, many, context):
    try:
        return execute(sql, params, many, context)
    except OperationalError as error:
        if error.args and isinstance(error.args[0], int):
            logging.getLogger(__name__).warning("Payment retry experiment MySQL errno=%s", error.args[0])
        raise


def configure_test_connection(sender, connection, **kwargs):
    if connection.vendor != "mysql" or connection.settings_dict["NAME"] != "test_project_one":
        raise RuntimeError("Retry experiment refuses a non-test database connection.")
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION innodb_lock_wait_timeout = 1")
    if observe_database_error not in connection.execute_wrappers:
        connection.execute_wrappers.append(observe_database_error)


def main():
    broker = urlsplit(settings.CELERY_BROKER_URL)
    if (
        os.getenv("PROJECT_ONE_MQ_RETRY_TEST_WORKER") != "true"
        or settings.ENVIRONMENT != "test"
        or settings.DATABASES["default"]["NAME"] != "test_project_one"
        or broker.hostname != "rabbitmq-test"
        or broker.path != "/project_one_test"
        or settings.CELERY_TASK_DEFAULT_QUEUE != "payment-events-test"
    ):
        raise RuntimeError("Retry experiment requires the isolated MQ profile.")
    connection_created.connect(configure_test_connection, weak=False)
    import django
    django.setup()
    from apps.payments import services

    original_claim = services._claim_event_processing

    def observe_claim(*, event_id, as_of):
        event, version = original_claim(event_id=event_id, as_of=as_of)
        if version is not None:
            logging.getLogger(__name__).info(
                "Payment retry experiment claim: inbox_id=%s count=%s as_of=%s",
                event_id, version, as_of.isoformat(),
            )
        elif event.processing_status == "ERROR" and event.failure_kind == "TRANSIENT":
            logging.getLogger(__name__).info(
                "Payment retry experiment deferred: inbox_id=%s count=%s",
                event_id, event.processing_attempt_count,
            )
        return event, version

    # 只观察原资格函数的输入与结果，不改变时间、状态或异常。
    services._claim_event_processing = observe_claim
    if os.getenv("PROJECT_ONE_MQ_RECOVERY_PUBLICATION_WINDOW") == "true":
        from apps.payments import tasks

        original_publish = tasks.publish_payment_event

        def wait_for_broker_outage(*, event_id, eta=None):
            if eta is not None:
                # 仅协调真实故障窗口；最终仍调用原发布器，不伪造发布结果。
                logging.getLogger(__name__).info(
                    "Payment recovery experiment waiting: inbox_id=%s", event_id
                )
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with socket.create_connection(("rabbitmq-test", 5672), timeout=0.2):
                            pass
                    except OSError:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("recovery_experiment_outage_not_observed")
                    time.sleep(0.05)
            return original_publish(event_id=event_id, eta=eta)

        tasks.publish_payment_event = wait_for_broker_outage
    app.worker_main([
        "worker", "--loglevel=INFO", "--pool=prefork", "--concurrency=1",
        "--queues=payment-events-test",
    ])


if __name__ == "__main__":
    main()
