"""Parent pool queries and ``parent_selection`` (random, all or manual; spec section 7.2).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.contracts.slot import ParentSelection
    from backend.generation.contracts.slot import PoolQuery
    from backend.generation.models import GenerationRun

_PENDING = "WP-12: not implemented yet"


def pool(query: PoolQuery) -> QuerySet[GenerationRun]:
    """Eligible parents: usable ``app_agentic`` runs with outcome ``tested``."""
    raise NotImplementedError(_PENDING)


def draw(selection: ParentSelection) -> list[GenerationRun]:
    """Reproducible (seeded, stratified) draw from the pool."""
    raise NotImplementedError(_PENDING)
