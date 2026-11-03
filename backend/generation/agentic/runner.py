"""Stage entry point for kind ``app_agentic`` (spec sections 6.4 to 6.9).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-08 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-08: not implemented yet"


def run_app_agentic(run: GenerationRun) -> None:
    """Seed the workspace, run the harness in the sandbox, collect and record one attempt."""
    raise NotImplementedError(_PENDING)
