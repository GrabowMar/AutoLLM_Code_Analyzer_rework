"""Single-shot slot calls: strict JSON output, no healing, no repair (spec sections 7.6 and D13).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-12 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-12: not implemented yet"


def run_stage(run: GenerationRun) -> None:
    """Generation stage of a ``slot_single_shot`` run; a parse failure is ``output_invalid``."""
    raise NotImplementedError(_PENDING)
