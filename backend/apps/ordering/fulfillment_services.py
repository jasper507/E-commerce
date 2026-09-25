from dataclasses import dataclass

from django.db import transaction

from apps.ordering.models import Order, Shipment


@dataclass(frozen=True)
class FulfillmentRuleError(Exception):
    """履约业务错误：API 或 Admin 负责将其转换成用户可见的结果。"""

    code: str
    detail: str
    http_status: int


@transaction.atomic
def ship_order(*, order_id, actor):
    """管理员模拟发货：只允许已支付订单进入已发货状态。

    先检查 Django 后端权限，再锁订单行并确认状态；发货事实 Shipment 与订单
    状态在同一事务内写入。即使后台误选多单或重复执行，已有发货记录的 SHIPPED
    订单只返回原结果，不会再次创建发货记录。任何写入失败都整体回滚。

    面试重点：隐藏前端按钮不是权限控制；一单一条 Shipment 的数据库唯一关系
    和状态检查共同约束重复发货，逐单调用 Service 也避免 Admin 批量更新绕过规则。
    """

    if not actor.is_staff or not actor.has_perm("ordering.change_order"):
        raise FulfillmentRuleError(
            "shipping_forbidden", "只有具备订单修改权限的管理员才能模拟发货。", 403
        )

    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if order is None:
        raise FulfillmentRuleError("order_not_found", "订单不存在。", 404)
    if order.status == Order.Status.SHIPPED and Shipment.objects.filter(order=order).exists():
        return order, False
    if order.status != Order.Status.PAID:
        raise FulfillmentRuleError(
            "order_not_paid", "只有已支付的订单才能模拟发货。", 409
        )

    Shipment.objects.create(order=order, shipped_by=actor)
    order.status = Order.Status.SHIPPED
    order.save(update_fields=("status", "updated_at"))
    return order, True


@transaction.atomic
def confirm_receipt(*, public_id, user):
    """顾客确认自己的订单已收货，使 SHIPPED 进入 COMPLETED。

    查询同时限制 public_id 与 user，别人的订单统一表现为不存在；锁订单行后
    检查真实发货记录，防止仅靠状态字段误确认。已完成的重复请求返回原订单；
    未发货或支付未成功的订单返回冲突。状态写入失败时事务回滚。
    """

    order = Order.objects.select_for_update().filter(
        public_id=public_id, user=user
    ).first()
    if order is None:
        raise FulfillmentRuleError("order_not_found", "订单不存在，或你无权查看这张订单。", 404)
    if order.status == Order.Status.COMPLETED:
        return order, False
    if order.status != Order.Status.SHIPPED or not Shipment.objects.filter(order=order).exists():
        raise FulfillmentRuleError(
            "order_not_shipped", "只有已发货的订单才能确认收货。", 409
        )

    order.status = Order.Status.COMPLETED
    order.save(update_fields=("status", "updated_at"))
    return order, True
