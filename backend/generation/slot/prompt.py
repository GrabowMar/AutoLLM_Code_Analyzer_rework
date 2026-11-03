"""Slot prompt and brief construction (spec section 7.5).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.contracts.slot import ResolvedContext
    from backend.generation.models import SlotTarget

_PENDING = "WP-12: not implemented yet"


def build_slot_context(
    target: SlotTarget,
    *,
    hole_mode: str,
    keep_docstring: bool,
    context: ResolvedContext,
) -> dict[str, Any]:
    """The render context of one slot prompt."""
    raise NotImplementedError(_PENDING)
