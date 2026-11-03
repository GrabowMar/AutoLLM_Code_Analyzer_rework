"""Study stop and pause rules and pilot gates (spec section 4.6; evaluated after every run and every 60 s).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import Study

_PENDING = "WP-11: not implemented yet"


def evaluate(study: Study) -> list[dict[str, Any]]:
    """The rules that fired and the action each applied."""
    raise NotImplementedError(_PENDING)


def pilot_gates(study: Study) -> list[dict[str, Any]]:
    """``[{code, ok, value, threshold}]`` for the pilot gates."""
    raise NotImplementedError(_PENDING)
