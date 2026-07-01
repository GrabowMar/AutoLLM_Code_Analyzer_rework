"""Shared helpers for statistics aggregations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet


def _scoped(
    qs: QuerySet[Any],
    user: AbstractBaseUser | None,
    field: str,
) -> QuerySet[Any]:
    if user is None or not getattr(user, "is_authenticated", False):
        return qs
    return qs.filter(**{field: user})


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)
