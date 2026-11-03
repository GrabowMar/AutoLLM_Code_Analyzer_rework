"""Study results (port of ``experiment_results``): cells, comparability, gate, ITT, attrition, EMMs.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import Study

_PENDING = "WP-10: not implemented yet"


def study_results(
    study: Study,
    *,
    metric: str | None = None,
    fingerprint: str = "majority",
    sensitivity: str | None = None,
) -> dict[str, Any]:
    """The results payload of ``GET /studies/{id}/results/``; intention-to-treat by default."""
    raise NotImplementedError(_PENDING)
