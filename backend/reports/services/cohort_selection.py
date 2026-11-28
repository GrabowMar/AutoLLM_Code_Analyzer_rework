"""Newest unique runs across studies: the sample of a cross-study (final assessment) study report.

A study report normally describes one study's current runs (``study_report.scope_runs``). With
``config["selection"] == "latest_unique"`` it describes a pool of studies instead, reduced to **one run per
unit**: a unit is (group, task), a group is a model (its runs pooled across studies and conditions while
they share one treatment hash; a model with several treatments gets one group per treatment, never pooled).

Per unit the **newest usable** attempt is selected (``created_at``, ties broken by ``pk``). A newer attempt
without a usable outcome (infra error, exclusion, cancellation) or still in flight never replaces a usable
run. A unit without a usable run is attrition (its newest attempt is recorded, never sampled), one whose
only hope is a queued or running attempt is ``in_flight``, and a task in scope that a group never attempted
is ``missing``. Every other attempt of a unit is listed as superseded, with the reason.

Candidates are the runs of the pooled studies (owner-scoped as ``statistics.services.runs.study_runs``),
reference, beyond-plan, pilot-stage and superseded-plan runs left out and counted, then the model and task
filters. With ``exclude_truncated`` a selected run flagged truncated is then left out (a sensitivity analysis;
an older attempt never takes its place). With ``common_tasks_only`` the sample keeps only the tasks every group
still has a run in the sample for (complete cases, after the truncation exclusion). Per selected run and tool
the analysis result is ``ToolResult.latest_ids_per_job`` (the newest result that measured the app, else the
newest); the summary counts what that superseded.

:func:`select_latest_unique` returns a :class:`Selection`; ``Selection.summary`` is the report's
``selection`` section. :func:`validate_selection_config` checks a report config up front (API, CLI).
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import choices
from backend.generation.models.run import SLOT_KINDS

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "LATEST_UNIQUE",
    "SELECTION_KEYS",
    "Selection",
    "analyses_summary",
    "scope_fields",
    "select_latest_unique",
    "selection_warnings",
    "validate_selection_config",
]

LATEST_UNIQUE = "latest_unique"
# Values of ``selection`` that mean the existing one-study (or every-run) scope.
_DEFAULT_SELECTIONS = (None, "", "study")
# Config keys that only mean something with ``selection == "latest_unique"``.
SELECTION_KEYS = ("study_ids", "models", "tasks", "common_tasks_only")
_IN_FLIGHT = (choices.RunStatus.PENDING, choices.RunStatus.RUNNING)
_TASK_RE = re.compile(r"^[-\w.]+(@[1-9]\d*)?$")

SELECTION_RULE = (
    "One run per (model, task) across the pooled studies: the newest usable attempt (by creation time, ties "
    "broken by run id). A newer attempt without a usable outcome (infra error, exclusion, cancellation) or still "
    "in flight never replaces a usable run. A model's runs are pooled across studies and conditions only when "
    "they share one treatment hash; different treatments are separate groups. Reference, beyond-plan, "
    "pilot-stage and superseded-plan runs are never candidates. A unit without a usable run is attrition (its "
    "newest attempt is recorded, not sampled); a unit whose only candidates are queued or running is in flight."
)
TRUNCATED_RULE = (
    " Truncated runs excluded (sensitivity): a selected run flagged truncated is left out of the sample; an older "
    "attempt never takes its place."
)
COMPLETE_CASES_RULE = (
    " Complete cases: only the tasks every group has a run in the sample for are kept; the other tasks are dropped "
    "with their selected runs."
)
ANALYSES_RULE = (
    "Per selected run and tool: the newest finished result that measured the app (completed, or skipped as not "
    "applicable), else the newest finished result. Older results of the same run and tool, and analysis runs "
    "none of whose results is selected, are superseded."
)


@dataclass
class Selection:
    """The sample of a ``latest_unique`` report: the selected runs and how they were chosen."""

    runs: list[GenerationRun]
    # run id -> group label (the report's ``group`` column).
    group_of: dict[str, str]
    # True when every group is a whole model (``protocol.grouped_by = "model"``).
    whole_models: bool
    # The recorded (newest) attempt of every in-flight unit.
    in_flight: list[GenerationRun] = field(default_factory=list)
    # Attrition units by the outcome (else status) of their newest attempt.
    attrition: Counter = field(default_factory=Counter)
    summary: dict[str, Any] = field(default_factory=dict)


# ── config ───────────────────────────────────────────────────────────────


def _string_list(config: dict[str, Any], key: str) -> list[str] | None:
    """``config[key]`` as a list of distinct non-empty strings (order kept); None when absent or empty."""
    value = config.get(key)
    if value is None or value == []:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        msg = f"{key} must be a list of non-empty strings"
        raise ValueError(msg)
    return list(dict.fromkeys(v.strip() for v in value))


def _parse(config: dict[str, Any]) -> dict[str, Any]:
    """The selection filters of *config* (types checked); ValueError on a malformed value."""
    if config.get("study_id"):
        msg = "study_id cannot be combined with selection=latest_unique; list the studies in study_ids"
        raise ValueError(msg)
    if config.get("run_ids"):
        msg = "run_ids cannot be combined with selection=latest_unique"
        raise ValueError(msg)
    study_ids = _string_list(config, "study_ids") or []
    for sid in study_ids:
        try:
            uuid.UUID(sid)
        except ValueError as exc:
            msg = f"study_ids must be study UUIDs: {sid!r}"
            raise ValueError(msg) from exc
    tasks = _string_list(config, "tasks")
    for task in tasks or []:
        if not _TASK_RE.match(task):
            msg = f"tasks must be task refs (slug@version or slug): {task!r}"
            raise ValueError(msg)
    common = config.get("common_tasks_only", False)
    if common is None:
        common = False
    if not isinstance(common, bool):
        msg = "common_tasks_only must be true or false"
        raise ValueError(msg)  # noqa: TRY004 - configuration validation consistently raises ValueError
    return {
        "study_ids": [str(uuid.UUID(s)) for s in study_ids],
        "models": _string_list(config, "models"),
        "tasks": tasks,
        "common_tasks_only": common,
    }


def _visible_studies(user):
    """Studies *user* may report on: their own; staff and ``user=None``: every study (``_owner_scoped``)."""
    qs = Study.objects.all()
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_staff", False):
        qs = qs.filter(created_by=user)
    return qs


def _studies(study_ids: list[str], user) -> list[Study]:
    """The pooled studies, oldest first; every study *user* may see when *study_ids* is empty."""
    qs = _visible_studies(user)
    if not study_ids:
        return list(qs.order_by("created_at", "pk"))
    found = {str(s.pk): s for s in qs.filter(pk__in=study_ids)}
    missing = [s for s in study_ids if s not in found]
    if missing:
        # Unknown and another user's studies read alike (a request cannot probe for ids).
        msg = f"Study not found: {', '.join(missing)}"
        raise ValueError(msg)
    return sorted(found.values(), key=lambda s: (s.created_at, str(s.pk)))


def validate_selection_config(config: dict[str, Any], user=None) -> dict[str, Any] | None:
    """Check the cross-study selection keys of a study-report config; ValueError when invalid.

    Returns the parsed filters for ``selection == "latest_unique"``, else None (after checking that no
    selection-only key is set: they would be silently ignored by a one-study report).
    """
    selection = config.get("selection")
    if selection in _DEFAULT_SELECTIONS:
        given = [k for k in SELECTION_KEYS if config.get(k) not in (None, [], False)]
        if given:
            msg = f"{', '.join(given)} need selection=latest_unique"
            raise ValueError(msg)
        return None
    if selection != LATEST_UNIQUE:
        msg = "selection must be 'latest_unique' (or absent for one study)"
        raise ValueError(msg)
    filters = _parse(config)
    if filters["models"] or filters["tasks"]:
        # A filter matching no candidate run is a typo: refuse it now, not in the background generation.
        _candidates(filters, user)
    else:
        _studies(filters["study_ids"], user)
    return filters


# ── selection ────────────────────────────────────────────────────────────


def _task_of(run: GenerationRun) -> str:
    """The unit's task: ``slug@version`` (slot runs add their target); a run without a task is its own unit."""
    if run.task_version_id is None:
        return f"run:{run.pk}"
    ref = run.task_version.ref
    if run.kind in SLOT_KINDS and run.slot_target_id is not None:
        return f"{ref}#slot:{run.slot_target_id}"
    return ref


def _task_matches(run: GenerationRun, wanted: list[str] | None) -> bool:
    if not wanted:
        return True
    if run.task_version_id is None:
        return False
    version = run.task_version
    return version.ref in wanted or version.task.slug in wanted


def _is_pilot(run: GenerationRun) -> bool:
    if run.cell_id is not None:
        return run.cell.stage == choices.Stage.PILOT
    return run.stage == choices.Stage.PILOT


def _superseded_plan(run: GenerationRun, active_plans: dict[Any, Any]) -> bool:
    """A cell of a plan the study no longer runs (pilot and main plans with different hashes never pool)."""
    if run.cell_id is None:
        return False
    active = active_plans.get(run.study_id)
    return active is not None and run.cell.plan_id != active


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _outcome(run: GenerationRun) -> str:
    return run.outcome or run.status


def _group_label(model: str, treatment: str | None) -> str:
    if treatment is None:
        return model or "(no model)"
    return f"{model or '(no model)'} (treatment {treatment[:8] or 'unrecorded'})"


def _group_keys(runs: list[GenerationRun]) -> dict[str, tuple[str, str | None]]:
    """run id -> (model, treatment or None for a whole model).

    A model is one group only while all its runs carry the same treatment hash. With several, each hash is a
    group; runs without a recorded hash count as a treatment of their own ("unrecorded"), never pooled with a
    recorded one (their treatment is unknown).
    """
    hashes: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        hashes[_model(run)].add(run.treatment_hash or "")
    out = {}
    for run in runs:
        model = _model(run)
        out[str(run.pk)] = (model, None) if len(hashes[model]) <= 1 else (model, run.treatment_hash or "")
    return out


def _model(run: GenerationRun) -> str:
    return run.model.model_id if run.model_id is not None else ""


def _pick(attempts: list[GenerationRun]) -> tuple[str, GenerationRun]:
    """``(status, run)`` of one unit's attempts (oldest first): the newest usable, else in flight, else attrition."""
    usable = [r for r in attempts if r.usable is True and r.status not in _IN_FLIGHT]
    if usable:
        return "selected", usable[-1]
    in_flight = [r for r in attempts if r.status in _IN_FLIGHT]
    if in_flight:
        return "in_flight", in_flight[-1]
    return "attrition", attempts[-1]


def _reason(run: GenerationRun) -> str:
    if run.status in _IN_FLIGHT:
        return "in_flight"
    return "newer_usable_run" if run.usable is True else "unusable_attempt"


def _truncated(run: GenerationRun) -> bool:
    """The report's truncation flag (``study_report``'s ``exclude_truncated`` reads the same one)."""
    return bool((run.validity or {}).get("truncated"))


def _candidates(filters: dict[str, Any], user) -> tuple[list[Study], list[GenerationRun], Counter, Counter]:
    """``(studies, candidate runs oldest first, exclusion counts, runs per study)`` of the pooled studies."""
    from backend.statistics.services.runs import _owner_scoped

    studies = _studies(filters["study_ids"], user)
    active_plans = {s.pk: s.active_plan_id for s in studies}
    models, tasks_wanted = filters["models"], filters["tasks"]
    qs = _owner_scoped(GenerationRun.objects.filter(study_id__in=[s.pk for s in studies]), user)
    qs = qs.select_related("model", "cell", "task_version__task", "resource_policy").order_by("created_at", "pk")
    counts: Counter = Counter()
    runs_total: Counter = Counter()
    candidates: list[GenerationRun] = []
    for run in qs:
        runs_total[run.study_id] += 1
        if run.kind == choices.RunKind.REFERENCE:
            counts["reference_excluded"] += 1
        elif run.beyond_plan:
            counts["beyond_plan_excluded"] += 1
        elif _is_pilot(run):
            counts["pilot_excluded"] += 1
        elif _superseded_plan(run, active_plans):
            counts["superseded_plan_excluded"] += 1
        elif (models and _model(run) not in models) or not _task_matches(run, tasks_wanted):
            counts["filtered_out"] += 1
        else:
            candidates.append(run)
    _check_filters(candidates, models, tasks_wanted)
    return studies, candidates, counts, runs_total


def select_latest_unique(config: dict[str, Any], user=None) -> Selection:
    """The newest usable run per (group, task) over the pooled studies of *config* (module docstring)."""
    filters = _parse(config)
    studies, candidates, counts, runs_total = _candidates(filters, user)
    study_by_id = {s.pk: s for s in studies}
    models, tasks_wanted = filters["models"], filters["tasks"]
    exclude_truncated = bool(config.get("exclude_truncated", False))

    keys = _group_keys(candidates)
    units: dict[tuple[str, str], list[GenerationRun]] = defaultdict(list)
    for run in candidates:
        units[(_group_label(*keys[str(run.pk)]), _task_of(run))].append(run)
    group_key = {_group_label(*k): k for k in keys.values()}
    groups = sorted(group_key)
    tasks = sorted({task for _group, task in units})

    picked: dict[tuple[str, str], tuple[str, GenerationRun]] = {k: _pick(v) for k, v in units.items()}
    # Sensitivity: a selected run flagged truncated leaves the sample; no older attempt takes its place.
    truncated_units = {k for k, (s, r) in picked.items() if exclude_truncated and s == "selected" and _truncated(r)}

    def kept(unit: tuple[str, str]) -> bool:
        return unit in picked and picked[unit][0] == "selected" and unit not in truncated_units

    # Complete cases are computed on what stays in the sample (after the truncation exclusion).
    common = [t for t in tasks if groups and all(kept((g, t)) for g in groups)]
    common_set = set(common)
    dropped = [t for t in tasks if t not in common_set] if filters["common_tasks_only"] else []
    dropped_set = set(dropped)

    matrix, superseded = [], []
    selected: list[GenerationRun] = []
    in_flight: list[GenerationRun] = []
    attrition: Counter = Counter()
    for group in groups:
        for task in tasks:
            attempts = units.get((group, task)) or []
            if not attempts:
                matrix.append(_cell(group, task, "missing", None, study_by_id, attempts=0, in_sample=False))
                continue
            status, chosen = picked[(group, task)]
            excluded = None
            if (group, task) in truncated_units:
                excluded = "truncated"
            elif status == "selected" and task in dropped_set:
                excluded = "dropped_task"
            in_sample = status == "selected" and excluded is None
            if in_sample:
                selected.append(chosen)
            elif status == "in_flight":
                in_flight.append(chosen)
            elif status == "attrition" and task not in dropped_set:
                # A dropped task is outside the complete-case design: the dropped-task note explains it.
                attrition[_outcome(chosen)] += 1
            others = [r for r in attempts if r.pk != chosen.pk]
            matrix.append(
                _cell(
                    group,
                    task,
                    status,
                    chosen,
                    study_by_id,
                    attempts=len(attempts),
                    in_sample=in_sample,
                    superseded=[str(r.pk) for r in reversed(others)],
                    excluded=excluded,
                ),
            )
            superseded += [
                {
                    "run_id": str(r.pk),
                    "group": group,
                    "task": task,
                    **_study_fields(r, study_by_id),
                    "outcome": _outcome(r),
                    "status": r.status,
                    "usable": r.usable,
                    "created_at": _iso(r.created_at),
                    # Only a selected run replaces an attempt; a unit without one records its newest attempt.
                    "replaced_by": str(chosen.pk) if status == "selected" else None,
                    "recorded_attempt": str(chosen.pk) if status != "selected" else None,
                    "reason": _reason(r),
                    # False when the unit has no run in the sample (attrition, in flight, dropped or truncated).
                    "unit_in_sample": in_sample,
                }
                for r in reversed(others)
            ]
    selected.sort(key=lambda r: (r.created_at, str(r.pk)))
    group_of = {str(r.pk): _group_label(*keys[str(r.pk)]) for r in selected}
    dropped_runs = sum(
        1
        for (_g, t), (s, _r) in picked.items()
        if s == "selected" and t in dropped_set and (_g, t) not in truncated_units
    )
    rule = SELECTION_RULE + (TRUNCATED_RULE if exclude_truncated else "")
    rule += COMPLETE_CASES_RULE if filters["common_tasks_only"] else ""
    summary = {
        "mode": LATEST_UNIQUE,
        "rule": rule,
        "filters": {
            "study_ids": filters["study_ids"],
            "models": models,
            "tasks": tasks_wanted,
            "common_tasks_only": filters["common_tasks_only"],
            "exclude_truncated": exclude_truncated,
        },
        "studies": [
            {
                "id": str(s.pk),
                "name": s.name,
                "status": s.status,
                "runs_total": runs_total[s.pk],
                "runs_considered": sum(1 for r in candidates if r.study_id == s.pk),
                "runs_selected": sum(1 for r in selected if r.study_id == s.pk),
            }
            for s in studies
        ],
        "counts": {
            "candidates": len(candidates),
            "selected": len(selected),
            "superseded": len(superseded),
            "attrition_cells": sum(1 for c in matrix if c["status"] == "attrition"),
            "missing_cells": sum(1 for c in matrix if c["status"] == "missing"),
            "in_flight_cells": len(in_flight),
            # Selected runs left out as truncated (exclude_truncated); they are in no other count.
            "truncated_excluded": len(truncated_units),
            "pilot_excluded": counts["pilot_excluded"],
            "beyond_plan_excluded": counts["beyond_plan_excluded"],
            "reference_excluded": counts["reference_excluded"],
            "superseded_plan_excluded": counts["superseded_plan_excluded"],
            "filtered_out": counts["filtered_out"],
            "dropped_tasks": len(dropped),
            "dropped_runs": dropped_runs,
        },
        "groups": [
            _group_row(
                group,
                group_key[group],
                tasks=tasks,
                matrix=matrix,
                units=units,
                selected=selected,
                group_of=group_of,
                study_by_id=study_by_id,
            )
            for group in groups
        ],
        "tasks": tasks,
        "matrix": matrix,
        "superseded": superseded,
        "analyses": analyses_summary([r.pk for r in selected]),
        "common_tasks": common,
        "dropped_tasks": dropped,
        # Per group, the dropped tasks it has no run in the sample for, and why (complete cases).
        "dropped_by_group": _dropped_by_group(groups, dropped, units, picked, truncated_units),
    }
    return Selection(
        runs=selected,
        group_of=group_of,
        whole_models=all(treatment is None for _model_id, treatment in group_key.values()),
        in_flight=in_flight,
        attrition=attrition,
        summary=summary,
    )


def _dropped_by_group(groups, dropped, units, picked, truncated_units) -> list[dict[str, Any]]:
    """The groups that block each task dropped for complete cases: ``{group, tasks, reasons}``, most first."""
    out = []
    for group in groups:
        reasons: Counter = Counter()
        for task in dropped:
            unit = (group, task)
            if not units.get(unit):
                reasons["missing"] += 1
            elif unit in truncated_units:
                reasons["truncated"] += 1
            elif picked[unit][0] != "selected":
                reasons[picked[unit][0]] += 1
        if reasons:
            out.append({"group": group, "tasks": sum(reasons.values()), "reasons": dict(sorted(reasons.items()))})
    return sorted(out, key=lambda r: (-r["tasks"], r["group"]))


def _check_filters(candidates: list[GenerationRun], models: list[str] | None, tasks: list[str] | None) -> None:
    """A model or task filter that matches no candidate run is a typo, never an empty group."""
    if models:
        seen = {_model(r) for r in candidates}
        unknown = [m for m in models if m not in seen]
        if unknown:
            msg = f"No runs of model(s) {', '.join(unknown)} in the selected studies"
            raise ValueError(msg)
    if tasks:
        seen = {r.task_version.ref for r in candidates if r.task_version_id is not None}
        seen |= {r.task_version.task.slug for r in candidates if r.task_version_id is not None}
        unknown = [t for t in tasks if t not in seen]
        if unknown:
            msg = f"No runs of task(s) {', '.join(unknown)} in the selected studies"
            raise ValueError(msg)


def _study_fields(run: GenerationRun | None, study_by_id: dict[Any, Study]) -> dict[str, Any]:
    study = study_by_id.get(run.study_id) if run is not None else None
    return {
        "study_id": str(study.pk) if study is not None else None,
        "study_name": study.name if study is not None else None,
    }


def _cell(
    group: str,
    task: str,
    status: str,
    run: GenerationRun | None,
    study_by_id: dict[Any, Study],
    *,
    attempts: int,
    in_sample: bool,
    superseded: list[str] | None = None,
    excluded: str | None = None,
) -> dict[str, Any]:
    return {
        "group": group,
        "task": task,
        "status": status,
        "run_id": str(run.pk) if run is not None else None,
        **_study_fields(run, study_by_id),
        "outcome": _outcome(run) if run is not None else None,
        "created_at": _iso(run.created_at) if run is not None else None,
        "attempts": attempts,
        "superseded_run_ids": superseded or [],
        # False unless the cell's run is in the analysed sample.
        "in_sample": in_sample,
        # Why a selected run is not in the sample: "truncated" (exclude_truncated) or "dropped_task"
        # (common_tasks_only); None otherwise.
        "excluded": excluded,
    }


def _group_row(group, key, *, tasks, matrix, units, selected, group_of, study_by_id) -> dict[str, Any]:
    model, treatment = key
    mine = [r for r in selected if group_of[str(r.pk)] == group]
    attempted = [r for (g, _t), runs in units.items() if g == group for r in runs]
    hashes = sorted({r.treatment_hash for r in attempted if r.treatment_hash})
    cells = Counter(c["status"] for c in matrix if c["group"] == group)
    studies = sorted({r.study_id for r in mine}, key=lambda sid: (study_by_id[sid].created_at, str(sid)))
    return {
        "group": group,
        "model": model,
        "treatment_hash": treatment if treatment is not None else (hashes[0] if hashes else ""),
        "conditions": sorted({r.condition_id for r in mine if r.condition_id is not None}),
        "studies": [str(s) for s in studies],
        "resource_policies": sorted({r.resource_policy.ref for r in mine if r.resource_policy_id is not None}),
        "resource_policy_runs": dict(
            Counter(r.resource_policy.ref if r.resource_policy_id is not None else "(none)" for r in mine),
        ),
        # Runs in the analysed sample (after the truncation and complete-case exclusions).
        "tasks_selected": len(mine),
        "tasks_in_scope": len(tasks),
        "outcomes": dict(Counter(_outcome(r) for r in mine)),
        "truncated_excluded": sum(1 for c in matrix if c["group"] == group and c.get("excluded") == "truncated"),
        "dropped_runs": sum(1 for c in matrix if c["group"] == group and c.get("excluded") == "dropped_task"),
        "attrition_cells": cells.get("attrition", 0),
        "missing_cells": cells.get("missing", 0),
        "in_flight_cells": cells.get("in_flight", 0),
        "attempts": len(attempted),
    }


# ── analyses ─────────────────────────────────────────────────────────────


def analyses_summary(run_ids: Iterable[Any]) -> dict[str, Any]:
    """What ``ToolResult.latest_ids_per_job`` selected for *run_ids* and what it superseded (module docstring)."""
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import ToolResult

    ids = [str(r) for r in run_ids]
    analysis_runs = list(
        AnalysisRun.objects.filter(generation_job_id__in=ids).values("id", "launch_id", "name", "created_at"),
    )
    # The finished results latest_ids_per_job chooses from (its own filter).
    finished = list(
        ToolResult.objects.filter(
            run__generation_job_id__in=ids,
            run__status__in=[AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL, AnalysisRun.Status.FAILED],
        )
        .exclude(status__in=[ToolResult.Status.PENDING, ToolResult.Status.RUNNING])
        .values("id", "run_id", "run__generation_job_id", "tool_slug", "created_at"),
    )
    chosen = set(ToolResult.latest_ids_per_job(ids)) if ids else set()
    contributing = {r["run_id"] for r in finished if r["id"] in chosen}

    newest: dict[tuple, Any] = {}
    for r in finished:
        key = (r["run__generation_job_id"], r["tool_slug"])
        newest[key] = max(newest.get(key, r["created_at"]), r["created_at"])
    older_measured = sum(
        1
        for r in finished
        if r["id"] in chosen and r["created_at"] < newest[(r["run__generation_job_id"], r["tool_slug"])]
    )
    launch_of = {a["id"]: str(a["launch_id"]) if a["launch_id"] else "none" for a in analysis_runs}
    label_of = {a["id"]: _analysis_label(a["name"]) for a in analysis_runs}
    by_launch, tool_launches = _breakdown("launch_id", launch_of, analysis_runs, finished, chosen)
    by_label, tool_labels = _breakdown("label", label_of, analysis_runs, finished, chosen)
    return {
        "rule": ANALYSES_RULE,
        "analysis_runs_total": len(analysis_runs),
        "analysis_runs_contributing": len(contributing),
        "analysis_runs_superseded": len(analysis_runs) - len(contributing),
        "tool_results_total": len(finished),
        "tool_results_selected": sum(1 for r in finished if r["id"] in chosen),
        "tool_results_superseded": sum(1 for r in finished if r["id"] not in chosen),
        "older_measured_preferred": older_measured,
        "by_launch": by_launch,
        # The same by analysis name with its trailing run id cut ("FINAL static 1a2b3c4d" -> "FINAL static"):
        # what tells analysis rounds apart when they were launched without a launch id.
        "by_label": by_label,
        "tools_mixed_launches": [
            {"tool": tool, "launches": len(found), "launch_ids": sorted(found)}
            for tool, found in sorted(tool_launches.items())
            if len(found) > 1
        ],
        "tools_mixed_labels": [
            {"tool": tool, "labels": sorted(found)} for tool, found in sorted(tool_labels.items()) if len(found) > 1
        ],
    }


_RUN_SUFFIX_RE = re.compile(r"\s+[0-9a-f]{6,}$")


def _analysis_label(name: str | None) -> str:
    return _RUN_SUFFIX_RE.sub("", (name or "").strip()) or "(unnamed)"


def _breakdown(key: str, key_of: dict, analysis_runs, finished, chosen) -> tuple[list[dict[str, Any]], dict]:
    """Analysis runs and results selected / superseded per *key* value; and per tool, the values selected from."""
    rows: dict[str, dict[str, Any]] = {}
    for a in sorted(analysis_runs, key=lambda a: a["created_at"], reverse=True):
        entry = rows.setdefault(
            key_of[a["id"]],
            {
                key: key_of[a["id"]],
                "example_name": "",
                "analysis_runs": 0,
                "results_selected": 0,
                "results_superseded": 0,
                "latest_at": _iso(a["created_at"]),
            },
        )
        entry["analysis_runs"] += 1
        entry["example_name"] = entry["example_name"] or a["name"] or ""
    by_tool: dict[str, set[str]] = defaultdict(set)
    for r in finished:
        entry = rows[key_of[r["run_id"]]]
        if r["id"] in chosen:
            entry["results_selected"] += 1
            by_tool[r["tool_slug"]].add(key_of[r["run_id"]])
        else:
            entry["results_superseded"] += 1
    ordered = sorted(rows.values(), key=lambda e: (-e["results_selected"], -e["analysis_runs"], e[key]))
    return ordered, by_tool


# ── report hooks ─────────────────────────────────────────────────────────


def scope_fields(summary: dict[str, Any]) -> dict[str, Any]:
    """The ``protocol.scope`` keys of a selection report."""
    filters = summary.get("filters") or {}
    studies = summary.get("studies") or []
    return {
        "selection": LATEST_UNIQUE,
        # The pool: the named studies, or every visible study when none was named.
        "study_ids": [s["id"] for s in studies],
        # The studies of the pool with at least one run in the sample.
        "studies_contributing": [s["id"] for s in studies if s.get("runs_selected")],
        "models": filters.get("models"),
        "tasks": filters.get("tasks"),
        "common_tasks_only": bool(filters.get("common_tasks_only")),
    }


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _reasons(reasons: dict[str, int]) -> str:
    return ", ".join(f"{reason.replace('_', ' ')} {n}" for reason, n in reasons.items())


def selection_warnings(summary: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    """``(code, level, section, message)`` validity entries of a selection report (none without one).

    Counts describe the analysed sample: with complete cases, attempts on dropped tasks are named separately.
    """
    if not summary:
        return []
    out = []
    dropped = set(summary.get("dropped_tasks") or [])
    tasks = summary.get("tasks") or []
    kept = len([t for t in tasks if t not in dropped])
    superseded = summary.get("superseded") or []
    in_design = [x for x in superseded if x.get("task") not in dropped]
    pooled = len([s for s in summary.get("studies") or [] if s.get("runs_selected")])
    message = (
        f"Newest unique runs pooled from {_plural(pooled, 'study', 'studies')}: one run per (model, task), the "
        f"newest usable attempt; {len(in_design)} older or unusable attempt(s) superseded"
        + (
            f" on the {kept} task(s) kept ({len(superseded) - len(in_design)} more on the "
            f"{len(dropped)} task(s) dropped for complete cases)"
            if dropped
            else ""
        )
        + ". There is no single pre-registered design: every comparison is exploratory."
    )
    out.append(("cross_study_selection", "info", "all", message))
    replaced = sorted(
        {
            f"{x['group']} {x['task']}"
            for x in superseded
            if x.get("reason") == "newer_usable_run" and x.get("unit_in_sample", x.get("task") not in dropped)
        },
    )
    if replaced:
        message = (
            f"{len(replaced)} usable run(s) in the sample replaced an older usable attempt of the same (model, task): "
            f"{', '.join(replaced)}. When a re-run was started because of the earlier outcome, keeping the newer "
            "run favours that model; the earlier runs are listed as superseded."
        )
        out.append(("newer_usable_replaced", "warning", "all", message))
    groups = summary.get("groups") or []
    in_sample: dict[str, set[str]] = defaultdict(set)
    left_out: dict[str, Counter] = defaultdict(Counter)
    for cell in summary.get("matrix") or []:
        if cell.get("in_sample"):
            in_sample[cell["group"]].add(cell["task"])
        else:
            left_out[cell["group"]][cell.get("excluded") or cell.get("status") or "missing"] += 1
    empty = [g["group"] for g in groups if not in_sample.get(g["group"])]
    complete_cases = bool((summary.get("filters") or {}).get("common_tasks_only")) and bool(dropped)
    if empty and not (complete_cases and kept == 0):
        # (Complete cases that keep no task empty every group; their own entry below says why.)
        parts = "; ".join(f"{g} ({_reasons(dict(sorted(left_out[g].items())))})" for g in empty)
        message = (
            f"{_plural(len(empty), 'group has', 'groups have')} no run in the sample: {parts}. "
            f"{'It is' if len(empty) == 1 else 'They are'} absent from every result table and from the ranking."
        )
        out.append(("empty_groups", "warning", "all", message))
    if complete_cases:
        blocking = "; ".join(
            f"{b['group']} {b['tasks']} ({_reasons(b.get('reasons') or {})})"
            for b in summary.get("dropped_by_group") or []
        )
        if kept == 0:
            message = (
                f"Complete cases left no task: none of the {len(tasks)} task(s) has a run in the sample for every "
                f"group, so the sample is empty. Groups without a run on the dropped tasks: {blocking}. Leave such "
                "groups out with the models filter, or turn complete cases off."
            )
            out.append(("complete_cases", "warning", "all", message))
        else:
            dropped_runs = (summary.get("counts") or {}).get("dropped_runs", 0)
            message = (
                f"Complete cases kept {kept} of {len(tasks)} task(s); {len(dropped)} dropped with their "
                f"{dropped_runs} selected run(s). Groups without a run in the sample on a dropped task: {blocking}."
            )
            out.append(("complete_cases", "info", "all", message))
    task_sets = {g["group"]: frozenset(in_sample.get(g["group"], ())) for g in groups}
    if len({v for k, v in task_sets.items() if k not in empty}) > 1:
        by_size: dict[int, list[str]] = defaultdict(list)
        for group, found in sorted(task_sets.items()):
            if group not in empty:
                by_size[len(found)].append(group)
        sizes = "; ".join(f"{n} tasks: {', '.join(names)}" for n, names in sorted(by_size.items(), reverse=True))
        common = len(summary.get("common_tasks") or [])
        remedy = (
            f"set common_tasks_only for a complete-case comparison over the {common} task(s) every group has."
            if common
            else "no task has a run in the sample for every group, so complete cases would leave nothing; leave "
            "groups out with the models filter first."
        )
        message = f"Groups cover different task sets: {sizes}. Rankings and means compare unequal task sets; {remedy}"
        out.append(("unequal_task_sets", "warning", "funnel, model_outcomes, ranking", message))
    mixed = [g for g in groups if len(g.get("resource_policies") or []) > 1]
    if mixed:
        parts = "; ".join(
            f"{g['group']} ({', '.join(f'{p}: {n}' for p, n in sorted(g['resource_policy_runs'].items()))})"
            for g in mixed
        )
        message = (
            f"Runs selected for one group ran under different resource policies (budget presets): {parts}. "
            "Their budgets and caps differ; compare the affected runs with care."
        )
        out.append(("mixed_resource_policies", "warning", "all", message))
    unrecorded = [g["group"] for g in groups if g.get("treatment_hash") == "" and "(treatment " in g["group"]]
    if unrecorded:
        message = (
            f"Runs without a recorded treatment hash form their own group ({', '.join(unrecorded)}): their "
            "treatment is unknown, so they are never pooled with the same model's recorded treatments."
        )
        out.append(("treatment_unrecorded", "info", "all", message))
    analyses = summary.get("analyses") or {}
    mixed_launches = [f"{t['tool']} ({t['launches']} launches)" for t in analyses.get("tools_mixed_launches") or []]
    mixed_labels = [f"{t['tool']} ({' / '.join(t['labels'])})" for t in analyses.get("tools_mixed_labels") or []]
    if mixed_launches or mixed_labels:
        message = (
            f"The selected results of {', '.join(mixed_launches or mixed_labels)} come from more than one analysis "
            "launch or round; check the tool versions and commands in the protocol."
        )
        out.append(("analysis_launches_mixed", "info", "issues, code_metrics", message))
    return out
