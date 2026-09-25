"""集中维护地址写操作，保证“有地址时恰好一个默认地址”的跨行规则。"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.models import Address


User = get_user_model()


@dataclass(frozen=True)
class AddressRuleError(Exception):
    """可预期的业务拒绝；code 供 API/前端识别，detail 供用户阅读。"""

    code: str
    detail: str


def _lock_user(user):
    """锁住同一用户的公共行，使其多个地址写请求按顺序执行。"""

    # 锁 Address 行不够：创建首地址时还没有行，两个请求会同时判断“没有默认”。
    # 所有地址写入口都先拿 User 行锁，数据库唯一约束再兜底“最多一个默认”。
    return User.objects.select_for_update().get(pk=user.pk)


def _owned_address(user, address_id):
    """只在当前用户的地址集合中查找，避免凭地址 ID 读取或修改他人数据。"""

    try:
        return Address.objects.select_for_update().get(
            pk=address_id,
            user=user,
        )
    except Address.DoesNotExist as exc:
        # 不区分“地址不存在”和“地址属于别人”，API 都返回 404。
        raise AddressRuleError(
            "address_not_found",
            "Address does not exist.",
        ) from exc


def _make_default(user, address):
    """在调用方的事务和 User 行锁下完成旧默认降级、新默认升级。"""

    # 先清旧值再写新值，避免中途触发 (user, default_marker) 唯一约束。
    # 两步都处于同一事务；后一步失败时前一步也会回滚。
    Address.objects.filter(user=user, default_marker=1).exclude(
        pk=address.pk
    ).update(default_marker=None)
    if address.default_marker != 1:
        address.default_marker = 1
        address.save(update_fields=("default_marker", "updated_at"))
    return address


@transaction.atomic
def create_address(*, user, is_default=False, **fields):
    """首地址自动设默认；后续地址只有明确要求时才替换旧默认。

    面试重点：事务保护整次切换，User 行锁串行化同一顾客的并发写入。
    即使两个“首地址”请求同时到达，也不会各自看到空地址后同时设默认。
    """

    locked_user = _lock_user(user)
    has_default = Address.objects.filter(
        user=locked_user,
        default_marker=1,
    ).exists()
    make_default = is_default or not has_default

    if make_default:
        # 显式设默认时先撤销旧默认；创建失败会连同撤销一起回滚。
        Address.objects.filter(
            user=locked_user,
            default_marker=1,
        ).update(default_marker=None)

    return Address.objects.create(
        user=locked_user,
        default_marker=1 if make_default else None,
        **fields,
    )


@transaction.atomic
def update_address(*, user, address_id, **changes):
    """修改自己的地址；不允许单独取消当前默认而不指定接任者。

    字段值由 Serializer 校验，Service 负责跨地址业务规则和归属校验。
    """

    locked_user = _lock_user(user)
    address = _owned_address(locked_user, address_id)
    requested_default = changes.pop("is_default", None)

    if requested_default is False and address.is_default:
        # 否则会出现“还有地址，但一个默认地址也没有”的不合法业务状态。
        raise AddressRuleError(
            "default_address_required",
            "Choose another default address before unsetting this one.",
        )

    for field, value in changes.items():
        setattr(address, field, value)

    update_fields = [*changes, "updated_at"]
    if changes:
        address.save(update_fields=update_fields)

    if requested_default is True:
        # 普通字段修改和默认切换属于同一事务，失败时整体回滚。
        _make_default(locked_user, address)

    return address


@transaction.atomic
def set_default_address(*, user, address_id):
    """把自己的指定地址设为默认；重复设置同一地址不会产生额外变化。"""

    locked_user = _lock_user(user)
    address = _owned_address(locked_user, address_id)
    return _make_default(locked_user, address)


@transaction.atomic
def delete_address(*, user, address_id):
    """删除自己的地址；删默认后让剩余地址中最早创建者接任。

    created_at 相同时再按 id 排序，接任规则稳定且可测试；删最后一个
    地址则允许回到“零地址、零默认”的状态。
    """

    locked_user = _lock_user(user)
    address = _owned_address(locked_user, address_id)
    was_default = address.is_default
    address.delete()

    if was_default:
        # 选接任者与删除在同一事务内，避免留下“有地址却无默认”的中间结果。
        replacement = (
            Address.objects.select_for_update()
            .filter(user=locked_user)
            .order_by("created_at", "id")
            .first()
        )
        if replacement is not None:
            replacement.default_marker = 1
            replacement.save(
                update_fields=("default_marker", "updated_at")
            )
