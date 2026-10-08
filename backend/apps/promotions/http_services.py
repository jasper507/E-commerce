"""秒杀 HTTP 的会话等待政策；订单事实仍由原业务事务负责。"""

import logging

from django.db import DatabaseError, connection

from apps.ordering.services import OrderRuleError
from apps.promotions.services import create_flash_sale_order


logger = logging.getLogger(__name__)
FLASH_SALE_LOCK_WAIT_SECONDS = 5


def _log_cleanup_error(action, error):
    number = error.args[0] if error.args and isinstance(error.args[0], int) else None
    # 不记录异常原文或 SQL；清理故障不能掩盖已提交结果或原业务异常。
    logger.warning("Flash-sale connection %s failed: %s, code=%s", action, type(error).__name__, number)


def _discard_connection():
    try:
        connection.close()
    except Exception as error:
        _log_cleanup_error("close", error)


def create_flash_sale_order_for_http(*, user, flash_sale_item_id, address_id, idempotency_key):
    """在业务事务外临时设置等待值，事务结束后恢复同一物理连接。"""

    if connection.in_atomic_block:
        raise RuntimeError("秒杀 HTTP 等待适配必须在业务事务外调用。")
    try:
        if not connection.get_autocommit():
            raise RuntimeError("秒杀 HTTP 等待适配需要 autocommit 连接。")
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@SESSION.innodb_lock_wait_timeout")
            original_wait = cursor.fetchone()[0]
            original_connection = connection.connection
            cursor.execute("SET SESSION innodb_lock_wait_timeout = %s", (FLASH_SALE_LOCK_WAIT_SECONDS,))
    except DatabaseError:
        # 设置结果无法确认时不继续下单，也不把此连接留给后续请求。
        _discard_connection()
        raise

    try:
        try:
            return create_flash_sale_order(
                user=user,
                flash_sale_item_id=flash_sale_item_id,
                address_id=address_id,
                idempotency_key=idempotency_key,
            )
        except DatabaseError as error:
            if not error.args or error.args[0] not in (1205, 1213):
                raise
            # 原 atomic 已退出；库存步骤已自行映射，其他下单 SQL 的锁竞争在此处理。
            raise OrderRuleError("checkout_busy", "下单正在处理，请重试原请求。", 409) from error
    finally:
        # 若连接已经丢弃或更换，不把旧会话值写到新连接上。
        if connection.connection is original_connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION innodb_lock_wait_timeout = %s", (original_wait,))
            except Exception as error:
                _log_cleanup_error("restore", error)
                _discard_connection()
