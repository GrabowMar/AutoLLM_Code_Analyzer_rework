"""Experiment results: leaderboard, app-blocked paired comparisons, matrix (spec §F.1). Contract stub → Track C.

The single implementation behind the Results tab, ``ResultsMatrix`` (via
``results.matrix``) and the auto-generated ``experiment_report``. The unit of
analysis is the app: repeats collapse to a per-(condition, app) mean; pairs
are keyed on ``app_id``; CIs are cluster bootstraps over apps; sign and
Wilcoxon signed-rank tests are Holm-adjusted vs the baseline; p-values are
null while the experiment is not terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import Experiment
    from backend.generation.models import ExperimentCondition


@dataclass(frozen=True)
class ResultFilters:
    metric: str = "pass_rate"
    exclude_invalid: bool = False
    exclude_truncated: bool = True
    common_cells: bool = True
    fingerprint: str = "all"  # "all" | "majority"


def compute(
    experiment: Experiment,
    baseline: ExperimentCondition | int | None,
    filters: ResultFilters | None,
    user,
) -> dict[str, Any]:
    """The ``ExperimentResults`` payload (§F.1) for ``experiment``.

    ``baseline`` None → the pre-registered ``experiment.baseline_condition``
    (``baseline_source="preregistered"``); any other condition →
    ``"post_hoc"``. ``user`` scopes owner-only fields. O(cells): one
    ``values()`` query over current-attempt jobs.
    """
    msg = "experiment_results.compute: Track C"
    raise NotImplementedError(msg)
