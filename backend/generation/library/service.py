"""Library entity lifecycle (spec sections 3.3 and 9.1): create, version, archive, fork, usage, resolve.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-03 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.users.models import User

_PENDING = "WP-03: not implemented yet"


def create(
    kind: str,
    *,
    slug: str,
    name: str,
    content: dict[str, Any],
    description: str = "",
    user: User | None,
) -> Any:
    """Create version 1 of a user-owned entity of ``kind`` (content normalized and hashed by the model)."""
    raise NotImplementedError(_PENDING)


def new_version(
    entity: Any,
    *,
    base_version: int,
    content: dict[str, Any],
    name: str | None = None,
    description: str | None = None,
    user: User | None,
) -> Any:
    """Create version+1 from ``content``; a stale ``base_version`` is a 409, an unchanged hash returns ``entity``."""
    raise NotImplementedError(_PENDING)


def archive(entity: Any, *, archived: bool = True) -> Any:
    """Archive (or un-archive) one version; versions are never deleted."""
    raise NotImplementedError(_PENDING)


def fork(entity: Any, *, new_slug: str, name: str, user: User | None) -> Any:
    """Copy ``entity`` as v1 of a new user-owned slug (``derived_from`` set)."""
    raise NotImplementedError(_PENDING)


def fork_for_override(owner_kind: str, owner_ref: str, new_template: str, *, user: User | None) -> Any:
    """Save an inline prompt edit as a new user version of the entity that owns the text."""
    raise NotImplementedError(_PENDING)


def usage(entity: Any) -> dict[str, Any]:
    """``{studies: [...], runs: int}`` referencing this slug."""
    raise NotImplementedError(_PENDING)


def latest(kind: str, slug: str) -> Any:
    """The latest non-archived version of ``slug``."""
    raise NotImplementedError(_PENDING)


def resolve_ref(ref: str) -> Any:
    """The entity named by ``kind:slug@v`` / ``slug@v[#hash12]`` (a hash mismatch is an error)."""
    raise NotImplementedError(_PENDING)
