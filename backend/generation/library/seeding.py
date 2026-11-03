"""``seed_library``: idempotent seeding of ``generation/data/`` (spec section 11.1).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-03 implements it.
"""

from __future__ import annotations

from typing import Any

_PENDING = "WP-03: not implemented yet"


def seed_library(*, using: str = "default") -> dict[str, Any]:
    """Seed every library kind: an equal content hash is a no-op, a new one creates version+1; user rows are kept."""
    raise NotImplementedError(_PENDING)
