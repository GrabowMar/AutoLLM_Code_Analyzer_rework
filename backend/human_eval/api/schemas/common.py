"""Schemas and helpers shared by every human-eval API module.

- :class:`HumanEvalErrorSchema` — the structured error body (TS ``HumanEvalError``)
  that ``config/api.py`` renders for ``HumanEvalServiceError``; list it in
  ``response=`` maps via :func:`error_responses` so OpenAPI documents it.
- :func:`paginated` — the TS ``Paginated<T>`` envelope schema for an item schema
  (one cached class per item schema, so OpenAPI names stay unique).
- :func:`paginate` — slice a queryset or list into that envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from typing import Any

from django.db.models import QuerySet
from ninja import Schema
from pydantic import create_model

from backend.common.pagination import paginate_queryset

PER_PAGE_DEFAULT = 20
PER_PAGE_MAX = 100


class FieldErrorSchema(Schema):
    path: str
    message: str


class HumanEvalErrorSchema(Schema):
    detail: str
    code: str
    errors: list[FieldErrorSchema] = []


def error_responses(*statuses: int) -> dict[int, type[Schema]]:
    """``{status: HumanEvalErrorSchema}`` for ``response=`` maps (default 400, 404, 409)."""
    return dict.fromkeys(statuses or (400, 404, 409), HumanEvalErrorSchema)


_PAGINATED: dict[type, type[Schema]] = {}


def paginated(item_schema: type) -> type[Schema]:
    """The ``Paginated<item>`` schema: ``{items: item[], total, page, per_page, pages}``."""
    cached = _PAGINATED.get(item_schema)
    if cached is not None:
        return cached
    model = create_model(
        f"Paginated{item_schema.__name__}",
        __base__=Schema,
        __module__=__name__,
        items=(list[item_schema], ...),
        total=(int, ...),
        page=(int, ...),
        per_page=(int, ...),
        pages=(int, ...),
    )
    _PAGINATED[item_schema] = model
    return model


def paginate(
    rows: QuerySet[Any] | Sequence[Any],
    page: int = 1,
    per_page: int = PER_PAGE_DEFAULT,
    *,
    serialize: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """The ``Paginated<T>`` envelope of one page (page clamped to 1..pages, per_page to 1..100).

    ``serialize`` maps each row of the page (e.g. a model instance to its payload dict).
    """
    per_page = max(1, min(int(per_page or PER_PAGE_DEFAULT), PER_PAGE_MAX))
    if isinstance(rows, QuerySet):
        page_rows, total, page, pages = paginate_queryset(rows, int(page or 1), per_page)
        items = list(page_rows)
    else:
        total = len(rows)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(int(page or 1), pages))
        start = (page - 1) * per_page
        items = list(rows[start : start + per_page])
    if serialize is not None:
        items = [serialize(item) for item in items]
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": pages}
