"""Launch, pilot expansion, top-up, clone and ad-hoc launches (spec sections 4.6, 6.14, 9.4).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.contracts.plan import Design
    from backend.generation.models import Study
    from backend.users.models import User

_PENDING = "WP-10: not implemented yet"


def launch(
    study: Study,
    plan_hash: str,
    *,
    confirm_usd: float | None = None,
    study_cap: float | str | None = None,
    pilot_override: bool = False,
) -> dict[str, Any]:
    """Create the planned runs of ``plan_hash`` (409 ``plan_stale`` if the design changed); side effects on commit."""
    raise NotImplementedError(_PENDING)


def expand_pilot(study: Study, *, override_gates: bool = False, reason: str | None = None) -> dict[str, Any]:
    """Pilot to main; reuses pilot cells as repeat 0 when allowed."""
    raise NotImplementedError(_PENDING)


def top_up(study: Study, cells: Any, *, reason: str) -> dict[str, Any]:
    """New attempts only for cells without a usable current run (6.14)."""
    raise NotImplementedError(_PENDING)


def clone(study: Study, *, mode: str, name: str, user: User | None) -> Study:
    """Copy a study as a draft (``exact`` or ``latest_versions``)."""
    raise NotImplementedError(_PENDING)


def launch_adhoc(design: Design, *, user: User | None, launch: bool = True) -> dict[str, Any]:
    """Resolve and launch in one call (automation, the slot picker's "Generate a parent app")."""
    raise NotImplementedError(_PENDING)
