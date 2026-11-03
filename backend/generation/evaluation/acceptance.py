"""TaskSpec to ``AcceptancePlan`` compiler (spec section 8.1).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-09 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.generation.contracts.evaluation import AcceptancePlan
    from backend.generation.models import StackProfile
    from backend.generation.models import TaskSpecVersion

_PENDING = "WP-09: not implemented yet"


def compile(  # noqa: A001 - the spec's name
    task_version: TaskSpecVersion,
    auth_mode: str,
    stack: StackProfile,
) -> AcceptancePlan:
    """The tester plan of one (task, auth mode, stack)."""
    raise NotImplementedError(_PENDING)


def checks_for_endpoints(task_version: TaskSpecVersion, endpoint_ids: list[str]) -> list[str]:
    """Check ids linked to ``endpoint_ids`` (slot targets)."""
    raise NotImplementedError(_PENDING)
