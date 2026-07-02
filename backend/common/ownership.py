"""Shared ownership checks for API views."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import TypeVar

from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

if TYPE_CHECKING:
    from django.db.models import Model
    from django.db.models import QuerySet

M = TypeVar("M", bound="Model")


def get_owned_or_staff_or_403(
    source: type[M] | QuerySet[M],
    *,
    user: Any,
    owner_field: str = "owner",
    **lookup: Any,
) -> M:
    """Fetch an object that is shared-read but owner-or-staff-write.

    Raises 404 if the object doesn't exist and 403 if the requesting user is
    neither its owner nor staff. Use for mutations on objects whose reads are
    intentionally unrestricted (so a 403 doesn't leak anything new).

    *owner_field* may traverse relations with ``__`` (e.g. ``pipeline__owner``).
    """
    obj = get_object_or_404(source, **lookup)
    *path, final = owner_field.split("__")
    target: Any = obj
    for part in path:
        target = getattr(target, part)
    if getattr(target, f"{final}_id") == getattr(user, "id", None):
        return obj
    if getattr(user, "is_staff", False):
        return obj
    raise HttpError(403, "You can only modify your own objects.")
