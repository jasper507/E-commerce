"""由支付适配层执行模拟退款；调用者负责先锁 Order 并开启事务。"""

from apps.payments.gateway import MockGateway


def simulate_refund(*, order, attempt, reason):
    return MockGateway().simulate_refund(
        order=order, attempt=attempt, reason=reason
    )
