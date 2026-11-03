"""Random re-evaluation of a fraction of runs for test-retest agreement (spec section 8.5).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import Study

_PENDING = "WP-09: not implemented yet"


def retest_agreement(study: Study) -> dict[str, Any]:
    """``{n, check_agreement, run_agreement, kappa}``."""
    raise NotImplementedError(_PENDING)
