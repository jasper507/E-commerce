from django.contrib import admin

from apps.catalog.models import Category, Product, SKU
from apps.inventory.models import Inventory


class NoDeleteAdminMixin:
    """目录记录有订单等历史引用，不通过 Admin 直接硬删除。"""

    def has_delete_permission(self, request, obj=None):
        return False


class InventoryInline(admin.StackedInline):
    """新建 SKU 时同步填写它的一对一库存，避免出现不可管理的空库存。"""

    model = Inventory
    extra = 1
    min_num = 1
    max_num = 1
    validate_min = True
    can_delete = False
    fields = ("available_stock", "reserved_stock")
    readonly_fields = ("reserved_stock",)


@admin.register(Category)
class CategoryAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def get_readonly_fields(self, request, obj=None):
        return ("slug",) if obj else ()


@admin.register(Product)
class ProductAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("name", "category", "slug", "is_active")
    list_filter = ("category", "is_active")
    list_select_related = ("category",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def get_readonly_fields(self, request, obj=None):
        return ("slug",) if obj else ()


@admin.register(SKU)
class SKUAdmin(NoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("code", "product", "color", "price", "is_active")
    list_filter = ("product__category", "is_active")
    list_select_related = ("product",)
    search_fields = ("code", "product__name")
    inlines = (InventoryInline,)

    def has_add_permission(self, request):
        # 创建 SKU 会连带创建 Inventory，必须同时拥有两种新增权限。
        return super().has_add_permission(request) and request.user.has_perm(
            "inventory.add_inventory"
        )

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()
