"""Shared Ninja view helpers (response envelopes, error mapping)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from llm_lab.common.exceptions import APIError


def paginated_response(
    items: Iterable[Any],
    total: int,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    """Return a standard paginated envelope.

    Keys: ``items``, ``total``, ``page``, ``pages``.
    """
    pages = max(1, (total + per_page - 1) // per_page) if per_page > 0 else 1
    return {
        "items": list(items),
        "total": total,
        "page": page,
        "pages": pages,
    }


def error_response(exc: APIError) -> tuple[int, dict[str, Any]]:
    """Map an :class:`APIError` to a Ninja ``(status_code, body)`` tuple."""
    return exc.status_code, {
        "error": exc.__class__.__name__,
        "detail": exc.message or str(exc),
    }
