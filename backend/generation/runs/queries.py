"""Run queries over studies (spec sections 6.13 and 6.14): current runs, attrition.

**Current run of a cell** = the **first** run (by ``created_at``) with ``usable=True``; when no attempt is
usable, the latest attempt (so attrition stays visible; consumers filter ``usable``). ``beyond_plan`` runs
are never current of a planned cell. A usable current run is **never superseded**: :func:`refresh_current`
keeps a stored usable ``StudyCell.current_run``, and :func:`current_runs` prefers it over a recomputation, so
retries, top-ups and re-evaluations cannot swap the analysed sample for a better attempt.

:func:`current_runs` computes the selection in SQL (it does not trust ``StudyCell.current_run`` to have been
refreshed, but honours a usable stored value). It replaces the temporary ``runs.view._current_runs_tmp``.

Cells are those of the study's active plan (pilot and main plans with different ``plan_hash`` values are
never pooled); a study without an active plan uses every cell of the stage. Replicate cells added after launch
(``StudyCell.beyond_plan``, from ``add_replicates``) are left out of the planned sample by default; their own
current run and state are maintained from their (``beyond_plan``) runs, so they never look unrun.
"""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.db.models import OuterRef
from django.db.models import Subquery
from django.db.models.functions import Coalesce

from backend.generation.models import GenerationRun
from backend.generation.models import StudyCell
from backend.generation.models.choices import CellExclusionReason
from backend.generation.models.choices import CellState
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from backend.generation.models import Study

__all__ = [
    "attrition",
    "cell_current_ids",
    "cells_for_stage",
    "current_runs",
    "refresh_current",
    "runs_for_study",
]

# Run outcome reasons that map onto a cell exclusion (CellExclusionReason).
_CELL_REASONS = frozenset(CellExclusionReason.values)
_ACTIVE = (RunStatus.PENDING, RunStatus.RUNNING)


def cells_for_stage(
    study: Study,
    stage: str | None = "main",
    *,
    beyond_plan: bool | None = False,
) -> QuerySet[StudyCell]:
    """The cells of ``study`` (its active plan when set) in ``stage`` (``None``: every stage).

    ``beyond_plan=False`` (default): the planned sample only; ``True``: only replicate cells added after
    launch; ``None``: both.
    """
    cells = StudyCell.objects.filter(study=study)
    if study.active_plan_id is not None:
        cells = cells.filter(plan_id=study.active_plan_id)
    if stage is not None:
        cells = cells.filter(stage=stage)
    if beyond_plan is not None:
        cells = cells.filter(beyond_plan=beyond_plan)
    return cells


def _cell_runs() -> QuerySet[GenerationRun]:
    # A planned cell counts its in-plan runs only; a replicate cell (all of its runs are beyond_plan) its own.
    return GenerationRun.objects.filter(cell=OuterRef("pk"), beyond_plan=OuterRef("beyond_plan"))


def cell_current_ids(cells: QuerySet[StudyCell]) -> QuerySet[StudyCell]:
    """``cells`` annotated with ``current_id``: the stored usable current run, else first usable, else latest."""
    stored_usable = GenerationRun.objects.filter(
        pk=OuterRef("current_run"),
        cell=OuterRef("pk"),
        usable=True,
        beyond_plan=OuterRef("beyond_plan"),
    ).values("pk")[:1]
    first_usable = _cell_runs().filter(usable=True).order_by("created_at", "pk").values("pk")[:1]
    latest = _cell_runs().order_by("-created_at", "-pk").values("pk")[:1]
    return cells.annotate(current_id=Coalesce(Subquery(stored_usable), Subquery(first_usable), Subquery(latest)))


def current_runs(study: Study, stage: str = "main") -> QuerySet[GenerationRun]:
    """The current run of every cell of ``stage``: the **first usable** attempt (never the latest), else the latest.

    ``beyond_plan`` runs and replicate cells are excluded. Cells without any run contribute nothing.
    """
    ids = cell_current_ids(cells_for_stage(study, stage)).exclude(current_id=None).values("current_id")
    return GenerationRun.objects.filter(pk__in=ids)


def runs_for_study(study: Study) -> QuerySet[GenerationRun]:
    """Every run of ``study`` (all attempts, including ``beyond_plan`` and runs of superseded plans)."""
    return GenerationRun.objects.filter(study=study)


def _cell_state(run: GenerationRun | None, cell: StudyCell) -> tuple[str, str]:  # noqa: PLR0911 - one return per state
    """``(state, exclusion_reason)`` of a cell from its current run."""
    if run is None:
        if cell.state == CellState.EXCLUDED:
            return cell.state, cell.exclusion_reason
        return CellState.PLANNED, ""
    if run.status == RunStatus.PENDING:
        return (CellState.QUEUED if run.queued_at is not None else CellState.PLANNED), ""
    if run.status == RunStatus.RUNNING:
        return CellState.RUNNING, ""
    if run.usable:
        return CellState.DONE, ""
    if run.outcome == Outcome.EXCLUDED and run.outcome_reason in _CELL_REASONS:
        return CellState.EXCLUDED, run.outcome_reason
    if run.outcome == Outcome.INFRA_ERROR or run.status == RunStatus.FAILED:
        return CellState.INFRA_PENDING, ""
    if run.status == RunStatus.CANCELLED:
        return CellState.EXCLUDED, CellExclusionReason.CANCELLED
    # Terminal and not usable for a reason that is not a cell exclusion (e.g. excluded:resource_limit).
    return CellState.DONE, ""


def refresh_current(cell: StudyCell) -> GenerationRun | None:
    """Recompute ``cell.current_run`` and ``cell.state``; called on every run outcome change.

    A usable current run never changes (6.14). Runs inside one cell are locked in creation order, so two
    concurrent refreshes of one cell serialise on the cell row.
    """
    with transaction.atomic():
        locked = StudyCell.objects.select_for_update().filter(pk=cell.pk).first()
        if locked is None:
            return None
        runs = list(
            GenerationRun.objects.filter(cell=locked, beyond_plan=locked.beyond_plan)
            .order_by("created_at", "pk")
            .only("pk", "status", "outcome", "outcome_reason", "usable", "queued_at", "created_at"),
        )
        stored = next((run for run in runs if run.pk == locked.current_run_id), None)
        if stored is not None and stored.usable:
            current = stored
        else:
            current = next((run for run in runs if run.usable), runs[-1] if runs else None)
        state, reason = _cell_state(current, locked)
        changed = []
        if locked.current_run_id != (current.pk if current is not None else None):
            locked.current_run = current
            changed.append("current_run")
        if (locked.state, locked.exclusion_reason) != (state, reason):
            locked.state, locked.exclusion_reason = state, reason
            changed += ["state", "exclusion_reason"]
        if changed:
            locked.save(update_fields=changed)
    cell.current_run_id, cell.state, cell.exclusion_reason = (
        locked.current_run_id,
        locked.state,
        locked.exclusion_reason,
    )
    return current


def _truncation_kind(validity: Any) -> str | None:
    if isinstance(validity, dict) and validity.get("truncated"):
        return str(validity.get("termination") or "unknown")
    return None


def attrition(study: Study, stage: str = "main") -> list[dict[str, Any]]:
    """Per condition: planned cells, runs per outcome, usable, truncated by kind, excluded by reason, infra retries.

    Rows also give the share of sandbox time spent installing dependencies per stack (from the attempts'
    ``time_split``) and the attrition rate (excluded + infra over planned cells), which feed the
    ``exclusion_rate`` / ``differential_attrition`` gate deviations (study/results.py).
    """
    from backend.generation.models import RunAttempt
    from backend.generation.models import StudyCondition
    from backend.generation.models import StudyPlan

    cells = list(
        cell_current_ids(cells_for_stage(study, stage)).values(
            "pk",
            "condition_id",
            "state",
            "exclusion_reason",
            "current_id",
        ),
    )
    planned_by_condition: dict[int, int] = Counter()
    if study.active_plan_id is not None:
        plan = StudyPlan.objects.filter(pk=study.active_plan_id).first()
        planned_by_condition = _planned_counts(plan, stage) if plan is not None else Counter()
    current_ids = [c["current_id"] for c in cells if c["current_id"]]
    runs = {
        run["pk"]: run
        for run in GenerationRun.objects.filter(pk__in=current_ids).values(
            "pk",
            "outcome",
            "outcome_reason",
            "usable",
            "validity",
            "attempt",
            "status",
            "stack_profile__slug",
        )
    }
    all_runs = GenerationRun.objects.filter(cell__in=[c["pk"] for c in cells])
    # Beyond-plan runs: top-ups of planned cells plus every run of the replicate cells added after launch.
    beyond = Counter(
        GenerationRun.objects.filter(
            cell__in=cells_for_stage(study, stage, beyond_plan=None),
            beyond_plan=True,
        ).values_list("condition_id", flat=True),
    )
    attempts = Counter(all_runs.filter(beyond_plan=False).values_list("condition_id", flat=True))
    install: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for row in RunAttempt.objects.filter(run_id__in=current_ids).values(
        "run__condition_id",
        "run__stack_profile__slug",
        "time_split",
    ):
        split = row["time_split"] if isinstance(row["time_split"], dict) else {}
        total = sum(float(split.get(k) or 0.0) for k in ("install_s", "llm_s", "tool_s", "idle_s"))
        bucket = install[row["run__condition_id"]][row["run__stack_profile__slug"] or "-"]
        bucket[0] += float(split.get("install_s") or 0.0)
        bucket[1] += total
    labels = dict(StudyCondition.objects.filter(study=study).values_list("pk", "label"))
    by_condition: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        by_condition[cell["condition_id"]].append(cell)
    rows = []
    for condition_id in sorted(set(by_condition) | set(planned_by_condition)):
        mine = by_condition.get(condition_id, [])
        outcomes: Counter[str] = Counter()
        truncated: Counter[str] = Counter()
        excluded: Counter[str] = Counter()
        usable = infra = retries = no_run = 0
        for cell in mine:
            run = runs.get(cell["current_id"])
            if run is None:
                if cell["state"] == CellState.EXCLUDED:
                    excluded[cell["exclusion_reason"] or "excluded"] += 1
                else:
                    no_run += 1
                continue
            outcome = run["outcome"] or (run["status"] if run["status"] in _ACTIVE else "none")
            outcomes[outcome] += 1
            retries += max(0, int(run["attempt"] or 0) - 1)
            if run["usable"]:
                usable += 1
                kind = _truncation_kind(run["validity"])
                if kind:
                    truncated[kind] += 1
            elif run["outcome"] == Outcome.EXCLUDED:
                excluded[run["outcome_reason"] or "excluded"] += 1
            elif run["outcome"] == Outcome.INFRA_ERROR:
                infra += 1
        planned = planned_by_condition.get(condition_id) or len(mine)
        lost = sum(excluded.values()) + infra
        rows.append(
            {
                "condition_id": condition_id,
                "label": labels.get(condition_id, ""),
                "planned_cells": planned,
                "cells": len(mine),
                "cells_without_run": no_run,
                "runs_by_outcome": dict(sorted(outcomes.items())),
                "usable": usable,
                "truncated": sum(truncated.values()),
                "truncated_by_kind": dict(sorted(truncated.items())),
                "excluded": sum(excluded.values()),
                "excluded_by_reason": dict(sorted(excluded.items())),
                "infra_error": infra,
                "infra_retries": retries,
                "cell_attempts": attempts.get(condition_id, 0),
                "beyond_plan_runs": beyond.get(condition_id, 0),
                "attrition_rate": round(lost / planned, 4) if planned else None,
                "install_share_by_stack": {
                    stack: (round(spent / total, 4) if total else None)
                    for stack, (spent, total) in sorted(install.get(condition_id, {}).items())
                },
            },
        )
    return rows


def _planned_counts(plan: Any, stage: str) -> Counter[int]:
    """Planned cells per ``StudyCondition`` id of ``plan`` in ``stage`` (from the plan payload)."""
    from backend.generation.models import StudyCondition
    from backend.generation.study.plan import load_bundle

    by_index = dict(StudyCondition.objects.filter(plan=plan).values_list("index", "pk"))
    counts: Counter[int] = Counter()
    try:
        payload = load_bundle(plan).plan
    except Exception:  # noqa: BLE001 - a missing payload blob only loses the planned counts
        return counts
    for cell in payload.cells:
        if cell.stage == stage and cell.condition_index in by_index:
            counts[by_index[cell.condition_index]] += 1
    return counts
