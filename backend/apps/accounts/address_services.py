from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.models import Address


User = get_user_model()


@dataclass(frozen=True)
class AddressRuleError(Exception):
    code: str
    detail: str


def _lock_user(user):
    return User.objects.select_for_update().get(pk=user.pk)


def _owned_address(user, address_id):
    try:
        return Address.objects.select_for_update().get(
            pk=address_id,
            user=user,
        )
    except Address.DoesNotExist as exc:
        raise AddressRuleError(
            "address_not_found",
            "Address does not exist.",
        ) from exc


def _make_default(user, address):
    Address.objects.filter(user=user, default_marker=1).exclude(
        pk=address.pk
    ).update(default_marker=None)
    if address.default_marker != 1:
        address.default_marker = 1
        address.save(update_fields=("default_marker", "updated_at"))
    return address


@transaction.atomic
def create_address(*, user, is_default=False, **fields):
    locked_user = _lock_user(user)
    has_default = Address.objects.filter(
        user=locked_user,
        default_marker=1,
    ).exists()
    make_default = is_default or not has_default

    if make_default:
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
    locked_user = _lock_user(user)
    address = _owned_address(locked_user, address_id)
    requested_default = changes.pop("is_default", None)

    if requested_default is False and address.is_default:
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
        _make_default(locked_user, address)

    return address


@transaction.atomic
def set_default_address(*, user, address_id):
    locked_user = _lock_user(user)
    address = _owned_address(locked_user, address_id)
    return _make_default(locked_user, address)


@transaction.atomic
def delete_address(*, user, address_id):
    locked_user = _lock_user(user)
    address = _owned_address(locked_user, address_id)
    was_default = address.is_default
    address.delete()

    if was_default:
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
