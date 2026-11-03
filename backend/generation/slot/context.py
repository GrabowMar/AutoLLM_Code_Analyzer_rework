"""``resolve_context(target, context_policy) -> ResolvedContext`` (per plan, hashed; spec section 7.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.slot import ContextPolicy
    from backend.generation.contracts.slot import ResolvedContext
    from backend.generation.models import SlotTarget

_PENDING = "WP-12: not implemented yet"


def resolve_context(target: SlotTarget, context_policy: ContextPolicy) -> ResolvedContext:
    """The files and token counts a slot prompt shows."""
    raise NotImplementedError(_PENDING)
