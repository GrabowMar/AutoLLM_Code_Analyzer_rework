"""Effective policy resolution, clamps, knob classification and policy lint (spec section 4.3).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-11 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.contracts.issues import Issue
    from backend.generation.contracts.resources import EffectivePolicy
    from backend.generation.contracts.resources import ResourcePolicyContent
    from backend.generation.models import ResourcePolicy
    from backend.generation.models import Study

_PENDING = "WP-11: not implemented yet"


def effective_policy(
    policy: ResourcePolicy,
    *,
    study: Study | None = None,
    model: Any = None,
    harness: str | None = None,
) -> EffectivePolicy:
    """The clamped policy for one cell, with notes and lint issues."""
    raise NotImplementedError(_PENDING)


def treatment_view(policy: EffectivePolicy) -> dict[str, Any]:
    """The class C and R knobs after clamping: ``generation_policy_hash = H(treatment_view(policy))``."""
    raise NotImplementedError(_PENDING)


def lint(content: ResourcePolicyContent) -> list[Issue]:
    """Policy lint issues (section 4.3)."""
    raise NotImplementedError(_PENDING)
