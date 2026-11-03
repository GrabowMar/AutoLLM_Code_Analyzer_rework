"""``eval_level`` to stage toggles (spec section 4.8); skipped stages are reported as not measured.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.plan import EvaluationSpec

_PENDING = "WP-09: not implemented yet"


def stages_for(spec: EvaluationSpec) -> dict[str, bool]:
    """Which stages run for an evaluation spec."""
    raise NotImplementedError(_PENDING)
