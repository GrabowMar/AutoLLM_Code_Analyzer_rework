"""Reuse of ``RunEvaluation`` rows by ``(tree_hash, suite, check selection)``.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

_PENDING = "WP-09: not implemented yet"


def find_reusable(
    run: GenerationRun,
    suite: EvalSuite,
    *,
    only_checks: list[str] | None = None,
) -> RunEvaluation | None:
    """A measured evaluation of an identical tree under the same suite and selection."""
    raise NotImplementedError(_PENDING)
