"""The study report: every table a paper needs from one scope of runs, with its uncertainty.

Sections (each a JSON object/list in ``report_data``):

* ``protocol`` -- what was run: scope, models, tasks, repeats, stacks, starters,
  harnesses, run kinds, configuration fingerprints, tool and evaluation suite
  versions, the pre-registered analysis plan and its hash, the instrument
  coverage, whether the analysis is confirmatory, attrition, and a fingerprint
  of the data.
* ``definitions`` -- what "generated", "deployed" and "functionally
  correct" mean here, so the three are never conflated.
* ``funnel`` -- generated -> analyzed -> evaluated -> deployed -> passes
  every check, per group and task, with 95% intervals over apps.
* ``model_outcomes`` -- the plan metrics per group, app means first.
* ``issues`` -- cross-tool deduplicated issues per model/condition/task/
  tool and the tool-overlap table (``statistics.services.study``).
* ``security_bounds`` -- code security issues per normalized severity:
  issues, apps affected, and for a zero count the exact 95% upper bound on
  the per-app rate.
* ``code_metrics`` -- size, backend share, complexity per app, group and
  task; the spread across groups on the same task; app-level correlations
  of size/complexity with issue density.
* ``correlations`` -- model parameters (nominal and effective context
  window, output limit, prices) vs outcomes: Spearman rho, permutation p,
  bootstrap CI, Holm and BH across the whole family, leave-one-out influence.
* ``ranking`` -- TOPSIS/WSM with weight and data sensitivity
  (``statistics.services.mcdm``).
* ``planned_comparisons`` -- for a study with a pre-registered plan: every
  declared metric, each condition vs the baseline, app-blocked, sign +
  Wilcoxon tests corrected as the plan says.
* ``accounting`` -- what ran.
* ``validity`` -- threats detected in this data, each with the section it
  affects.

The sample of a study is its **current runs** (``generation.runs.view.current_runs``): the first usable
attempt of every main-stage cell (never a later attempt), pilot runs and runs beyond the plan left out.
Runs without a model-attributable outcome (infra errors, exclusions) are attrition: reported, never
counted as failures. The default analysis is intention-to-treat (truncated runs included); a locked
plan's ``exclusions.truncated`` or the report's ``exclude_truncated`` makes the sensitivity analysis.

The unit of analysis is the app: one unit (task version or slot target) under one condition
(``statistics.services.runs.app_unit``). Its repeats are averaged before any per-group figure, and
intervals and zero-count bounds are over apps, not runs. A *group* is the model, or the condition when
some model ran under several conditions (harness, prompt or stack arms), so arms that share a model are never pooled
(``protocol.grouped_by``; group rows keep the ``model`` key, holding the condition label in that case,
and the issue table gets ``by_group`` rows under the same labels). Model-parameter correlations stay one
point per model (a model's arms averaged).

Run content (files, LOC, labels, evaluations) is read only through ``generation.runs.view``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Count
from django.utils import timezone

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationRun
from backend.generation.models import RunAttempt
from backend.generation.models import Study
from backend.generation.models import choices
from backend.reports.services.cohort_selection import LATEST_UNIQUE
from backend.reports.services.cohort_selection import scope_fields
from backend.reports.services.cohort_selection import select_latest_unique
from backend.reports.services.cohort_selection import selection_warnings
from backend.reports.services.list_cost import cost_basis
from backend.reports.services.list_cost import earlier_attempts
from backend.reports.services.list_cost import list_prices
from backend.reports.services.list_cost import map_twin_prices
from backend.reports.services.list_cost import run_list_cost
from backend.reports.services.loc import loc_from_view
from backend.reports.services.plan_stats import FINAL_ASSESSMENT_METRICS
from backend.reports.services.plan_stats import MIN_APPS_FOR_CI
from backend.reports.services.plan_stats import PLAN_METRICS
from backend.reports.services.plan_stats import cluster_bootstrap_ci
from backend.reports.services.plan_stats import declared_metrics
from backend.reports.services.plan_stats import paired_difference
from backend.reports.services.plan_stats import paired_mean_ci
from backend.reports.services.plan_stats import plan_multiplicity
from backend.reports.services.plan_stats import sign_test_p
from backend.reports.services.plan_stats import wilcoxon_p
from backend.statistics.services import inference as inf
from backend.statistics.services.dedup import VULN_TOOLS
from backend.statistics.services.mcdm import CRITERIA_PRESETS
from backend.statistics.services.mcdm import criteria_from_config
from backend.statistics.services.mcdm import rank_models
from backend.statistics.services.runs import app_unit
from backend.statistics.services.runs import condition_label
from backend.statistics.services.runs import run_labels
from backend.statistics.services.runs import study_runs
from backend.statistics.services.study import CODE_METRIC_COLUMNS
from backend.statistics.services.study import CODE_METRIC_TOOLS
from backend.statistics.services.study import SEVERITIES
from backend.statistics.services.study import code_metrics_by_run
from backend.statistics.services.study import group_summary
from backend.statistics.services.study import issue_breakdowns
from backend.statistics.services.study import tool_measured

if TYPE_CHECKING:
    from backend.generation.runs.view import RunView

# 4: exact paired inference, fixed correction families, shared-task ranking resampling,
# conditional interval assumptions and explicit attempt/instrument provenance.
REPORT_VERSION = 5
SMALL_N_MODELS = 10
MIN_APPS_PER_MODEL = 5
RunStatus = choices.RunStatus
_TERMINAL = (choices.StudyStatus.COMPLETED, choices.StudyStatus.CANCELLED, choices.StudyStatus.STOPPED)
_IN_FLIGHT = (RunStatus.PENDING, RunStatus.RUNNING)
# These statuses do not provide a grade; they do not identify the cause of the missing grade.
_EVAL_UNMEASURED = frozenset({choices.EvaluationStatus.NOT_MEASURED, choices.EvaluationStatus.ERROR})
# Final outcomes that are known model-caused failures: whole-cohort (ITT) rates count them as 0.
_KNOWN_FAILURES = frozenset({choices.Outcome.OUTPUT_INVALID, choices.Outcome.BUILD_FAIL, choices.Outcome.START_FAIL})
# Cells stopped before they ran: the planned sample was not collected.
_STOPPED_CELLS = (
    choices.CellExclusionReason.BUDGET_STOP,
    choices.CellExclusionReason.STOPPED_BY_RULE,
    choices.CellExclusionReason.CANCELLED,
)
# Per-app lizard / jscpd metric columns (radon keeps its avg_complexity / max_complexity names).
TOOL_METRIC_KEYS = tuple(k for k, (tool, _key) in CODE_METRIC_COLUMNS.items() if tool != "radon")
# Which per-app columns each metric tool feeds (code_metrics section; instrument-drift check).
_METRIC_TOOL_COLUMNS = {
    "radon": ("avg_complexity", "max_complexity"),
    **{tool: tuple(k for k in TOOL_METRIC_KEYS if CODE_METRIC_COLUMNS[k][0] == tool) for tool in CODE_METRIC_TOOLS},
}

DEFINITIONS = {
    "app": (
        "One unit (task version or slot target) built under one condition, the unit of analysis: its repeats "
        "are averaged before any per-model or per-condition figure."
    ),
    "run": "One generation run: one repeat of one app (a cell's current run: its first usable attempt).",
    "attrition": (
        "A cell whose current run has no model-attributable outcome (infra error or exclusion): left out of every "
        "rate and reported apart, never counted as a failure."
    ),
    "generated": "The run completed with a usable outcome and valid output (not a measure of quality).",
    "truncated": (
        "The run was cut off by a budget, wall-clock, idle or turn cap; its app may be incomplete. Included by "
        "default (intention-to-treat)."
    ),
    "analyzed": "The app has a finished static-analysis run (each tool's latest result is counted).",
    "source_loc": (
        "A file-scope size heuristic: nonblank lines excluding language-specific comment-prefix lines in "
        "eligible authored, modified and slot source files. Entire modified files are counted, not net added "
        "lines. This is not a parser-based logical SLOC measure; multiline comments and docstrings can remain."
    ),
    "evaluated": "The selected evaluation suite measured the app (tester errors excluded).",
    "deployed": "The app built and started, and the tester ran against it (outcome tested).",
    "end_to_end_deploy_rate": (
        "Observed deployment across model-attributable outcomes (output/build/start failures included). "
        "Static/build-only tested outcomes without an observed start remain unknown; "
        "a point rate is withheld when deployment is unknown for any run."
    ),
    "conditional_deploy_rate": (
        "The existing deploy_rate and ranking criterion are conditional on a measured evaluation. "
        "Read end_to_end_deploy_rate for success across the full usable cohort."
    ),
    "functionally_correct": (
        "The app passed every counted instrument check (agent pass rate 1). This includes unstated conventions; "
        "it is not a measure of complete prompt compliance. Read requirement_alignment for the tagged subset."
    ),
    "pass_rate": (
        "Share of functional checks passed (0..1; infra and flaky checks excluded); 0 for evaluated apps that "
        "did not build or start."
    ),
    "unique_issue": "One deduplicated static issue: findings of equivalent rules from several tools at one location.",
    "code_issue": (
        "A unique issue in the app's own non-test code (deptry/depcheck manifest findings included); "
        "per kLOC over non-test model-scope source lines (authored, modified and slot files)."
    ),
    "dependency_advisory": (
        "A known vulnerability in a package version (pip-audit, npm-audit, osv-scanner, trivy, retire): not a "
        "code issue; split into versions the model chose and versions inherited from the starter."
    ),
    "runtime_issue": "An issue only dynamic scanners (DAST) reported against the running app; one per issue class.",
    "test_code_issue": "A unique issue in test files (tests/, *.test.*, conftest.py, smoke scripts); counted apart.",
    "code_metrics_tools": (
        "Complexity and duplication as the tools printed them, from each tool's latest result per app: radon "
        "(avg_complexity, max_complexity: Python only), lizard (lizard_*: Python and JS/JSX/TS functions; "
        "functions_over_15 = CCN above 15) and jscpd (jscpd_duplication_percentage, jscpd_clones). They read the "
        "analyzed tree (the whole app), test files included; missing when the tool did not measure the app."
    ),
    "normalized_severity": (
        "The platform's heuristic critical..info level for a finding, assigned even where the tool prints none; "
        "tables label it. 'Levels the tool printed' are the tools' own labels."
    ),
}


# What a final-assessment report (whole-cohort rates, cost at list price) adds to the definitions; its
# ``conditional_deploy_rate`` entry replaces the one that points readers at the ranking's conditional criterion.
FINAL_ASSESSMENT_DEFINITIONS = {
    "conditional_deploy_rate": (
        "deploy_rate, pass_rate and functional_passed are conditional on a measured evaluation: known "
        "output/build/start failures have no grade and are left out of them. The whole-cohort rates count those "
        "failures as 0; end_to_end_deploy_rate withholds its rate while any deployment is unknown."
    ),
    "whole_cohort_rates": (
        "deploy_rate_itt, pass_rate_itt and functional_passed_itt: over every usable app, known output/build/start "
        "failures counted as 0 (deployment from final run outcomes, as end_to_end_deploy_rate). Runs whose outcome "
        "or grade is genuinely unknown are left out and reported, never counted as failures."
    ),
    "list_cost": (
        "Generation cost at public list prices (list_cost_per_app): every run's tokens priced, cache-aware, by its "
        "model's OpenRouter catalog row or catalog twin (protocol.cost_basis), failed runs included. The billed "
        "cost (cost_per_app) is kept for reference; it is 0 on subscription endpoints and keys billed outside "
        "per-call pricing."
    ),
}


# ── per-app table ────────────────────────────────────────────────────────


def _evaluation_fields(view: RunView, run: GenerationRun, *, eval_suite=None) -> dict[str, Any]:
    """The evaluation columns of a run row, from its selected evaluation instrument."""
    from backend.reports.services.evaluation_selection import evaluation_for_report
    from backend.reports.services.requirement_alignment import alignment_fields

    evaluation = evaluation_for_report(view, eval_suite)
    status = evaluation.status if evaluation is not None else ""
    result = (evaluation.result if evaluation is not None else None) or {}
    evaluated = status == choices.EvaluationStatus.MEASURED and bool(result.get("measured", True))
    stages = (evaluation.stages if evaluation is not None else None) or {}
    start_status = (stages.get("start") or {}).get("status")
    deployed = None
    if evaluated:
        if any((stages.get(stage) or {}).get("status") == "fail" for stage in ("build", "start")):
            deployed = False
        elif start_status == "ok":
            deployed = True
        elif not str(getattr(run, "outcome_reason", "") or "").startswith("not_tested:"):
            deployed = run.outcome == choices.Outcome.TESTED
    metrics = result.get("metrics") or {}
    rate = metrics.get("agent_pass_rate")
    if not evaluated:
        pass_rate = None
    elif deployed is False:
        pass_rate = 0.0  # built or started nothing: every functional check fails
    else:
        pass_rate = float(rate) if rate is not None else None
    suite = evaluation.eval_suite if evaluation is not None else None
    return {
        "evaluation_status": status,
        "eval_suite": suite.ref if suite is not None else "",
        "tester_version": result.get("tester_version"),
        "evaluated": evaluated,
        "deployed": deployed,
        "functional_passed": (pass_rate == 1.0) if evaluated and pass_rate is not None else None,
        "pass_rate": pass_rate,
        "start_stage_status": start_status,
        **alignment_fields(result, (view.task_dict() or {}).get("variant"), measured=evaluated),
    }


def _providers_by_run(run_ids) -> dict[str, list[str]]:
    """Upstream providers that served each run's calls (``RunAttempt.served``, every attempt)."""
    out: dict[str, set[str]] = defaultdict(set)
    for run_id, served in RunAttempt.objects.filter(run_id__in=list(run_ids)).values_list("run_id", "served"):
        providers = (served or {}).get("providers") if isinstance(served, dict) else None
        for name in providers or {}:
            if name and name != "unknown":
                out[str(run_id)].add(name)
    return {k: sorted(v) for k, v in out.items()}


def itt_outcomes(row: dict[str, Any]) -> dict[str, Any]:
    """The whole-cohort (intention-to-treat) outcomes of one run row: known model-caused failures count as 0.

    ``deployed_itt`` is the funnel's end-to-end deployment evidence (:func:`deployment_outcome`), so the
    rates agree with it. ``pass_rate_itt`` / ``functional_passed_itt`` are the measured grade, 0 for a known
    output/build/start failure (it cannot pass a check) and None when the grade is genuinely unknown (no
    measured evaluation and no known failure): left out and reported, never counted as a failure.
    """
    failed = row.get("outcome") in _KNOWN_FAILURES
    measured = bool(row.get("evaluated"))
    return {
        "deployed_itt": deployment_outcome(row),
        "pass_rate_itt": 0.0 if failed else (row.get("pass_rate") if measured else None),
        "functional_passed_itt": False if failed else (row.get("functional_passed") if measured else None),
    }


def app_table(
    runs,
    *,
    include_ai: bool = False,
    line_tolerance: int = 2,
    eval_suite=None,
    prices: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One row per run with every per-run quantity the report aggregates; plus the issue breakdowns.

    *prices* (``list_cost.list_prices`` of the runs' models) adds the final-assessment columns: the
    whole-cohort outcomes (:func:`itt_outcomes`) and ``list_cost``, the run's cost at list prices.
    """
    from backend.generation.runs.view import run_views
    from backend.reports.services.evaluation_selection import planned_suite_ref

    by_id = GenerationRun.objects.select_related("model", "condition", "starter").in_bulk([r.pk for r in runs])
    runs = [by_id[r.pk] for r in runs if r.pk in by_id]
    issues = issue_breakdowns(runs, include_ai=include_ai, line_tolerance=line_tolerance)
    by_run = {str(a["run_id"]): a for a in issues["per_app"]}
    result_ids = ToolResult.latest_ids_per_job([r.id for r in runs])
    complexity: dict[str, dict[str, Any]] = {}
    for run_id, metrics in ToolResult.objects.filter(id__in=result_ids, tool_slug="radon").values_list(
        "run__generation_job_id",
        "metrics",
    ):
        if isinstance(metrics, dict) and metrics.get("average_complexity") is not None:
            complexity[str(run_id)] = metrics
    # lizard / jscpd metrics (radon's stay avg_complexity / max_complexity above).
    tool_metrics = code_metrics_by_run(result_ids)
    providers = _providers_by_run([r.id for r in runs])

    rows = []
    for run, view in zip(runs, run_views(runs), strict=True):
        rid = view.id
        metrics = run.metrics if isinstance(run.metrics, dict) else {}
        validity = run.validity if isinstance(run.validity, dict) else {}
        loc = loc_from_view(view)  # non-test model-scope source lines; test files are test_loc
        backend, frontend = loc["backend_loc"], loc["frontend_loc"]
        total = loc["total_loc"]
        analyzed = by_run.get(rid)

        def counted(key: str, *fallbacks: str, analyzed=analyzed) -> Any:
            if not analyzed:
                return None
            for k in (key, *fallbacks):
                if k in analyzed:
                    return analyzed[k]
            return None

        cx = complexity.get(rid) or {}
        stack = view.stack
        rows.append(
            {
                **run_labels(run, view),
                "stack": stack.ref if stack is not None else "",
                "starter": run.starter.ref if run.starter_id else "",
                "harness": "/".join(view.harness) if view.harness else "",
                "prompt_hash": run.prompt_hash,
                "config_fingerprint": run.config_fingerprint,
                "status": run.status,
                "outcome": run.outcome,
                "usable": run.usable,
                "generated": bool(run.usable) and run.outcome != choices.Outcome.OUTPUT_INVALID,
                "truncated": bool(validity.get("truncated")),
                "route_error_terminated": any(
                    isinstance(n, dict) and n.get("code") == "route_error_terminated"
                    for n in validity.get("notes") or []
                ),
                "files": loc["source_files"],
                "loc_backend": backend,
                "loc_frontend": frontend,
                # None when the tree was never collected: unknown, not zero.
                "loc": total,
                "tree_hash": run.tree_hash or None,
                "test_loc": loc.get("test_loc"),
                "backend_share": round(backend / total, 4) if total else None,
                **_evaluation_fields(view, run, eval_suite=eval_suite),
                "planned_eval_suite": planned_suite_ref(view),
                "analyzed": analyzed is not None,
                "raw_findings": counted("raw_findings"),
                "unique_issues": counted("unique_issues"),
                # Code issues only (P2-P4): advisories, runtime issues and test code are counted apart.
                "code_unique": counted("code_unique"),
                # Of code_unique: issues in the model's own files (authored, modified, slot).
                "code_unique_model": counted("code_unique_model"),
                "code_unique_tests": counted("code_unique_tests"),
                "manifest_unique": counted("manifest_unique"),
                # Of manifest_unique: "unused" findings on packages the starter preinstalls.
                "manifest_unused_starter": counted("manifest_unused_starter"),
                "runtime_unique": counted("runtime_unique"),
                "dependency_unique": counted("dependency_unique"),
                "dependency_added": counted("dependency_added"),
                "dependency_inherited": counted("dependency_inherited"),
                "security_unique": counted("security_unique"),
                "security_unique_model": counted("security_unique_model"),
                "security_unique_tests": counted("security_unique_tests"),
                "issues_per_kloc": counted("unique_per_kloc"),
                # Normalized severities (a heuristic, named so; never the tool's own label): every
                # issue kind, and code security issues only.
                **{f"unique_normalized_{s}": counted(f"unique_normalized_{s}") for s in SEVERITIES},
                **{f"security_normalized_{s}": counted(f"security_normalized_{s}") for s in SEVERITIES},
                "avg_complexity": cx.get("average_complexity"),
                "max_complexity": cx.get("max_complexity"),
                # None when the tool did not measure the app (study.code_metrics_by_run).
                **{k: (tool_metrics.get(rid) or {}).get(k) for k in TOOL_METRIC_KEYS},
                "cost": float(metrics["cost_usd"]) if metrics.get("cost_usd") is not None else None,
                "tokens": metrics.get("total_tokens"),
                # Upstream providers that served the run's LLM calls (OpenRouter routing).
                "providers": providers.get(rid, []),
            },
        )
        if prices is not None:
            price = prices.get(rows[-1]["model"])
            rows[-1].update(
                # The run's outcome reason decides a ``not_tested:<level>`` deployment (unknown without an observed
                # start), as the funnel's end-to-end rate does once provenance adds it to the row.
                itt_outcomes({**rows[-1], "outcome_reason": run.outcome_reason}),
                # Unknown (None), never 0, when the model has no list price or the tokens were not recorded.
                list_cost=run_list_cost(metrics, price),
                list_cost_priced_as=(price.priced_as or None) if price is not None else None,
            )
    return rows, issues


def _mean(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _avg(values) -> float | None:
    """Unrounded mean of the non-None values (booleans count as 0/1)."""
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _units(rows) -> dict[tuple, list[dict[str, Any]]]:
    """Run rows grouped by app (:func:`app_unit`): the repeats of one app under one condition."""
    units: dict[tuple, list] = defaultdict(list)
    for r in rows:
        units[app_unit(r)].append(r)
    return units


def _app_stats(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """The plan metrics of one app (its repeats averaged); ``_code``/``_loc`` feed issues per kLOC.

    Rows with the final-assessment columns (``app_table(prices=...)``) also give the
    :data:`FINAL_ASSESSMENT_METRICS`: whole-cohort rates over every run and the list cost of every run.
    """
    analyzed = [r for r in rows if r["analyzed"]]
    stats = {
        "deploy_rate": _avg(r["deployed"] for r in rows),
        "pass_rate": _avg(r["pass_rate"] for r in rows),
        "functional_passed": _avg(r["functional_passed"] for r in rows),
        "unique_issues_per_app": _avg(r["unique_issues"] for r in analyzed),
        "security_per_app": _avg(r["security_unique_model"] for r in analyzed),
        "cost_per_app": _avg(r["cost"] for r in rows if r["generated"]),
        "_code": _avg(r["code_unique_model"] for r in analyzed),
        "_loc": _avg(r["loc"] for r in analyzed),
    }
    if rows and "deployed_itt" in rows[0]:
        stats.update(
            deploy_rate_itt=_avg(r["deployed_itt"] for r in rows),
            pass_rate_itt=_avg(r["pass_rate_itt"] for r in rows),
            functional_passed_itt=_avg(r["functional_passed_itt"] for r in rows),
            # Every run's tokens were spent, failures included.
            list_cost_per_app=_avg(r["list_cost"] for r in rows),
        )
    return stats


def _combine(stats: list[dict[str, float | None]]) -> dict[str, float | None]:
    """Group metrics from per-app stats: the mean over apps; issues per kLOC as total code issues over total LOC."""
    out: dict[str, float | None] = {}
    extra = [k for k in FINAL_ASSESSMENT_METRICS if any(k in s for s in stats)]
    for key in (*PLAN_METRICS, *extra):
        if key == "issues_per_kloc":
            measured = [s for s in stats if s["_code"] is not None and s["_loc"] is not None]
            loc = sum(s["_loc"] for s in measured)
            out[key] = round(sum(s["_code"] for s in measured) * 1000 / loc, 4) if loc else None
        else:
            out[key] = _mean(s.get(key) for s in stats)
    return out


def model_criteria(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """The plan metrics for one group of run rows (``analysis_plan.METRICS``).

    The unit is the app: each app's repeats are averaged first, then the
    group's value is the mean over its apps (as on the Results tab), so an
    app with more usable repeats does not weigh more. ``issues_per_kloc`` is
    the apps' mean code issues in the model's own files over their mean non-test model LOC, summed over apps
    (the ``unique_per_kloc`` of the issue tables).
    """
    return _combine([_app_stats(v) for v in _units(rows).values()])


def _group(rows, key):
    groups: dict[Any, list] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return dict(sorted(groups.items(), key=lambda kv: str(kv[0])))


def label_groups(rows: list[dict[str, Any]]) -> str:
    """Set each row's ``group``: its model, or its condition when a model ran under several; returns which.

    A label shared by two different conditions gets the condition id appended.
    Runs outside a study keep their model.
    """
    conditions: dict[str, set] = defaultdict(set)
    for r in rows:
        if r["condition_id"] is not None:
            conditions[r["model"]].add(r["condition_id"])
    grouped_by = "condition" if any(len(c) > 1 for c in conditions.values()) else "model"
    ids_by_label: dict[str, set] = defaultdict(set)
    for r in rows:
        if r["condition_id"] is not None:
            ids_by_label[r["condition_label"] or str(r["condition_id"])].add(r["condition_id"])
    for r in rows:
        if grouped_by == "model" or r["condition_id"] is None:
            r["group"] = r["model"]
            continue
        label = r["condition_label"] or str(r["condition_id"])
        r["group"] = label if len(ids_by_label[label]) == 1 else f"{label} (#{r['condition_id']})"
    return grouped_by


def _selection_groups(rows: list[dict[str, Any]], selection) -> str:
    """Set each row's ``group`` to its cross-study selection group (a model, or a model's treatment).

    Returns ``model`` when every group is a whole model, else ``condition`` (groups are then treatments of a
    model, kept apart as arms are).
    """
    for r in rows:
        r["group"] = selection.group_of[r["run_id"]]
    return "model" if selection.whole_models else "condition"


def _group_fields(group: str, items: list[dict[str, Any]], grouped_by: str) -> dict[str, Any]:
    """The identifying columns of a group row: ``model`` is the group's label (a condition label when grouped so).

    ``condition_id`` is the group's one condition, None when it pools several (a cross-study selection group).
    """
    if grouped_by != "condition":
        return {"model": group}
    conditions = {r["condition_id"] for r in items}
    return {
        "model": group,
        "condition_id": next(iter(conditions)) if len(conditions) == 1 else None,
        "model_id": items[0]["model"],
    }


# ── sections ─────────────────────────────────────────────────────────────


def _app_rate(items, value, seed: int) -> dict[str, Any]:
    """A rate over apps: each app's repeats averaged, then the mean over apps.

    ``k``/``n`` count runs. The interval is Wilson over apps when every app's
    repeats agree (each app is then one 0/1 outcome; with one run per app it
    is the plain Wilson interval), otherwise a two-stage cluster bootstrap
    over apps (none below ``MIN_APPS_FOR_CI`` apps). Runs of one app are not
    independent, so a Wilson interval over runs would be too narrow.
    """
    by_app: dict[tuple, list[float]] = defaultdict(list)
    groups = {r.get("group") or r.get("condition_id") or r.get("model") for r in items}
    for r in items:
        v = value(r)
        if v is not None:
            by_app[app_unit(r)].append(1.0 if v else 0.0)
    means = [sum(v) / len(v) for v in by_app.values()]
    out: dict[str, Any] = {
        "k": int(sum(sum(v) for v in by_app.values())),
        "n": sum(len(v) for v in by_app.values()),
        "apps": len(by_app),
        "rate": round(sum(means) / len(means), 4) if means else None,
        "ci95": None,
        "ci_method": None,
        "ci_assumptions": (
            "Descriptive pooled rate: intervals withheld across fixed endpoint/condition groups; "
            "shared tasks do not provide independent endpoint-by-task trials."
            if len(groups) > 1
            else "Conditional on this endpoint/condition and measured task cohort. Intervals assume independent, "
            "exchangeable task units; a convenience cohort does not establish population representativeness."
        ),
    }
    if not means or len(groups) > 1:
        return out
    if all(m in (0.0, 1.0) for m in means):
        out.update(ci95=inf.wilson_interval(int(sum(means)), len(means)), ci_method="wilson_apps")
    elif len(by_app) >= MIN_APPS_FOR_CI:
        ci = cluster_bootstrap_ci(by_app, random.Random(seed))  # noqa: S311 - reproducible bootstrap
        out.update(ci95=ci, ci_method="cluster_bootstrap")
    return out


def _app_mean(items, key: str) -> float | None:
    """Mean over apps of each app's mean *key* (repeats averaged first)."""
    return _mean(_avg(r[key] for r in v) for v in _units(items).values())


def _end_to_end_functional_bounds(rows) -> dict[str, Any]:
    """Observed success bounds over the whole usable cohort; unknown grades are not failures."""
    failures = {choices.Outcome.OUTPUT_INVALID, choices.Outcome.BUILD_FAIL, choices.Outcome.START_FAIL}

    def value(row):
        if row.get("outcome") in failures:
            return False
        return row.get("functional_passed") if row.get("evaluated") else None

    units = list(_units(rows).values())
    lower = [_avg(0.0 if value(row) is None else float(value(row)) for row in unit) for unit in units]
    upper = [_avg(1.0 if value(row) is None else float(value(row)) for row in unit) for unit in units]
    return {
        "apps": len(units),
        "runs": len(rows),
        "unknown_runs": sum(value(row) is None for row in rows),
        "unknown_apps": sum(any(value(row) is None for row in unit) for unit in units),
        "lower": _mean(lower),
        "upper": _mean(upper),
        "note": "Bounds from unmeasured functional outcomes, not confidence intervals. "
        "Known output/build/start failures cannot pass all checks; missing grades remain unknown.",
    }


def deployment_outcome(row) -> bool | None:
    """Deployment evidence from final outcomes; evaluation levels can finish without starting an app."""
    outcome = row.get("outcome")
    if outcome in {choices.Outcome.OUTPUT_INVALID, choices.Outcome.BUILD_FAIL, choices.Outcome.START_FAIL}:
        return False
    if outcome == choices.Outcome.TESTED:
        if str(row.get("outcome_reason") or "").startswith("not_tested:"):
            return True if row.get("start_stage_status") == "ok" else None
        return True
    return None


def _end_to_end_deploy_rate(rows, seed):
    result = _app_rate(rows, deployment_outcome, seed)
    result["unknown_runs"] = sum(deployment_outcome(row) is None for row in rows)
    if result["unknown_runs"]:
        # A rate over the known subset would be another conditional rate, not a whole-cohort rate.
        result.update(rate=None, ci95=None, ci_method=None)
    return result


def _itt_rates(rows, seed: int) -> dict[str, Any]:
    """The whole-cohort rates of a funnel row (rows with the final-assessment columns): failures counted as 0.

    Unlike ``end_to_end_deploy_rate`` the rate is not withheld when some outcome is unknown: unknown runs are
    left out (``unknown_runs``) and reported, as the ranking's ``deploy_rate_itt`` does.
    """
    deploy = _app_rate(rows, lambda r: r["deployed_itt"], seed)
    deploy["unknown_runs"] = sum(r["deployed_itt"] is None for r in rows)
    functional = _app_rate(rows, lambda r: r["functional_passed_itt"], seed)
    functional["unknown_runs"] = sum(r["functional_passed_itt"] is None for r in rows)
    return {
        "deploy_rate_itt": deploy,
        "functional_rate_itt": functional,
        "mean_pass_rate_itt": _app_mean(rows, "pass_rate_itt"),
        "known_failures": sum(r.get("outcome") in _KNOWN_FAILURES for r in rows),
    }


def _funnel_row(rows, seed: int = 0) -> dict[str, Any]:
    generated = [r for r in rows if r["generated"]]
    evaluated = [r for r in rows if r["evaluated"]]
    deployed = [r for r in evaluated if r["deployed"]]
    passed = [r for r in evaluated if r["functional_passed"]]
    # Final-assessment rows (``app_table(prices=...)``) also get the whole-cohort rates.
    itt = _itt_rates(rows, seed) if rows and "deployed_itt" in rows[0] else {}
    return {
        # Run counts; the rates below are over apps.
        "runs": len(rows),
        "apps": len(_units(rows)),
        "generated": len(generated),
        "truncated": sum(1 for r in generated if r["truncated"]),
        "analyzed": sum(1 for r in rows if r["analyzed"]),
        "evaluated": len(evaluated),
        "deployed": len(deployed),
        "functionally_correct": len(passed),
        "generation_rate": _app_rate(rows, lambda r: r["generated"], seed),
        "end_to_end_deploy_rate": _end_to_end_deploy_rate(rows, seed),
        "end_to_end_functional_bounds": _end_to_end_functional_bounds(rows),
        "deploy_rate": _app_rate(evaluated, lambda r: r["deployed"], seed),
        "functional_rate": _app_rate(evaluated, lambda r: r["functional_passed"], seed),
        # Of the apps that run, how many do what was asked.
        "functional_given_deployed": _app_rate(deployed, lambda r: r["functional_passed"], seed),
        "mean_pass_rate": _app_mean(evaluated, "pass_rate"),
        "mean_pass_rate_deployed": _app_mean(deployed, "pass_rate"),
        **itt,
    }


def funnel_section(rows, seed: int = 0, grouped_by: str = "model") -> dict[str, Any]:
    return {
        "overall": _funnel_row(rows, seed),
        "by_model": [
            {**_group_fields(k, v, grouped_by), **_funnel_row(v, seed)} for k, v in _group(rows, "group").items()
        ],
        "by_task": [{"task": k, **_funnel_row(v, seed)} for k, v in _group(rows, "task").items()],
    }


def _has(rows, key: str) -> bool:
    return any((r.get(key) or 0) > 0 for r in rows)


def security_bounds_section(rows, grouped_by: str = "model") -> dict[str, Any]:
    """Code security issues per normalized severity, with the apps affected and zero-count bounds.

    Only security/secret issues in the app's own non-test code
    (``security_normalized_<severity>``): dependency advisories, runtime
    (DAST) issues and test code are reported elsewhere. Apps are app units
    (repeats count once: an app is affected when any repeat has the issue);
    ``issues`` totals the runs.
    """
    analyzed = [r for r in rows if r["analyzed"]]

    def severity_rows(items):
        units = list(_units(items).values())
        pooled = len({r.get("group") or r.get("model") for r in items}) > 1
        out = []
        for s in SEVERITIES:
            key = f"security_normalized_{s}"
            apps = sum(1 for v in units if _has(v, key))
            out.append(
                {
                    "severity": s,
                    # Unmeasured is not zero: no analyzed app, no count.
                    "issues": sum(r.get(key) or 0 for r in items) if units else None,
                    "apps_affected": apps,
                    "apps": len(units),
                    "runs": len(items),
                    "apps_affected_ci95": None if pooled else inf.wilson_interval(apps, len(units)),
                    # Zero is not "none": with n apps the rate is only bounded.
                    "zero_rate_upper95": inf.zero_count_upper_bound(len(units)) if apps == 0 and not pooled else None,
                },
            )
        return out

    by_model = []
    for group, items in _group(analyzed, "group").items():
        sev = {r["severity"]: r for r in severity_rows(items)}
        units = list(_units(items).values())
        by_model.append(
            {
                **_group_fields(group, items, grouped_by),
                "apps": len(units),
                "runs": len(items),
                "critical_high_issues": sev["critical"]["issues"] + sev["high"]["issues"],
                "apps_with_critical_or_high": sum(
                    1 for v in units if _has(v, "security_normalized_critical") or _has(v, "security_normalized_high")
                ),
                "zero_critical_upper95": sev["critical"]["zero_rate_upper95"],
                "zero_high_upper95": sev["high"]["zero_rate_upper95"],
            },
        )
    return {
        "population": "code_security",
        "severity": "normalized",
        "apps_analyzed": len(_units(analyzed)),
        "runs_analyzed": len(analyzed),
        "overall": severity_rows(analyzed),
        "by_model": by_model,
        "interval_assumptions": (
            "Pooled endpoint-by-task counts are descriptive; pooled intervals are withheld. Per-group bounds "
            "assume independent exchangeable task units under a fixed endpoint and instrument, concern detected "
            "issues only, and do not establish population vulnerability risk or scanner sensitivity."
        ),
    }


_APP_MEANS = (
    "loc",
    "loc_backend",
    "loc_frontend",
    "files",
    "backend_share",
    "avg_complexity",
    "max_complexity",
    *TOOL_METRIC_KEYS,
)


def _app_rows(items) -> list[dict[str, Any]]:
    """One row per app with its repeats averaged: the observations of app-level summaries and correlations."""
    out = []
    for v in _units(items).values():
        analyzed = [r for r in v if r["analyzed"]]
        evaluated = [r for r in v if r["evaluated"]]
        code = _avg(r["code_unique_model"] for r in analyzed)
        loc = _avg(r["loc"] for r in analyzed)
        out.append(
            {
                "group": v[0]["group"],
                "task": v[0]["task"],
                "runs": len(v),
                **{k: _avg(r.get(k) for r in v) for k in _APP_MEANS},
                "analyzed": bool(analyzed),
                "evaluated": bool(evaluated),
                "unique_issues": _avg(r["unique_issues"] for r in analyzed),
                "issues_per_kloc": round(code * 1000 / loc, 4) if code is not None and loc else None,
                "pass_rate": _avg(r["pass_rate"] for r in evaluated),
            },
        )
    return out


def _app_correlation(rows, x, y, seed) -> dict[str, Any]:
    pairs = [(r[x], r[y]) for r in rows if r[x] is not None and r[y] is not None]
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    pooled = len({r.get("group") for r in rows}) > 1
    return {
        "x": x,
        "y": y,
        "n_apps": len(pairs),
        "rho": inf.spearman(xs, ys),
        "p_permutation": None if pooled else inf.spearman_permutation_p(xs, ys, seed=seed, draws=2000),
        "ci95": None if pooled else inf.spearman_bootstrap_ci(xs, ys, seed=seed, draws=1000),
        "inference": "descriptive_crossed_groups" if pooled else "conditional_exchangeable_tasks",
    }


def code_metrics_section(rows, seed: int, grouped_by: str = "model") -> dict[str, Any]:
    generated = [r for r in rows if r["generated"]]
    apps = _app_rows(generated)

    def summary(items):
        def values(key):
            return [r[key] for r in items if r[key] is not None]

        return {
            "apps": len(items),
            "runs": sum(r["runs"] for r in items),
            "loc": inf.describe(values("loc")),
            "loc_backend": inf.describe(values("loc_backend")),
            "loc_frontend": inf.describe(values("loc_frontend")),
            "files": inf.describe(values("files")),
            "backend_share": inf.describe(values("backend_share")),
            "avg_complexity": inf.describe(values("avg_complexity")),
            "max_complexity": inf.describe(values("max_complexity")),
            # lizard (all languages) and jscpd, as the tools printed them; n = apps they measured.
            **{k: inf.describe(values(k)) for k in TOOL_METRIC_KEYS},
        }

    spread = []
    for task, items in _group(apps, "task").items():
        per_model = {m: _mean(r["loc"] for r in v) for m, v in _group(items, "group").items()}
        if not task or len(per_model) < 2:  # a spread needs two groups on one task
            continue
        cx = {m: _mean(r["avg_complexity"] for r in v) for m, v in _group(items, "group").items()}
        spread.append(
            {
                "task": task,
                "models": len(per_model),
                "loc_cv_across_models": inf.coefficient_of_variation([v for v in per_model.values() if v is not None]),
                "complexity_cv_across_models": inf.coefficient_of_variation([v for v in cx.values() if v is not None]),
                "loc_min_model": min(per_model, key=lambda m: per_model[m] or 0) if per_model else None,
                "loc_max_model": max(per_model, key=lambda m: per_model[m] or 0) if per_model else None,
            },
        )
    by_group = _group(generated, "group")
    analyzed = [r for r in apps if r["analyzed"]]
    return {
        "overall": summary(apps),
        "by_model": [
            {**_group_fields(k, by_group[k], grouped_by), **summary(v)} for k, v in _group(apps, "group").items()
        ],
        "by_task": [{"task": k, **summary(v)} for k, v in _group(apps, "task").items()],
        "spread_across_models": spread,
        # App-level (each app's repeats averaged), so n = apps; apps of one model are not independent.
        "app_level_correlations": [
            _app_correlation(analyzed, "loc", "unique_issues", seed),
            _app_correlation(analyzed, "avg_complexity", "issues_per_kloc", seed),
            _app_correlation([r for r in apps if r["evaluated"]], "loc", "pass_rate", seed),
        ],
        "note": (
            "Per app (repeats averaged; LOC excludes test files). Pooled correlations across fixed groups are "
            "descriptive: p-values and intervals are withheld because tasks cross groups and within-group and "
            "between-group associations are mixed. Single-group inference assumes exchangeable independent tasks."
        ),
    }


def _model_parameters(runs) -> dict[str, dict[str, Any]]:
    """One predictor per model, preferring recorded endpoint policies and prices.

    Different observed values under one model cannot form one model-level predictor.
    Nominal catalog specifications are labelled as current metadata, not historical facts.
    """
    from backend.generation.models import RunManifest

    manifests = dict(RunManifest.objects.filter(run_id__in=[r.id for r in runs]).values_list("run_id", "manifest"))
    observed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        m = run.model
        if m is None:
            continue
        caps = m.capabilities_json if isinstance(m.capabilities_json, dict) else {}
        nominal = caps.get("context_length") or m.context_window or None
        frozen = manifests.get(run.id) or {}
        model = frozen.get("model") or {}
        endpoint = model.get("endpoint") or {}
        pricing = model.get("pricing") or {}
        policy = ((frozen.get("resources") or {}).get("effective") or {}).get("content") or {}
        values = {
            "context_window_nominal": float(nominal) if nominal else None,
            "context_window_effective": endpoint.get("num_ctx")
            if frozen
            else (m.num_ctx_override or m.context_window or nominal),
            "max_output_tokens": float(m.max_output_tokens) if m.max_output_tokens else None,
            "context_budget_tokens": (policy.get("context") or {}).get("max_context_tokens"),
            "call_output_cap_tokens": (policy.get("call") or {}).get("max_output_tokens"),
            "input_price_per_mtok": pricing.get("input_per_mtok")
            if frozen
            else (round(m.input_price_per_token * 1e6, 4) if m.input_price_per_token is not None else None),
            "output_price_per_mtok": pricing.get("output_per_mtok")
            if frozen
            else (round(m.output_price_per_token * 1e6, 4) if m.output_price_per_token is not None else None),
        }
        sources = dict.fromkeys(values, "current_catalog")
        for key in ("context_window_effective", "input_price_per_mtok", "output_price_per_mtok"):
            sources[key] = "run_manifest" if frozen else "current_catalog"
        sources.update(context_budget_tokens="run_manifest", call_output_cap_tokens="run_manifest")
        observed[m.model_id].append({"values": values, "sources": sources})
    params = {}
    for model, entries in sorted(observed.items()):
        row: dict[str, Any] = {}
        values_seen = {}
        source = {}
        for key in entries[0]["values"]:
            values = [entry["values"][key] for entry in entries]
            distinct = sorted({float(v) for v in values if v is not None})
            values_seen[key] = distinct
            # A partially recorded or varying predictor cannot represent every run of a model.
            row[key] = distinct[0] if len(distinct) == 1 and all(v is not None for v in values) else None
            source[key] = sorted({entry["sources"][key] for entry in entries})
        params[model] = {
            **row,
            "sources": source,
            "observed_values": values_seen,
            "varying_predictors": [key for key, values in values_seen.items() if len(values) > 1],
        }
    return params


def model_level_criteria(
    criteria_by_group: dict[str, dict[str, float | None]],
    by_group: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, float | None]]:
    """One point per model for the model-parameter correlations: the mean of its groups' values.

    Grouped by model this is each model's own row. Grouped by condition, the
    arms of one model share every predictor (its context window and prices),
    so feeding them to a correlation as separate points would count the same
    model several times; each model gets the mean of its arms' values instead
    (each arm weighted equally).
    """
    arms: dict[str, list[dict[str, float | None]]] = defaultdict(list)
    for group, criteria in criteria_by_group.items():
        model = by_group[group][0]["model"]
        if model:
            arms[model].append(criteria)
    return {model: {k: _mean(c[k] for c in values) for k in values[0]} for model, values in sorted(arms.items())}


PREDICTORS = (
    "context_window_nominal",
    "context_window_effective",
    "max_output_tokens",
    "context_budget_tokens",
    "call_output_cap_tokens",
    "input_price_per_mtok",
    "output_price_per_mtok",
)
OUTCOMES = ("deploy_rate", "pass_rate", "issues_per_kloc", "security_per_app")


def correlations_section(params, criteria_by_model, seed: int, *, arms_averaged: bool = False) -> dict[str, Any]:
    """Model parameters vs outcomes, one point per model (*criteria_by_model* keyed by model id).

    *arms_averaged*: the outcomes are the means of each model's conditions
    (:func:`model_level_criteria` under condition grouping), said in the notes.
    """
    models = sorted(set(params) & set(criteria_by_model))
    tests = []
    for predictor in PREDICTORS:
        for outcome in OUTCOMES:
            pairs = [
                (params[m][predictor], criteria_by_model[m][outcome], m)
                for m in models
                if params[m].get(predictor) is not None and criteria_by_model[m][outcome] is not None
            ]
            xs, ys, labels = [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs]
            tests.append(
                {
                    "predictor": predictor,
                    "outcome": outcome,
                    "n_models": len(pairs),
                    "rho": inf.spearman(xs, ys),
                    "p_permutation": inf.spearman_permutation_p(xs, ys, seed=seed, digits=None),
                    "ci95_bootstrap": inf.spearman_bootstrap_ci(xs, ys, seed=seed),
                    "leave_one_out": inf.leave_one_out(xs, ys, labels),
                },
            )
    p_values = [t["p_permutation"] for t in tests]
    for t, holm_p, bh_q in zip(tests, inf.holm(p_values), inf.benjamini_hochberg(p_values), strict=True):
        t["p_permutation"] = inf.round_p(t["p_permutation"])
        t["p_holm"] = holm_p
        t["q_bh"] = bh_q
    differs = sorted(
        m
        for m in models
        if params[m]["context_window_nominal"]
        and params[m]["context_window_effective"]
        and params[m]["context_window_nominal"] != params[m]["context_window_effective"]
    )
    return {
        "status": "exploratory",
        # The m Holm and BH corrected over: tests without a p (too few models, a constant variable) are not in it.
        "family_size": inf.family_size(p_values),
        "n_models": len(models),
        "tests": tests,
        "model_parameters": [{"model": m, **params[m]} for m in models],
        "effective_context_differs": differs,
        "arms_averaged": arms_averaged,
        "notes": [
            "Observational, model-level (n = number of models): association, not cause. Inference assumes "
            "exchangeable endpoint/model units; the selected convenience sample is not a random model population.",
            (
                "Predictor sources are exported per model. Current-catalog specifications are not frozen "
                "historical measurements."
            ),
            (
                "Endpoint context is unknown when a manifest does not record it. Context policy and output caps "
                "are separate predictors, not nominal model capacities."
            ),
            (
                "Varying or incompletely recorded predictors are excluded from model-level tests; "
                "observed values remain in the export."
            ),
            *(
                [
                    "A model that ran under several conditions is one point: the mean of its conditions' values "
                    "(arms share the model's parameters, so they are not separate observations).",
                ]
                if arms_averaged
                else []
            ),
            "Predictors are not independent (larger, newer models tend to have larger windows and prices).",
            (
                "p_holm is the primary familywise correction across all predictor x outcome tests. q_bh is "
                "a sensitivity summary: its FDR guarantee requires independence or appropriate positive "
                "dependence, which is not established for these correlated predictors and outcomes."
            ),
            "leave_one_out.sign_flips marks a correlation that rests on a single model.",
        ],
    }


def _resampler(rows_by_model):
    """Resample shared task/slot blocks jointly across the fixed groups, retaining observed missingness.

    A block contains every observed group's app on that task; repeats remain
    averaged within app. Incomplete blocks remain incomplete, so this assesses
    the observed cohort's task-composition sensitivity, not missing outcomes.
    """
    stats = {
        group: {
            (v[0].get("kind"), v[0].get("unit_key") or v[0]["run_id"]): _app_stats(v) for v in _units(rows).values()
        }
        for group, rows in rows_by_model.items()
    }
    blocks = sorted({key for apps in stats.values() for key in apps}, key=str)

    def resample(rng: random.Random) -> dict[str, dict[str, float | None]]:
        selected = [rng.choice(blocks) for _ in blocks]
        return {group: _combine([apps[key] for key in selected if key in apps]) for group, apps in stats.items()}

    resample.design = {
        "method": "shared_task_block_bootstrap",
        "blocks": len(blocks),
        "complete_blocks": sum(all(key in apps for apps in stats.values()) for key in blocks),
        "note": "Task/slot blocks are sampled jointly across fixed groups; repeats stay averaged within app. "
        "This assumes exchangeable tasks and retains observed missingness; it does not generalize to new endpoints.",
    }

    return resample


def ranking_draws(config):
    """Bound report simulation work while retaining useful probability precision."""
    value = config.get("ranking_draws", 5000)
    if isinstance(value, bool) or not isinstance(value, int) or not 500 <= value <= 20_000:
        msg = "ranking_draws must be an integer between 500 and 20000"
        raise ValueError(msg)
    return value


def _conditions(study: Study) -> list:
    from backend.reports.services.generators import study_conditions

    return study_conditions(study)


def design_state(study: Study) -> dict[str, Any]:
    """Where the study's design stands: planned repeats, excluded runs, stopped cells, interim, locked plan."""
    runs = GenerationRun.objects.filter(study=study)
    stopped_cells = study.cells.filter(
        stage=choices.Stage.MAIN,
        state=choices.CellState.EXCLUDED,
        exclusion_reason__in=_STOPPED_CELLS,
    ).count()
    return {
        "planned_repeats": study.repeats,
        # Never in the sample (current runs exclude them); counted for the record.
        "beyond_plan_excluded": runs.filter(beyond_plan=True).count(),
        "pilot_excluded": runs.filter(cell__stage=choices.Stage.PILOT).count(),
        "stopped_cells": stopped_cells,
        "stopped_early": study.status == choices.StudyStatus.STOPPED or stopped_cells > 0,
        "interim": study.status not in _TERMINAL,
        "plan_locked": bool(study.analysis_plan) and study.locked_at is not None,
    }


def exploratory_reasons(plan: dict[str, Any], design: dict[str, Any] | None) -> list[str]:
    """Why the declared metrics and the ranking are not confirmatory here (empty: they are).

    Confirmatory needs the plan's own analysis on the planned sample: a plan
    locked at launch, a finished study and every planned cell run (runs beyond
    the plan and pilot runs are never in the sample).
    """
    if not plan or design is None:
        return ["no pre-registered analysis plan"]
    out = []
    if not design["plan_locked"]:
        out.append("the analysis plan was never locked at launch")
    if design["interim"]:
        out.append("interim: the study has not finished")
    if design["stopped_early"]:
        out.append("stopped early: planned cells were cancelled")
    if design.get("evaluation_suite_changed"):
        out.append("the evaluation suite differs from the planned instrument")
    return out


def _app_key(r: dict[str, Any]) -> Any:
    """The app a run of a study condition measures (its unit; a run without one is its own app)."""
    return r["unit_key"] or r["run_id"]


def planned_comparisons_section(
    rows,
    study: Study | None,
    plan: dict[str, Any],
    design: dict[str, Any] | None = None,
    *,
    seed: int = 0,
) -> dict[str, Any] | None:
    """Every declared plan metric, each condition vs the pre-registered baseline, blocked on the app.

    The family covers the primary, secondary and research-question metrics
    (``declared_metrics``). With the plan's ``multiplicity = "all"`` one
    correction covers every tested (metric, condition) pair (the default);
    ``per_metric`` corrects each metric apart.
    ``family_size`` is the m the correction used: eligible comparisons with
    all-zero differences receive p=1 and remain in it. The correction and ``significant``
    use the unrounded p-values; the stored ones are rounded.

    A comparison that cannot be run is listed but not tested and stays out
    of the correction (``skipped``): ``below_min_apps`` (fewer analyzable
    apps than the plan's ``min_apps_per_condition``), ``no_baseline_data``
    (the baseline has no value for the metric) or ``no_pairs`` (no app has a
    value in both). Only the sample planned at launch counts: runs beyond the
    plan are never current runs (``beyond_plan_excluded`` counts them). A
    family is ``confirmatory`` only under a plan locked at launch and when no
    planned cell was cancelled or stopped (``stopped_early``).
    """
    conditions = _conditions(study) if study is not None else []
    baseline = next((c for c in conditions if c.is_baseline), None)
    if study is None or not plan or not plan.get("primary_metric") or baseline is None:
        return None
    design = design or design_state(study)
    primary = plan["primary_metric"]
    secondary = plan.get("secondary_metrics", [])
    questions_by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in plan.get("research_questions", []):
        questions_by_metric[q["metric"]].append(q)
    metrics = list(dict.fromkeys([primary, *secondary, *questions_by_metric]))
    min_apps = int(plan.get("min_apps_per_condition") or 0)
    multiplicity = plan_multiplicity(plan)
    planned = design["planned_repeats"]
    in_design = [r for r in rows if r["condition_id"] is not None and (r.get("repeat_index") or 0) < planned]
    stopped_early = design["stopped_early"]
    by_condition = _group(in_design, "condition_id")
    interim = design["interim"]
    alpha = plan.get("alpha", 0.05)
    correction = plan.get("correction", "holm")
    base_id = baseline.id
    labels = {c.id: condition_label(c) for c in conditions}

    def adjust(ps):
        # Unrounded: the decision must not depend on display rounding.
        if correction == "holm":
            return inf.holm(ps, digits=None)
        if correction == "bh":
            return inf.benjamini_hochberg(ps, digits=None)
        return list(ps)

    families = []
    all_common: set = set()
    for metric in metrics:
        # Common cells per metric, as on the Results tab: an (app, repeat) with
        # no value in one condition (failed, excluded, not analyzed) is dropped
        # for every condition, so a missing value cannot select on one arm.
        valued = {
            cid: {(_app_key(r), r["repeat_index"]): r for r in items if model_criteria([r])[metric] is not None}
            for cid, items in by_condition.items()
        }
        populated = [set(cells) for cells in valued.values() if cells]
        baseline_valued = bool(valued.get(base_id))
        # Without baseline values nothing can be paired: no common cells at all.
        common_cells = set.intersection(*populated) if populated and baseline_valued else set()
        all_common |= common_cells

        def per_app(cid, metric=metric, common_cells=common_cells, valued=valued):
            kept = [r for key, r in valued.get(cid, {}).items() if key in common_cells]
            groups: dict[Any, list] = defaultdict(list)
            for r in kept:
                groups[_app_key(r)].append(r)
            return {app: model_criteria(rs)[metric] for app, rs in groups.items()}

        # A one-sided test only when every question on this metric declares the same direction.
        directions = {q["direction"] for q in questions_by_metric.get(metric, [])}
        alternative = directions.pop() if len(directions) == 1 else "two-sided"
        base = per_app(base_id)
        comparisons = []
        for cid in by_condition:
            if cid == base_id:
                continue
            other = per_app(cid)
            untested = {
                "condition_id": cid,
                "condition": labels.get(cid, str(cid)),
                "n_apps": len(other),
                "n_apps_baseline": len(base),
                "p_adjusted": None,
                "significant": None,
            }
            if not baseline_valued:
                n_valued = len({key[0] for key in valued.get(cid, {})})
                comparisons.append({**untested, "n_apps": n_valued, "n_pairs": 0, "skipped": "no_baseline_data"})
                continue
            if min_apps and (len(other) < min_apps or len(base) < min_apps):
                comparisons.append(
                    {**untested, "n_pairs": len(set(base) & set(other)), "skipped": "below_min_apps"},
                )
                continue
            common = sorted(set(base) & set(other), key=str)
            if not common:
                n_valued = len({key[0] for key in valued.get(cid, {})})
                comparisons.append({**untested, "n_apps": n_valued, "n_pairs": 0, "skipped": "no_pairs"})
                continue
            deltas = [paired_difference(base[a], other[a]) for a in common]
            wins = sum(1 for d in deltas if d > 0)
            losses = sum(1 for d in deltas if d < 0)
            all_zero = not wins and not losses
            p_raw = 1.0 if all_zero else wilcoxon_p(deltas, alternative)
            comparisons.append(
                {
                    "condition_id": cid,
                    "condition": labels.get(cid, str(cid)),
                    "n_pairs": len(common),
                    "mean_delta": _mean(deltas),
                    "mean_delta_ci95": paired_mean_ci(deltas, seed=seed),
                    "median_delta": inf.percentile(deltas, 0.5),
                    "wins": wins,
                    "losses": losses,
                    "all_zero": all_zero,
                    "p_sign": 1.0 if all_zero else inf.round_p(sign_test_p(wins, losses)),
                    "p_wilcoxon": inf.round_p(p_raw),
                    "_p": p_raw,
                },
            )
        role = "primary" if metric == primary else "secondary" if metric in secondary else "research_question"
        families.append(
            {
                "metric": metric,
                "role": role,
                "status": "confirmatory" if design["plan_locked"] and not stopped_early else "exploratory",
                "alternative": alternative,
                "common_cells": len(common_cells),
                "research_questions": [
                    {"id": q["id"], "direction": q["direction"]} for q in questions_by_metric.get(metric, [])
                ],
                "comparisons": comparisons,
            },
        )

    # The correction: one family over every tested comparison, or one per metric.
    groups = [[c for f in families for c in f["comparisons"] if not c.get("skipped")]]
    if multiplicity == "per_metric":
        groups = [[c for c in f["comparisons"] if not c.get("skipped")] for f in families]
    family_size = 0
    for tested in groups:
        raw = [c.pop("_p") for c in tested]
        family_size += inf.family_size(raw)
        for c, p_raw, adj in zip(tested, raw, adjust(raw), strict=True):
            c["in_family"] = p_raw is not None
            c["p_adjusted"] = inf.round_p(adj)
            c["significant"] = adj is not None and adj < alpha
            if interim:
                # Peeking at significance mid-run invites optional stopping.
                c.update(p_sign=None, p_wilcoxon=None, p_adjusted=None, significant=None)
    return {
        "baseline_condition_id": base_id,
        "baseline_condition": labels.get(base_id, str(base_id)),
        "alpha": alpha,
        "correction": correction,
        "multiplicity": multiplicity,
        "family_size": family_size,
        "comparisons_tested": sum(len(g) for g in groups),
        "effect_interval": (
            f"95% paired-app percentile bootstrap, seed {seed}; not multiplicity-adjusted; absent below 3 apps"
        ),
        "min_apps_per_condition": min_apps,
        "interim": interim,
        "planned_repeats": planned,
        "beyond_plan_excluded": design["beyond_plan_excluded"],
        "stopped_early": stopped_early,
        "common_cells": len(all_common),
        "families": families,
        "notes": [
            "Paired differences are rounded to twelve decimals before ranking. Signed ranks use the exact "
            "conditional sign-flip distribution through 50 nonzero pairs, retaining tied midranks; larger "
            "samples use a tie/continuity-corrected normal approximation.",
            "Signed-rank inference assumes independent symmetric task differences. All-zero eligible "
            "comparisons receive p=1 and remain in the correction family; this does not establish equivalence.",
        ],
    }


def _fingerprint(rows) -> str:
    payload = json.dumps(
        sorted([r["run_id"], r["status"], r["evaluation_status"], r["unique_issues"]] for r in rows),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _instruments(result_ids) -> dict[str, dict[str, Counter]]:
    """Per tool, what the counted results were produced with, as recorded on each result (not the catalog pins).

    ``versions`` (the installed build), ``configs`` (the options), ``commands``
    (``summary.command_sha``: the rendered command with the app URL
    normalized, or the AI reviewer's prompt) and ``parsers``
    (``summary.parser_sha``) and ``patterns`` (``summary.patterns_sha``, the
    file patterns that decide applicability). Results stored before the command hash existed
    get it from their recorded command; results with no command (skipped,
    AI reviewers of that time) and without a parser hash are not counted.
    """
    from backend.analysis.services.runner import command_fingerprint

    observed: dict[str, dict[str, Counter]] = defaultdict(
        lambda: {
            "versions": Counter(),
            "configs": Counter(),
            "commands": Counter(),
            "parsers": Counter(),
            "patterns": Counter(),
        },
    )
    legacy: list = []
    for result_id, slug, raw_summary in ToolResult.objects.filter(id__in=result_ids).values_list(
        "id",
        "tool_slug",
        "summary",
    ):
        summary = raw_summary if isinstance(raw_summary, dict) else {}
        entry = observed[slug]
        if summary.get("tool_version"):
            entry["versions"][str(summary["tool_version"])] += 1
        if summary.get("config"):
            entry["configs"][json.dumps(summary["config"], sort_keys=True)] += 1
        if summary.get("command_sha"):
            entry["commands"][str(summary["command_sha"])] += 1
        else:
            legacy.append(result_id)
        if summary.get("parser_sha"):
            entry["parsers"][str(summary["parser_sha"])] += 1
        if summary.get("patterns_sha"):
            entry["patterns"][str(summary["patterns_sha"])] += 1
    if legacy:
        # Only the two keys, never the whole raw output (it can hold megabytes of tool output).
        for slug, command, url in ToolResult.objects.filter(id__in=legacy).values_list(
            "tool_slug",
            "raw_output__command",
            "raw_output__target_url",
        ):
            if isinstance(command, str) and command:
                observed[slug]["commands"][command_fingerprint(command, url if isinstance(url, str) else "")] += 1
    return observed


def protocol_section(
    runs,
    rows,
    study: Study | None,
    config: dict[str, Any],
    *,
    grouped_by: str = "model",
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_ids = ToolResult.latest_ids_per_job([r.id for r in runs])
    observed = _instruments(result_ids)
    tools_used = sorted(observed)
    versions = dict(AnalyzerTool.objects.filter(slug__in=tools_used).values_list("slug", "version"))
    evaluators = Counter(
        f"{r['eval_suite']}/tester-{r['tester_version']}" for r in rows if r["evaluated"] and r["eval_suite"]
    )
    by_group = _group(rows, "group")
    cells = Counter((r["group"], r["task"]) for r in rows)
    fingerprints: dict[tuple, set] = defaultdict(set)
    for r in rows:
        if r["config_fingerprint"]:
            fingerprints[(r["condition_id"], r["unit_key"] or r["run_id"])].add(r["config_fingerprint"])
    conditions = _conditions(study) if study is not None else []
    baseline = next((c for c in conditions if c.is_baseline), None)
    return {
        "report_version": REPORT_VERSION,
        "generated_at": timezone.now().isoformat(),
        "scope": {k: config.get(k) for k in ("study_id", "run_ids")},
        "study": None
        if study is None
        else {
            "id": str(study.id),
            "name": study.name,
            "hypothesis": study.hypothesis,
            "kind": study.kind,
            "mode": study.mode,
            "status": study.status,
            "repeats": study.repeats,
            "planned_repeats": (design or {}).get("planned_repeats"),
            "base_seed": study.base_seed,
            "baseline_condition_id": baseline.id if baseline is not None else None,
            "baseline_condition": condition_label(baseline) if baseline is not None else None,
            "analysis_plan": study.analysis_plan or None,
            "analysis_plan_hash": study.analysis_plan_hash or None,
            "prereg_hash": study.prereg_hash or None,
            "locked_at": study.locked_at.isoformat() if study.locked_at else None,
        },
        "runs": len(rows),
        "apps": len(_units(rows)),
        "grouped_by": grouped_by,
        "groups": sorted(g for g in by_group if g),
        "models": sorted({r["model"] for r in rows if r["model"]}),
        "tasks": sorted({r["task"] for r in rows if r["task"]}),
        "kinds": dict(Counter(r["kind"] for r in rows)),
        "stacks": dict(Counter(r["stack"] or "(none)" for r in rows)),
        "starters": dict(Counter(r["starter"] or "(none)" for r in rows)),
        "harnesses": dict(Counter(r["harness"] or "(none)" for r in rows)),
        "prompt_versions": len({r["prompt_hash"] for r in rows if r["prompt_hash"]}),
        "config_fingerprints": len({r["config_fingerprint"] for r in rows if r["config_fingerprint"]}),
        # (condition, unit) groups whose repeats ran under more than one configuration fingerprint.
        "mixed_fingerprint_cells": sum(1 for v in fingerprints.values() if len(v) > 1),
        # Group sizes: how many groups have this many apps (app units) / runs.
        "apps_per_model": dict(Counter(len(_units(v)) for v in by_group.values())),
        "runs_per_model": dict(Counter(len(v) for v in by_group.values())),
        "trials_per_cell": dict(Counter(cells.values())),
        "tools": [
            {
                "slug": t,
                "version": versions.get(t, ""),
                "installed_versions": dict(observed[t]["versions"]),
                "configs": [json.loads(c) for c in observed[t]["configs"]],
                # Fingerprints of the rendered command and of the parser (results per fingerprint).
                "commands": dict(observed[t]["commands"]),
                "parsers": dict(observed[t]["parsers"]),
                # File patterns decide measured (completed) vs not_applicable.
                "patterns": dict(observed[t]["patterns"]),
            }
            for t in tools_used
        ],
        "evaluators": dict(evaluators),
        "settings": {
            "include_ai": bool(config.get("include_ai")),
            "line_tolerance": int(config.get("line_tolerance", 2)),
            # What counts as the delivered app (tests and scratch scripts apart by default).
            "code_scope": config.get("code_scope"),
            "exclude_truncated": bool(config.get("exclude_truncated", False)),
            "seed": int(config.get("seed", 0)),
            "ranking_draws": ranking_draws(config),
            "eval_suite": config.get("eval_suite"),
        },
        "data_fingerprint": _fingerprint(rows),
    }


def _tools_dropped_message(coverage: dict[str, Any], skip: set[str]) -> str | None:
    """The tools left out of every count because they did not measure every analyzed run, and why."""
    partial = {t: v for t, v in (coverage.get("tools_partial") or {}).items() if t not in skip}
    if not partial:
        return None
    total = coverage.get("apps")
    parts = []
    for tool, info in sorted(partial.items()):
        reasons = {k: v for k, v in info.items() if k not in ("apps_measured", "apps_missing")}
        not_run = (info.get("apps_missing") or 0) - sum(reasons.values())
        why = [f"{v} {str(k).replace('_', ' ')}" for k, v in sorted(reasons.items())]
        if not_run > 0:
            why.append(f"not run on {not_run}")
        parts.append(f"{tool} ({info.get('apps_measured')} of {total} runs measured; {', '.join(why)})")
    return (
        f"{len(partial)} tool(s) did not measure every analyzed run and are left out of every issue count, so all "
        f"apps share one instrument set: {'; '.join(parts)}. Re-run them on the missing apps to count them."
    )


def validity_section(
    rows,
    *,
    protocol,
    issues,
    accounting,
    correlations,
    security,
    plan,
    config,
    planned=None,
    ranking=None,
    sample_selection=None,
    group_criteria=None,
) -> list[dict[str, Any]]:
    """Threats to validity detected in this data (``level``: warning | info).

    *sample_selection*: the ``selection`` section of a cross-study report (``cohort_selection``), whose own
    checks replace ``mixed_study_scope`` (pooling studies is the point there, and its rule is stated).
    *group_criteria*: per group, the metrics with the final-assessment columns (:func:`final_assessment_threats`).
    """
    out = []

    def add(code, level, section, message):
        out.append({"code": code, "level": level, "section": section, "message": message})

    for entry in selection_warnings(sample_selection):
        add(*entry)

    selection = protocol.get("evaluation_selection") or {}
    if selection.get("changed_from_plan_runs"):
        add(
            "evaluation_suite_changed",
            "info",
            "protocol",
            f"{selection['changed_from_plan_runs']} run(s) are reanalyzed under {selection['suite_ref']} "
            "instead of the originally planned instrument. This is a versioned exploratory reanalysis; "
            "the original generation sample and source remain unchanged, and other suites never fill missing grades.",
        )
    if ((protocol.get("execution") or {}).get("coverage") or {}).get("mixed_study_scope") and not sample_selection:
        add(
            "mixed_study_scope",
            "warning",
            "all",
            "This scope combines runs from different studies or ad hoc runs. Their tasks, conditions and "
            "selection rules can differ; inspect the coverage grid and report each planned cohort separately.",
        )
    n_models = len(protocol["models"])
    if n_models and n_models < SMALL_N_MODELS:
        plural = "" if n_models == 1 else "s"
        if (ranking or {}).get("status") == "confirmatory":
            message = (
                f"Only {n_models} model{plural}: model-level correlations are exploratory, and the pre-registered "
                f"ranking describes these {n_models} model{plural} only (wide rank intervals; it does not "
                "generalise to other models)."
            )
        else:
            message = f"Only {n_models} model{plural}: model-level correlations and rankings are exploratory."
        add("small_n_models", "warning", "correlations, ranking", message)
    by_group = _group(rows, "group")
    unit = "condition" if protocol.get("grouped_by") == "condition" else "model"
    thin = sorted(
        g for g, v in by_group.items() if g and len(_units([r for r in v if r["evaluated"]])) < MIN_APPS_PER_MODEL
    )
    if thin:
        add(
            "few_apps",
            "warning",
            "funnel, ranking",
            f"Fewer than {MIN_APPS_PER_MODEL} evaluated apps (repeats count once) for: {', '.join(thin)}.",
        )
    counts = {len(_units(v)) for v in by_group.values()}
    if len(counts) > 1:
        add(
            "unbalanced",
            "warning",
            "all",
            f"{unit.capitalize()}s have unequal numbers of apps; compare rates, not totals.",
        )
    truncated = protocol.get("truncated_excluded", 0) + sum(1 for r in rows if r["truncated"])
    if truncated:
        add(
            "truncated",
            "warning",
            "all",
            f"{truncated} run(s) were recorded as truncated: "
            f"{sum(1 for r in rows if r['truncated'])} remain in this analysis and "
            f"{protocol.get('truncated_excluded', 0)} were excluded. "
            "A truncation flag can indicate a token, turn, budget or time limit; "
            "it does not identify a single cause or establish that every output is incomplete.",
        )
    route_errors = sum(1 for r in rows if r.get("route_error_terminated"))
    if route_errors:
        add(
            "route_error_terminated",
            "warning",
            "all",
            f"{route_errors} generation(s) ended on an LLM route error (upstream 4xx/5xx or transport failure) "
            "after at least one successful call and were evaluated as the model's result; compare the rates "
            "with and without them (validity note route_error_terminated).",
        )
    unmeasured = [r for r in rows if r["evaluation_status"] in _EVAL_UNMEASURED]
    if unmeasured:
        known_failures = sum(
            r.get("outcome")
            in {choices.Outcome.OUTPUT_INVALID, choices.Outcome.BUILD_FAIL, choices.Outcome.START_FAIL}
            for r in unmeasured
        )
        add(
            "unmeasured_evaluations",
            "info",
            "funnel",
            f"{len(unmeasured)} evaluation(s) have no measured grade and are excluded from conditional graded-check "
            f"rates. {known_failures} have a known output/build/start failure; "
            f"{len(unmeasured) - known_failures} have an unknown all-check-pass outcome. "
            "Evaluation status alone does not distinguish application failure from a tester or platform error.",
        )
    attrition = protocol.get("attrition_excluded") or {}
    if attrition:
        n_attrition = sum(attrition.values())
        add(
            "attrition",
            "warning",
            "all",
            f"{n_attrition} cell(s) have no usable run ("
            + ", ".join(f"{n} {k}" for k, n in sorted(attrition.items()))
            + "): infra errors and exclusions are not model outcomes, so they are left out and reported here.",
        )
    not_evaluated = sum(1 for r in rows if r["generated"] and not r["evaluated"])
    if not_evaluated:
        add(
            "not_evaluated",
            "warning",
            "funnel",
            f"{not_evaluated} generated run(s) have no measured evaluation; conditional rates exclude them. "
            "Whole-cohort deployment uses final run outcomes, and functional bounds retain unknown grades.",
        )
    if group_criteria is not None:
        for entry in final_assessment_threats(rows, group_criteria, ranking or {}, protocol):
            add(*entry)
    mixed = protocol.get("mixed_fingerprint_cells") or 0
    if mixed:
        add(
            "mixed_fingerprints",
            "warning",
            "all",
            f"{mixed} (condition, unit) cell group(s) ran under more than one configuration fingerprint; "
            "their repeats are pooled across configurations.",
        )
    if len(protocol["kinds"]) > 1:
        add(
            "mixed_kinds",
            "warning",
            "all",
            f"Run kinds are pooled ({', '.join(sorted(protocol['kinds']))}); scope the report to one study.",
        )
    if len(protocol["stacks"]) == 1:
        add(
            "single_stack",
            "info",
            "all",
            f"One stack only ({next(iter(protocol['stacks']))}): results may not generalise to others.",
        )
    zero = [s["severity"] for s in security["overall"] if s["severity"] in ("critical", "high") and s["issues"] == 0]
    if zero and security["apps_analyzed"]:
        bound = next(s.get("zero_rate_upper95") for s in security["overall"] if s["severity"] in zero)
        inference_note = (
            f" The conditional one-sided 95% binomial upper bound is {bound:.1%}, assuming independent, "
            "exchangeable task units under the fixed endpoint and instrument."
            if bound is not None
            else " A pooled binomial confidence bound is withheld because endpoint-by-task observations "
            "are not independent trials."
        )
        add(
            "zero_severe",
            "warning",
            "security_bounds",
            f"No {' or '.join(zero)} code security issues (normalized severity) in {security['apps_analyzed']} "
            f"apps.{inference_note} Absence of detected findings does not establish zero vulnerability risk; "
            "the tasks or instruments may not detect these classes.",
        )
    params = issues.get("params") or {}
    if not config.get("include_ai"):
        add(
            "ai_excluded",
            "info",
            "issues",
            "AI-reviewer findings are excluded from issue counts (opinions, not measurements).",
        )
    elif not params.get("ai_counted"):
        add(
            "ai_excluded",
            "info",
            "issues",
            "AI-reviewer findings were requested, but no AI reviewer reviewed every analyzed app, so none are "
            "counted.",
        )
    if config.get("include_ai") and params.get("ai_partial"):
        add(
            "ai_partial",
            "warning",
            "issues",
            f"AI reviewer(s) {', '.join(params['ai_partial'])} did not review every analyzed app, so their findings "
            "are left out of the issue counts although AI findings were requested (one instrument set for all apps).",
        )
    dropped = _tools_dropped_message(issues.get("coverage") or {}, set(params.get("ai_partial") or []))
    if dropped:
        add("tools_dropped", "warning", "issues", dropped)
    if correlations["effective_context_differs"]:
        add(
            "effective_context",
            "warning",
            "correlations",
            "Effective (endpoint-limited) context differs from the nominal one for: "
            + ", ".join(correlations["effective_context_differs"])
            + ". Both are reported; do not cite one without the other.",
        )
    unstable = [t for t in correlations["tests"] if t["leave_one_out"].get("sign_flips")]
    if unstable:
        add(
            "loo_unstable",
            "warning",
            "correlations",
            f"{len(unstable)} correlation(s) change sign when one model is removed.",
        )
    if issues["totals"]["raw_findings"] and issues["totals"]["duplicate_findings"]:
        share = issues["totals"]["duplicate_findings"] / issues["totals"]["raw_findings"]
        add(
            "duplicates",
            "info",
            "issues",
            f"{share:.0%} of raw findings were duplicates of another tool's report and count once.",
        )
    dep_total = issues["totals"].get("dependency_unique") or 0
    if dep_total:
        inherited = issues["totals"].get("dependency_inherited")
        if inherited is None:  # payloads without the split column
            inherited = dep_total - (issues["totals"].get("dependency_added") or 0)
        add(
            "starter_dependencies",
            "warning" if inherited / dep_total >= 0.5 else "info",
            "issues, security_bounds",
            f"{inherited} of {dep_total} dependency advisories ({inherited / dep_total:.0%}) are in package versions "
            "the model did not choose (pinned by the starter, or transitive): they measure the starter more than "
            "the model. Advisories are kept out of code issues, issues per kLOC and the security bounds; compare "
            "models on the advisories in versions they chose.",
        )
    pass_values = {r["pass_rate"] for r in rows if r["evaluated"] and r["pass_rate"] is not None}
    n_graded = sum(1 for r in rows if r["evaluated"] and r["pass_rate"] is not None)
    if len(pass_values) == 1 and n_graded >= 2:
        add(
            "ceiling",
            "warning",
            "funnel, ranking, planned_comparisons",
            f"Every evaluated app scored a pass rate of {next(iter(pass_values)):g}: the evaluation suite does "
            "not discriminate between models here (ceiling/floor effect); comparisons on it cannot show a "
            "difference. Use harder tasks or another metric.",
        )
    tied = (ranking or {}).get("tied_groups") or []
    if (ranking or {}).get("models_tied") and tied:
        add(
            "ranking_tied",
            "warning",
            "ranking",
            f"Tied in the ranking (equal scores share the best rank of their group): "
            f"{'; '.join(' = '.join(g) for g in tied)}. The ranking does not order the {unit}s within a tie; "
            "their order in the table is not a result.",
        )
    multi_provider = sorted(
        g for g, v in by_group.items() if g and len({p for r in v for p in r.get("providers") or []}) > 1
    )
    if multi_provider:
        add(
            "mixed_providers",
            "warning",
            "all",
            f"Upstream provider varied across runs of: {', '.join(multi_provider)}. Providers can serve "
            "different quantizations and stacks; pin provider routing to hold it constant.",
        )
    if protocol.get("in_flight_excluded"):
        add(
            "in_flight",
            "warning",
            "all",
            f"{protocol['in_flight_excluded']} run(s) are still queued, running or waiting for evaluation and are "
            "left out; "
            "this is an interim view.",
        )
    if len(protocol.get("evaluators") or {}) > 1:
        add(
            "mixed_evaluators",
            "warning",
            "funnel, ranking, planned_comparisons",
            f"Apps were graded by {len(protocol['evaluators'])} different evaluation suite / tester versions "
            f"({', '.join(sorted(protocol['evaluators']))}); pass rates are pooled across them. Re-evaluate "
            "the older ones for a single instrument.",
        )
    # Only the tools whose findings are counted pool anything; a tool left out of every count does not.
    common = (protocol.get("instrument_coverage") or {}).get("tools_common")
    counted_tools = [t for t in protocol.get("tools") or [] if common is None or t["slug"] in set(common)]
    drifted = sorted(t["slug"] for t in counted_tools if len(t.get("installed_versions") or {}) > 1)
    if drifted:
        add(
            "mixed_tool_versions",
            "warning",
            "issues",
            f"Different versions of {', '.join(drifted)} measured different apps; "
            "issue counts are pooled across them.",
        )
    instruments = []
    for t in counted_tools:
        parts = [
            word
            for word, key in (
                ("commands", "commands"),
                ("parsers", "parsers"),
                ("options", "configs"),
                ("file patterns", "patterns"),
            )
            if len(t.get(key) or ()) > 1
        ]
        if parts:
            instruments.append(f"{t['slug']} ({', '.join(parts)})")
    if instruments:
        add(
            "mixed_tool_commands",
            "warning",
            "issues",
            f"Different commands, parsers, options or file patterns of {'; '.join(sorted(instruments))} "
            "measured different apps "
            "(the tool's catalog entry or parser changed between analyses); issue counts are pooled across them. "
            "Re-analyze the older apps for a single instrument.",
        )
    # The code-metrics section pools the metric tools' figures over every app they measured
    # (counted for issues or not): the same check, on the tools whose metrics are in the rows.
    tools_by_slug = {t["slug"]: t for t in protocol.get("tools") or []}
    metric_drift = []
    for slug, columns in _METRIC_TOOL_COLUMNS.items():
        tool = tools_by_slug.get(slug)
        if tool is None or not any(r.get(c) is not None for r in rows for c in columns):
            continue
        parts = [
            word
            for word, key in (
                ("versions", "installed_versions"),
                ("commands", "commands"),
                ("parsers", "parsers"),
                ("options", "configs"),
                ("file patterns", "patterns"),
            )
            if len(tool.get(key) or ()) > 1
        ]
        if parts:
            metric_drift.append(f"{slug} ({', '.join(parts)})")
    if metric_drift:
        add(
            "mixed_metric_instruments",
            "warning",
            "code_metrics",
            f"Different versions, commands, parsers, options or file patterns of {'; '.join(sorted(metric_drift))} "
            "produced the complexity / duplication figures of different apps; the code metrics are pooled across "
            "them. Re-analyze the older apps for a single instrument.",
        )
    unmeasured = (issues.get("coverage") or {}).get("apps_unmeasured") or []
    not_analyzed = sum(1 for r in rows if r["generated"] and not r["analyzed"] and r["run_id"] not in set(unmeasured))
    if not_analyzed:
        add(
            "not_analyzed",
            "warning",
            "issues, planned_comparisons",
            f"{not_analyzed} generated run(s) have no finished static analysis: their issue counts are missing (not "
            "zero) and every issue figure covers the analyzed runs only, a selected subset when analysis depended on "
            "the outcome (post-run only_passing). Analyze them to complete the sample.",
        )
    if unmeasured:
        add(
            "unmeasured_apps",
            "warning",
            "issues",
            f"{len(unmeasured)} analyzed run(s) have no successful static-tool result; their issue counts are "
            "missing (not zero).",
        )
    if not plan:
        add("no_plan", "info", "all", "No pre-registered analysis plan: every analysis here is exploratory.")
    elif not (protocol.get("study") or {}).get("locked_at"):
        add(
            "plan_not_locked",
            "warning",
            "all",
            "The analysis plan was never locked at launch: it was not pre-registered, so every analysis here "
            "is exploratory.",
        )
    if config.get("_exclusions_overridden"):
        add(
            "plan_exclusions",
            "info",
            "all",
            "The report's exclude_truncated setting differs from the analysis plan's exclusions; the plan's applies.",
        )
    families = (planned or {}).get("families", [])
    below = sorted(
        {c["condition"] for family in families for c in family["comparisons"] if c.get("skipped") == "below_min_apps"},
    )
    if planned and planned.get("stopped_early"):
        add(
            "stopped_early",
            "warning",
            "planned_comparisons",
            "Planned cells were cancelled or stopped before they ran: the planned sample was not collected, so the "
            "planned comparisons are exploratory (stopping on what the interim results showed would bias them). "
            "Top up the study to complete the design.",
        )
    if protocol.get("beyond_plan_excluded"):
        add(
            "design_extended",
            "info",
            "all",
            f"{protocol['beyond_plan_excluded']} run(s) beyond the plan (top-ups of cells that already had a usable "
            "run) are left out of every section.",
        )
    n_families = len(families)
    if planned and planned.get("multiplicity") == "per_metric" and n_families > 1:
        add(
            "multiplicity_per_metric",
            "warning",
            "planned_comparisons",
            f"The plan corrects each of its {n_families} metrics separately: the chance of at least one false "
            f"positive across them is up to {1 - (1 - planned['alpha']) ** n_families:.0%}, not "
            f"{planned['alpha']:.0%}. Read the secondary metrics as supporting evidence.",
        )
    if below:
        add(
            "below_min_apps",
            "warning",
            "planned_comparisons",
            f"Fewer than {planned['min_apps_per_condition']} apps (the plan's min_apps_per_condition) for: "
            f"{', '.join(below)}; those comparisons are not tested.",
        )
    untested = [
        f"{family['metric']}: {c['condition']}"
        + (" (no baseline values)" if c["skipped"] == "no_baseline_data" else "")
        for family in families
        for c in family["comparisons"]
        if c.get("skipped") in ("no_pairs", "no_baseline_data")
    ]
    if untested:
        add(
            "untested_comparisons",
            "warning",
            "planned_comparisons",
            "Not tested, because no app has a value in both the condition and the baseline: "
            f"{'; '.join(untested)}. These are missing tests, not null results.",
        )
    return out


def _failure_counts(items) -> str:
    counts = Counter(r["outcome"] for r in items if r.get("outcome") in _KNOWN_FAILURES)
    return ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))


def final_assessment_threats(
    rows,
    group_criteria: dict[str, dict[str, Any]],
    ranking: dict[str, Any],
    protocol: dict[str, Any],
) -> list[tuple[str, str, str, str]]:
    """Validity entries of the final-assessment metrics: ``(code, level, section, message)``.

    ``conditional_vs_itt`` names the groups whose conditional rates left known failures out;
    ``itt_unknown`` the runs neither measured nor known to fail; ``list_cost_unpriced`` the models without a
    list price; ``ranking_dropped`` the groups the ranking left out and the criteria they lack.
    """
    out = []
    by_group = _group(rows, "group")
    differing, any_changed = [], False
    for group, values in sorted(group_criteria.items()):
        changed = [
            f"{word} {_fmt_rate(values.get(key))} vs {_fmt_rate(values.get(f'{key}_itt'))}"
            for word, key in (("deploy", "deploy_rate"), ("pass", "pass_rate"))
            if values.get(key) is not None
            and values.get(f"{key}_itt") is not None
            and round(values[key], 4) != round(values[f"{key}_itt"], 4)
        ]
        any_changed = any_changed or bool(changed)
        failures = _failure_counts(by_group.get(group) or [])
        if changed or failures:
            differing.append(f"{group} ({failures or 'no known failures'}: {', '.join(changed) or 'rates equal'})")
    if any_changed:
        uses = {c["key"] for c in ranking.get("criteria") or []}
        basis = (
            " The ranking uses the whole-cohort rates."
            if {"deploy_rate_itt", "pass_rate_itt"} & uses
            else " The ranking uses the conditional rates; compare it with the whole-cohort rates."
            if {"deploy_rate", "pass_rate"} & uses
            else ""
        )
        out.append(
            (
                "conditional_vs_itt",
                "info",
                "funnel, model_outcomes, ranking",
                (
                    "Conditional rates (deploy_rate, pass_rate) are over measured evaluations only, so known "
                    "output/build/start failures are left out of them; the whole-cohort rates (deploy_rate_itt, "
                    "pass_rate_itt) count those failures as 0. Groups whose known failures the conditional rates "
                    f"exclude (conditional vs whole-cohort): {'; '.join(differing)}.{basis}"
                ),
            ),
        )
    no_deploy = [r for r in rows if r.get("deployed_itt") is None]
    no_grade = [r for r in rows if r.get("pass_rate_itt") is None]
    if no_deploy or no_grade:
        groups = Counter(r["group"] for r in {r["run_id"]: r for r in [*no_deploy, *no_grade]}.values())
        out.append(
            (
                "itt_unknown",
                "warning",
                "funnel, model_outcomes, ranking",
                f"{len(no_deploy)} run(s) have no known deployment outcome and {len(no_grade)} neither a measured "
                "grade nor a known output/build/start failure: they are left out of the whole-cohort rates "
                "(unknown, not failures), which are conditional on the rest; runs per group: "
                + ", ".join(f"{g} ({n})" for g, n in sorted(groups.items()))
                + ".",
            ),
        )
    basis = protocol.get("cost_basis") or {}
    unpriced = [m for m in basis.get("per_model") or [] if not m.get("priced")]
    if unpriced:
        out.append(
            (
                "list_cost_unpriced",
                "warning",
                "model_outcomes, ranking",
                "No list price for: "
                + "; ".join(f"{m['model']} ({m.get('missing')})" for m in unpriced)
                + ". Their cost at list price is unknown, not 0; a ranking on list_cost_per_app leaves them out.",
            ),
        )
    priced = {m["model"] for m in basis.get("per_model") or [] if m.get("priced")}
    untokened = Counter(r["group"] for r in rows if r["model"] in priced and r.get("list_cost") is None)
    if untokened:
        out.append(
            (
                "list_cost_tokens_missing",
                "warning",
                "model_outcomes, ranking",
                f"{sum(untokened.values())} run(s) of priced models recorded no token counts, so their cost at list "
                "price is unknown and left out of the cost per app: "
                + ", ".join(f"{g} ({n})" for g, n in sorted(untokened.items()))
                + ".",
            ),
        )
    keys = [c["key"] for c in ranking.get("criteria") or []]
    dropped = [
        f"{group} ({', '.join(k for k in keys if (group_criteria.get(group) or {}).get(k) is None)})"
        for group in ranking.get("dropped_missing_criteria") or []
    ]
    if dropped:
        out.append(
            (
                "ranking_dropped",
                "warning",
                "ranking",
                f"Left out of the ranking because a criterion has no value (never imputed): {'; '.join(dropped)}.",
            ),
        )
    return out


def _fmt_rate(value: float | None) -> str:
    return "-" if value is None else f"{value:.3g}"


def _issues_by_group(per_app, rows, grouped_by: str) -> list[dict[str, Any]]:
    """The deduplicated issue table per report group, labelled as the funnel and ranking are.

    ``issues.by_condition`` keys on the raw condition label and puts every
    run outside a study, whatever its model, in one row; this keys on
    each run's ``group`` (:func:`label_groups`). Counts come from the one
    issue breakdown of the whole scope, so every group shares its instrument set.
    """
    group_of = {r["run_id"]: r["group"] for r in rows}
    groups: dict[str, list] = defaultdict(list)
    for app in per_app:
        if group_of.get(app["run_id"]):
            groups[group_of[app["run_id"]]].append(app)
    return [{**_group_fields(g, v, grouped_by), **group_summary(v)} for g, v in sorted(groups.items())]


def group_tool_measurements(rows, grouped_by: str, *, common_tools=(), include_ai: bool = False):
    """Raw tool counts by group, with equal app weights and explicit measurement coverage.

    Repeats of one app are averaged before averaging apps. Every observed tool has a row for
    every group; a failed, skipped or absent measurement stays unknown, while a completed
    clean scan contributes zero. These tool-specific counts are not cross-tool deduplicated.
    A tool missing from the catalog has an unknown analysis mode, never an assumed static mode.
    """
    groups = _group(rows, "group")
    row_by_id = {r["run_id"]: r for r in rows}
    result_ids = ToolResult.latest_ids_per_job(row_by_id)
    results = list(
        ToolResult.objects.filter(id__in=result_ids)
        .values("run__generation_job_id", "tool_slug", "category", "status", "summary")
        .annotate(finding_count=Count("findings")),
    )
    catalog = {
        t["slug"]: t
        for t in AnalyzerTool.objects.filter(slug__in={r["tool_slug"] for r in results}).values(
            "slug",
            "category",
            "analysis_mode",
            "kind",
        )
    }
    ai = {slug for slug, tool in catalog.items() if tool["kind"] == "ai"}
    ai.update(r["tool_slug"] for r in results if r["category"] == "ai")
    results = [r for r in results if include_ai or r["tool_slug"] not in ai]
    slugs = sorted({r["tool_slug"] for r in results})
    by_tool: dict[str, list] = defaultdict(list)
    by_group_tool: dict[tuple[str, str], list] = defaultdict(list)
    for result in results:
        run_id = str(result["run__generation_job_id"])
        by_tool[result["tool_slug"]].append(result)
        by_group_tool[(row_by_id[run_id]["group"], result["tool_slug"])].append(result)
    metadata = {}
    for slug in slugs:
        tool = catalog.get(slug, {})
        categories = sorted({r["category"] for r in by_tool[slug] if r["category"]})
        fallback = categories[0] if len(categories) == 1 else "mixed" if categories else "unknown"
        category = tool.get("category") or fallback
        metadata[slug] = {
            "category": category,
            "analysis_mode": tool.get("analysis_mode", "unknown"),
            "is_dependency_scanner": slug in VULN_TOOLS or category == "dependencies",
            "is_ai": slug in ai,
        }
    out = []
    for group, items in sorted(groups.items()):
        group_fields = _group_fields(group, items, grouped_by)
        apps_total = len(_units(items))
        for slug in slugs:
            observations = by_group_tool[(group, slug)]
            measured = [r for r in observations if tool_measured(r["status"], r["summary"])]
            per_app: dict[tuple, list[int]] = defaultdict(list)
            for result in measured:
                row = row_by_id[str(result["run__generation_job_id"])]
                per_app[app_unit(row)].append(result["finding_count"])
            app_means = [_mean(counts) for counts in per_app.values()]
            out.append(
                {
                    **group_fields,
                    "group": group,
                    "tool": slug,
                    **metadata[slug],
                    "raw_findings": sum(r["finding_count"] for r in measured) if measured else None,
                    "raw_findings_per_app": _mean(app_means),
                    "apps_measured": len(per_app),
                    "apps_total": apps_total,
                    "runs_measured": len(measured),
                    "runs_total": len(items),
                    "runs_with_result": len(observations),
                    "complete_coverage": len(measured) == len(items),
                    "counted_in_comparison": slug in common_tools,
                },
            )
    return out


# ── final assessment: ranking criteria and comparable cost ───────────────


def ranking_criteria(config: dict[str, Any], plan: dict[str, Any], *, cross_study: bool = False):
    """The ranking's criteria and where they come from: ``(criteria, preset)``.

    The report's ``ranking_criteria`` (preset ``report``), else the plan's (``analysis_plan``), else the
    preset: ``final_assessment`` (whole-cohort rates, cost at list prices) for a cross-study sample,
    ``default`` (``mcdm.DEFAULT_CRITERIA``) everywhere else.
    """
    if config.get("ranking_criteria"):
        return criteria_from_config(config["ranking_criteria"]), "report"
    if plan.get("ranking_criteria"):
        return criteria_from_config(plan["ranking_criteria"]), "analysis_plan"
    preset = "final_assessment" if cross_study else "default"
    return CRITERIA_PRESETS[preset], preset


def _run_models(runs) -> dict[str, Any]:
    """``{model_id: LLMModel}`` of *runs* (one query)."""
    from backend.llm_models.models import LLMModel

    rows = LLMModel.objects.filter(pk__in={r.model_id for r in runs if r.model_id})
    return {m.model_id: m for m in rows}


def _alternative_costs(runs, rows, models, prices) -> dict[str, tuple[Any, float | None]]:
    """Per model whose curated name-map twin is not its catalog twin: those prices and its list cost per app."""
    alternatives = map_twin_prices(models.values(), prices)
    if not alternatives:
        return {}
    metrics = {str(r.pk): r.metrics if isinstance(r.metrics, dict) else {} for r in runs}
    out = {}
    for model_id, price in alternatives.items():
        model_rows = [
            {**row, "list_cost": run_list_cost(metrics.get(row["run_id"]), price)}
            for row in rows
            if row["model"] == model_id
        ]
        out[model_id] = (price, _mean(_avg(r["list_cost"] for r in v) for v in _units(model_rows).values()))
    return out


# ── generator ────────────────────────────────────────────────────────────


def _study(config, user) -> Study | None:
    if not config.get("study_id"):
        return None
    from backend.reports.services.generators import owned_study

    return owned_study(config["study_id"], user)


def scope_runs(config: dict[str, Any], user, study: Study | None):
    """The runs a study report describes (``statistics.services.runs.study_runs``), reference runs left out.

    A study's current runs (main stage), the given ``run_ids``, or every sample run *user* may report on
    (staff and ``user=None``: everyone's). Reference runs are instruments, never samples.
    """
    run_ids = [str(r) for r in config["run_ids"]] if config.get("run_ids") else None
    qs = study_runs(user, study_id=str(study.pk) if study is not None else None, run_ids=run_ids)
    return qs.exclude(kind=choices.RunKind.REFERENCE).select_related("starter")


def generate_study_report(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """The study report of *config*. ``config["code_scope"]`` (``reports.services.loc.CODE_SCOPES``;
    absent: ``settings.STUDY_CODE_SCOPE``) says what counts as the delivered app in every table of it."""
    from backend.reports.services.loc import CODE_SCOPES
    from backend.reports.services.loc import use_code_scope

    scope = config.get("code_scope")
    if scope is not None and scope not in CODE_SCOPES:
        msg = f"code_scope must be one of {', '.join(CODE_SCOPES)}"
        raise ValueError(msg)
    with use_code_scope(scope) as in_force:
        return _generate_study_report({**config, "code_scope": in_force}, user=user)


def _generate_study_report(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    from backend.reports.services.evaluation_selection import selected_suite
    from backend.reports.services.evaluation_selection import selection_summary
    from backend.reports.services.requirement_alignment import alignment_section
    from backend.reports.services.supplemental_evidence import bind_evidence

    eval_suite = selected_suite(config)
    if eval_suite is not None:
        config = {**config, "eval_suite": eval_suite.pinned_ref}
    selection = None
    if config.get("selection") == LATEST_UNIQUE:
        # Cross-study: the newest usable run per (model, task) of several studies (no one study's design).
        selection = select_latest_unique(config, user)
    study = _study(config, user) if selection is None else None
    seed = int(config.get("seed", 0))
    include_ai = bool(config.get("include_ai", False))
    line_tolerance = int(config.get("line_tolerance", 2))
    runs = list(scope_runs(config, user, study)) if selection is None else list(selection.runs)
    # Runs still queued, running or being evaluated have no outcome yet; counting them would
    # read as "not generated" (a failure) in the funnel and rates.
    in_flight = [r for r in runs if r.status in _IN_FLIGHT]
    runs = [r for r in runs if r.status not in _IN_FLIGHT]
    # Attrition: no model-attributable outcome (infra error, exclusion): reported, never a failure.
    attrition = Counter(r.outcome or r.status for r in runs if r.usable is not True)
    runs = [r for r in runs if r.usable is True]
    if selection is not None:
        # The selection's units: a unit without a usable run, or only in flight, never reaches the sample.
        in_flight, attrition = selection.in_flight, selection.attrition
    total_runs = len(runs)
    plan = (study.analysis_plan if study is not None and isinstance(study.analysis_plan, dict) else None) or {}
    exclusions = plan.get("exclusions") or {}
    if plan and "truncated" in exclusions:
        # A pre-registered plan's exclusions govern (the report setting would let a confirmatory
        # analysis change its sample after the fact).
        requested = config.get("exclude_truncated")
        config = {**config, "exclude_truncated": bool(exclusions["truncated"])}
        config["_exclusions_overridden"] = requested is not None and bool(requested) != config["exclude_truncated"]
    if config.get("exclude_truncated", False):
        # Sensitivity analysis: truncated runs measure the budget ceiling as much as the model.
        runs = [r for r in runs if not bool((r.validity or {}).get("truncated"))]

    criteria, preset = ranking_criteria(config, plan, cross_study=selection is not None)
    # The final assessment's columns (whole-cohort outcomes, cost at list prices): a cross-study sample, or a
    # ranking that names one of them. Other reports keep exactly their previous content.
    final_assessment = selection is not None or any(c.key in FINAL_ASSESSMENT_METRICS for c in criteria)
    models = _run_models(runs) if final_assessment else {}
    prices = list_prices(models.values()) if final_assessment else None
    rows, issues = app_table(
        runs,
        include_ai=include_ai,
        line_tolerance=line_tolerance,
        eval_suite=eval_suite,
        prices=prices,
    )
    supplemental = bind_evidence(config.get("supplemental_evidence"), rows)
    # Arms that share a model (harness, prompt or stack conditions) are never pooled.
    grouped_by = label_groups(rows) if selection is None else _selection_groups(rows, selection)
    design = design_state(study) if study is not None else None
    evaluation_selection = selection_summary(rows, eval_suite)
    if design is not None:
        design["evaluation_suite_changed"] = bool(evaluation_selection["changed_from_plan_runs"])
    # The declared metrics and the ranking are confirmatory only for the plan's analysis on the planned sample.
    reasons = exploratory_reasons(plan, design)

    by_model = _group([r for r in rows if r["group"]], "group")
    criteria_by_model = {m: model_criteria(v) for m, v in by_model.items()}
    ranking = rank_models(
        {m: {c.key: criteria_by_model[m][c.key] for c in criteria} for m in criteria_by_model},
        criteria,
        seed=seed,
        resample=_resampler(by_model) if config.get("bootstrap", True) else None,
        draws=ranking_draws(config),
        data_draws=ranking_draws(config),
    )
    declared = declared_metrics(plan)
    ranking["status"] = (
        "confirmatory"
        if plan.get("ranking_criteria") and not config.get("ranking_criteria") and not reasons
        else "exploratory"
    )

    # Model parameters vary by model only: one point per model, never one per arm.
    correlations = correlations_section(
        _model_parameters(runs),
        model_level_criteria(criteria_by_model, by_model),
        seed,
        arms_averaged=grouped_by == "condition",
    )
    security = security_bounds_section(rows, grouped_by)
    from backend.reports.services.study_provenance import study_provenance
    from backend.statistics.services.accounting import run_accounting
    from backend.statistics.services.accounting import tool_inventory

    accounting = run_accounting(GenerationRun.objects.filter(id__in=[r.id for r in runs]))
    provenance = study_provenance(runs, rows, study=study, eval_suite=eval_suite)
    inventory = tool_inventory()
    protocol = protocol_section(runs, rows, study, config, grouped_by=grouped_by, design=design)
    protocol["evaluation_selection"] = evaluation_selection
    protocol["execution"] = provenance["execution"]
    if supplemental.get("lock_materialization"):
        protocol["execution"]["notes"] = [
            note
            for note in protocol["execution"]["notes"]
            if not note.startswith("No paired original-versus-materialized")
        ]
        protocol["execution"]["notes"].append(
            "A separately collected matched lock experiment is included in supplemental_evidence. "
            "Its outcomes do not replace primary evaluation grades.",
        )
    protocol["requirements"] = provenance["requirements"]
    protocol["tool_catalog"] = inventory["analyzers"]
    protocol["platform_evaluators"] = inventory["evaluators"]
    protocol["truncated_excluded"] = total_runs - len(runs)
    protocol["in_flight_excluded"] = len(in_flight)
    protocol["attrition_excluded"] = dict(attrition)
    protocol["beyond_plan_excluded"] = (design or {}).get("beyond_plan_excluded", 0)
    protocol["pilot_excluded"] = (design or {}).get("pilot_excluded", 0)
    if selection is not None:
        counts = selection.summary["counts"]
        protocol["beyond_plan_excluded"] = counts["beyond_plan_excluded"]
        protocol["pilot_excluded"] = counts["pilot_excluded"]
        # The selection left truncated runs out itself (before complete cases), so its sample is the report's.
        protocol["truncated_excluded"] = counts["truncated_excluded"]
        protocol["scope"].update(scope_fields(selection.summary))
    protocol["settings"]["exclusions_source"] = "analysis_plan" if plan and "truncated" in exclusions else "report"
    if final_assessment:
        # Which criteria ranked (the final-assessment preset is the cross-study default) and how cost is priced.
        protocol["ranking_preset"] = preset
        in_sample = {r["model"] for r in rows}
        protocol["cost_basis"] = cost_basis(
            {m: p for m, p in prices.items() if m in in_sample},
            alternatives=_alternative_costs(runs, rows, models, prices),
            earlier=earlier_attempts(rows, prices),
        )
    protocol["confirmatory"] = {"design": not reasons, "reasons": reasons}
    planned = planned_comparisons_section(rows, study, plan, design, seed=seed)
    from backend.reports.services.exploratory_comparisons import exploratory_comparisons

    exploratory = (
        exploratory_comparisons(
            rows,
            study,
            seed=seed,
            cross_study=selection is not None,
            final_assessment=final_assessment,
        )
        if planned is None
        else None
    )
    # Which tools' findings the issue counts include (measured on every app) and which were left out.
    protocol["instrument_coverage"] = issues.get("coverage")

    issues_out = {k: v for k, v in issues.items() if k not in ("issues", "per_app")}
    issues_out["by_group_tool"] = group_tool_measurements(
        rows,
        grouped_by,
        common_tools=(issues.get("coverage") or {}).get("tools_common", []),
        include_ai=include_ai,
    )
    issues_out["by_group_tool_note"] = (
        "Raw findings per measured app, repeats averaged first; not cross-tool deduplicated. "
        "Partial coverage describes the measured subset only. Null means unmeasured, not clean. "
        "These tool counts cover the files each scanner read; dependency and dynamic tools are marked separately. "
        "Tools absent from the catalog have an unknown analysis mode."
    )
    if grouped_by == "condition" or selection is not None:
        # The groups every other table uses: arms apart, colliding labels disambiguated,
        # and runs outside a study kept per model (by_condition pools them in one row).
        issues_out["by_group"] = _issues_by_group(issues["per_app"], rows, grouped_by)
    if selection is not None:
        # Selection groups pool conditions across studies; a per-condition split would contradict them.
        issues_out.pop("by_condition", None)
    report = {
        "protocol": protocol,
        "supplemental_evidence": supplemental,
        "requirement_alignment": alignment_section(rows),
        "definitions": {**DEFINITIONS, **FINAL_ASSESSMENT_DEFINITIONS} if final_assessment else DEFINITIONS,
        "metric_definitions": {**PLAN_METRICS, **FINAL_ASSESSMENT_METRICS} if final_assessment else PLAN_METRICS,
        "funnel": funnel_section(rows, seed, grouped_by),
        "model_outcomes": [
            {
                **_group_fields(m, by_model[m], grouped_by),
                "apps": len(_units(by_model[m])),
                "runs": len(by_model[m]),
                **c,
                "status": {k: "confirmatory" if k in declared and not reasons else "exploratory" for k in c},
            }
            for m, c in criteria_by_model.items()
        ],
        "issues": issues_out,
        "security_bounds": security,
        "runtime_security": provenance["runtime_security"],
        "code_metrics": code_metrics_section(rows, seed, grouped_by),
        "correlations": correlations,
        "ranking": ranking,
        "planned_comparisons": planned,
        "exploratory_comparisons": exploratory,
        "tool_inventory": inventory["counts"],
        "accounting": {k: accounting[k] for k in ("apps", "analysis_runs", "executions", "evaluations", "tools")},
        "per_app": rows,
        "validity": validity_section(
            rows,
            protocol=protocol,
            issues=issues,
            accounting=accounting,
            correlations=correlations,
            security=security,
            plan=plan,
            config=config,
            planned=planned,
            ranking=ranking,
            sample_selection=selection.summary if selection is not None else None,
            group_criteria=criteria_by_model if final_assessment else None,
        ),
    }
    if selection is not None:
        report["selection"] = selection.summary
    return report
