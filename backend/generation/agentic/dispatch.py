"""``GenerationRun`` to stage-function dispatcher (replaces the v2 orchestrator); the executor's only entry point.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-08: not implemented yet"


def run_stage(run: GenerationRun) -> None:
    """Run the generation stage of ``run`` (``app_agentic``, ``slot_agent``, ``slot_single_shot``)."""
    raise NotImplementedError(_PENDING)
