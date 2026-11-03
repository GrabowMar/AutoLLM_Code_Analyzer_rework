"""Design validation (units, slot sets of one task, primary features, legacy shapes).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.issues import Issue
    from backend.generation.contracts.plan import Design
    from backend.users.models import User

_PENDING = "WP-10: not implemented yet"


def validate_design(design: Design, *, user: User | None = None) -> list[Issue]:
    """Registered issues of a design (``prose_variant_in_unit``, ``mixed_parents``, ...)."""
    raise NotImplementedError(_PENDING)
