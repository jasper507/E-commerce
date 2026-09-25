from django.contrib import admin, messages

from apps.ordering.fulfillment_services import FulfillmentRuleError, ship_order
from apps.ordering.models import Order, Shipment


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """订单只读展示；运营发货必须走受权限和状态机约束的 Service。"""

    list_display = ("public_id", "user", "status", "total", "currency", "created_at")
    list_filter = ("status",)
    search_fields = ("public_id", "user__username")
    readonly_fields = tuple(field.name for field in Order._meta.fields)
    actions = ("ship_selected_orders",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="模拟发货", permissions=("change",))
    def ship_selected_orders(self, request, queryset):
        # 逐单调用 Service，而不是批量 update(status=...) 绕过 PAID 校验和 Shipment 记录。
        shipped = 0
        already_shipped = 0
        skipped = 0
        for order_id in queryset.values_list("pk", flat=True):
            try:
                _, created = ship_order(order_id=order_id, actor=request.user)
            except FulfillmentRuleError:
                skipped += 1
            else:
                if created:
                    shipped += 1
                else:
                    already_shipped += 1
        self.message_user(
            request,
            f"模拟发货：新发货 {shipped} 单，已发货 {already_shipped} 单，跳过 {skipped} 单。",
            level=messages.SUCCESS if skipped == 0 else messages.WARNING,
        )


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    """只读审计发货事实，避免后台直接伪造或删除记录。"""

    list_display = ("order", "shipped_by", "shipped_at")
    readonly_fields = tuple(field.name for field in Shipment._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
