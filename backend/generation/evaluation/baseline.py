"""Starter baselines, oracle validation (3x) and probe mutants (spec section 8.5).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import EvalBaseline
    from backend.generation.models import EvalSuite
    from backend.generation.models import Starter
    from backend.generation.models import TaskSpecVersion

_PENDING = "WP-09: not implemented yet"


def ensure_baseline(starter: Starter, task_version: TaskSpecVersion, auth_mode: str, suite: EvalSuite) -> EvalBaseline:
    """The stored starter baseline, evaluating the bare starter when missing."""
    raise NotImplementedError(_PENDING)
