"""Target validation: original 3x, holed tree, discriminating checks (spec section 7.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.slot import TargetValidation
    from backend.generation.models import SlotTarget

_PENDING = "WP-12: not implemented yet"


def validate_target(target: SlotTarget) -> TargetValidation:
    """Keep only the linked checks that discriminate; none left is ``uninformative``."""
    raise NotImplementedError(_PENDING)
