"""Address resolution, target creation, allowlists and cross-parent matching (spec section 7.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import SlotSet
    from backend.generation.models import SlotTarget
    from backend.users.models import User

_PENDING = "WP-12: not implemented yet"


def preview(slot_set: SlotSet, target_in: dict[str, Any]) -> dict[str, Any]:
    """Hole, context, leak and cost hint of a prospective target."""
    raise NotImplementedError(_PENDING)


def create_target(slot_set: SlotSet, target_in: dict[str, Any], *, user: User | None) -> SlotTarget:
    """Create a target and queue its validation."""
    raise NotImplementedError(_PENDING)


def match(target: SlotTarget, slot_sets: list[SlotSet]) -> dict[str, Any]:
    """The same part (by ``match_key``) in other parents."""
    raise NotImplementedError(_PENDING)
