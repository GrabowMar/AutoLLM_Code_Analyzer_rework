"""v3 content contracts: the typed inter-WP interface (spec sections 3-9).

This package module holds the shared API shapes (``Issue``, ``Ref``, ``Page``,
``ErrorOut``). The per-area contracts live in the submodules and are imported
from there (``from backend.generation.contracts.library import TaskSpecContent``);
this module deliberately does not import them, so submodules can import it
without cycles.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from backend.generation.contracts._base import CONTRACT_CONFIG
from backend.generation.contracts._base import ContractModel
from backend.generation.contracts.issues import ISSUE_CODES
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import IssueFix
from backend.generation.contracts.issues import make_issue

__all__ = [
    "API_ERROR_CODES",
    "ISSUE_CODES",
    "PAGE_SIZE_MAX",
    "ContractModel",
    "ErrorOut",
    "Issue",
    "IssueFix",
    "Page",
    "Ref",
    "make_issue",
]

PAGE_SIZE_MAX: Final = 200

# Error ``code`` values the v3 API returns with 400/403/404/409/422 (section 9).
API_ERROR_CODES: Final = frozenset(
    {
        "address_stale",
        "cell_has_usable_run",
        "invalid",
        "locked",
        "not_found",
        "not_infra",
        "permission_denied",
        "plan_stale",
        "stale_base",
        "confirmation_required",
        "in_use",
        # wave 2 (CHANGES asks: WP-03 library, WP-08 runs, WP-10 plans)
        "hash_mismatch",
        "not_evaluable",
        "not_implemented",
        "run_active",
        "slug_exists",
        # wave 3: WP-10 study overrides (plan_conflict is gone: plans are unique per study)
        "prompt_set_ambiguous",
    },
)

_REF_RE: Final = re.compile(
    r"^(?P<slug>[A-Za-z0-9][A-Za-z0-9_-]*)@(?P<version>[1-9][0-9]*)(?:#(?P<hash>[0-9a-f]{12}))?$",
)


class Ref(ContractModel):
    """A library reference: ``slug@version`` or the pinned ``slug@version#<content_hash[:12]>``."""

    model_config = ConfigDict(**CONTRACT_CONFIG, frozen=True)

    slug: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=100)
    version: int = Field(gt=0)
    hash12: str | None = Field(default=None, pattern=r"^[0-9a-f]{12}$")

    @classmethod
    def parse(cls, value: str) -> Ref:
        match = _REF_RE.match(value or "")
        if match is None:
            msg = f"not a ref (slug@version[#hash12]): {value!r}"
            raise ValueError(msg)
        return cls(slug=match["slug"], version=int(match["version"]), hash12=match["hash"])

    @property
    def is_pinned(self) -> bool:
        return self.hash12 is not None

    @property
    def unpinned(self) -> str:
        return f"{self.slug}@{self.version}"

    def __str__(self) -> str:
        return f"{self.unpinned}#{self.hash12}" if self.hash12 else self.unpinned


class Page[T](BaseModel):
    """Paginated list response ``{items, total, page, page_size}`` (page_size max 200)."""

    model_config = CONTRACT_CONFIG

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=PAGE_SIZE_MAX)


class ErrorOut(ContractModel):
    """Error body ``{"detail", "code", "issues"?}`` used with 400, 403, 404, 409 and 422."""

    detail: str
    code: str
    issues: list[Issue] | None = None
