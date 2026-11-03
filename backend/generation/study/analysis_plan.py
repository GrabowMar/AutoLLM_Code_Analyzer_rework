"""Pre-registered analysis plan (port of ``services/analysis_plan.py`` plus patterns, sequential designs, ITT).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import Any

_PENDING = "WP-10: not implemented yet"


def normalize_analysis_plan(plan: dict[str, Any] | None, *, kind: str) -> dict[str, Any]:
    """The plan with defaults filled (``itt: true``); confirmatory studies require one."""
    raise NotImplementedError(_PENDING)


def analysis_plan_hash(plan: dict[str, Any]) -> str:
    """``H(normalized plan)``."""
    raise NotImplementedError(_PENDING)
