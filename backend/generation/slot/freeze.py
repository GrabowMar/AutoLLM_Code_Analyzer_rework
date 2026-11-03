"""``SlotSet`` creation: freeze a parent (spec section 7.2).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotSet
    from backend.users.models import User

_PENDING = "WP-12: not implemented yet"


def freeze(
    parent_run: GenerationRun,
    *,
    name: str,
    user: User | None,
    selection: dict[str, Any] | None = None,
    allow_unbuilt_parent: bool = False,
) -> SlotSet:
    """Freeze a usable ``app_agentic`` run as a slot set (index built, tree hash pinned)."""
    raise NotImplementedError(_PENDING)
