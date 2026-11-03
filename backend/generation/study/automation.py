"""Study schedules and post-runs (port of ``experiment_automation``; slot studies auto-attach a patterns post-run).

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-10 implements it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from backend.generation.models import StudyPostRun

_PENDING = "WP-10: not implemented yet"


def fire_due_schedules(now: datetime | None = None) -> int:
    """Fire every due ``StudySchedule``; returns the number fired."""
    raise NotImplementedError(_PENDING)


def run_post_run(post_run: StudyPostRun) -> None:
    """Execute one leased post-run (analysis then report, or pattern analysis)."""
    raise NotImplementedError(_PENDING)
