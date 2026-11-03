"""Cost and time estimation, fit-to-budget and the ``auto`` cap (spec section 4.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.plan import CellEstimate
    from backend.generation.contracts.plan import Estimate
    from backend.generation.contracts.plan import ResolvedCell
    from backend.generation.contracts.plan import ResolvedPlan

_PENDING = "WP-11: not implemented yet"


def estimate_cell(cell: ResolvedCell) -> CellEstimate:
    """Prior-based (or heuristic) estimate of one cell."""
    raise NotImplementedError(_PENDING)


def estimate_plan(plan: ResolvedPlan) -> Estimate:
    """Totals, by condition and by level, with the fit."""
    raise NotImplementedError(_PENDING)
