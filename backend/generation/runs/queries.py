"""Run queries over studies (spec sections 6.13 and 6.14): current runs, attrition.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.models import GenerationRun
    from backend.generation.models import Study
    from backend.generation.models import StudyCell

_PENDING = "WP-10: not implemented yet"


def current_runs(study: Study, stage: str = "main") -> QuerySet[GenerationRun]:
    """The current run of every cell: the **first usable** attempt (never the latest), else the latest attempt.

    ``beyond_plan`` runs are excluded.
    """
    raise NotImplementedError(_PENDING)


def runs_for_study(study: Study) -> QuerySet[GenerationRun]:
    """Every run of ``study`` (all attempts, including ``beyond_plan``)."""
    raise NotImplementedError(_PENDING)


def refresh_current(cell: StudyCell) -> GenerationRun | None:
    """Recompute ``cell.current_run``; called on every run outcome change. A usable current run never changes."""
    raise NotImplementedError(_PENDING)


def attrition(study: Study) -> list[dict[str, Any]]:
    """Per condition: planned cells, runs per outcome, usable, truncated by kind, excluded by reason, infra retries."""
    raise NotImplementedError(_PENDING)
