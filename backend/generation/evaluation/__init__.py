"""EvalSuite v2: lock, build, start, tester, probes and adherence (spec section 8).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

_PENDING = "WP-09: not implemented yet"


def credentials(run: GenerationRun) -> dict[str, str]:
    """The runtime credentials record of a started sample (section 8.6), e.g. its real admin password."""
    raise NotImplementedError(_PENDING)
