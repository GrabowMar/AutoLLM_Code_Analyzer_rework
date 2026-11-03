"""EvalSuite v2 orchestration: lock, build, start, tester, adherence (spec section 6.11).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import EvalSuite
    from backend.generation.models import GenerationRun
    from backend.generation.models import RunEvaluation

_PENDING = "WP-09: not implemented yet"


def evaluate(
    run: GenerationRun,
    suite: EvalSuite,
    *,
    only_checks: list[str] | None = None,
    probes: list[str] | None = None,
    mode: str = "full",
    image_override: str | None = None,
    overlay_files: dict[str, str] | None = None,
) -> RunEvaluation:
    """Evaluate ``run`` under ``suite`` into a new (append-only) ``RunEvaluation``."""
    raise NotImplementedError(_PENDING)


def run_eval_stage(run: GenerationRun) -> None:
    """The eval worker's entry point (claimed by phase ``eval_queued``)."""
    raise NotImplementedError(_PENDING)
