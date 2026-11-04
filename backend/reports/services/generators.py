"""Report generators: the JSON content of each report type, read from generation runs and their analyses.

The comparison reports (model analysis, task comparison, study conditions) measure every compared row
with one instrument set: the study tables' :func:`~backend.statistics.services.study.issue_breakdowns`
runs once over the whole report, and finding counts come only from the tools that measured every
analyzed run (``coverage``). A run nothing measured shows ``None``, never zero findings. Per-kLOC
densities and the empirical score count code findings only: dependency advisories, dynamic (runtime)
findings and findings in test code are reported apart, as in the study tables and /rankings. Severity
columns show the levels the tools printed (``reported_levels``); the normalized severity (a heuristic)
only appears in keys labelled ``normalized``.

Run content is read only through ``generation.runs.view`` (LOC through ``reports.services.loc``); a
study's sample is its **current runs** (the first usable attempt of each main-stage cell, runs beyond
the plan left out). The default analysis is intention-to-treat: truncated runs are included and
``exclude_truncated`` is a sensitivity setting.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import choices
from backend.llm_models.models import LLMModel

# Findings weighted by how much they matter for cross-model comparisons; raw
# counts let a flood of style nits outweigh one exploitable hole. Shared with
# the rankings scoring so reports and /rankings weigh findings the same way.
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.statistics.services.dedup import VULN_TOOLS
from backend.statistics.services.dedup import reported_level

from .loc import is_test_path
from .loc import loc_for_runs
from .loc import loc_from_view
from .study_report import generate_study_report

if TYPE_CHECKING:
    from django.db.models import QuerySet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RunStatus = choices.RunStatus
_LEVELS = ("critical", "high", "medium", "low", "info")
_IN_FLIGHT = (RunStatus.PENDING, RunStatus.RUNNING)
# Finding.severity is normalized by a heuristic (tools that print no level get
# one assigned): keys holding it say so, and are statistics, not the tool's label.
NORMALIZED = "normalized (heuristic)"
# The study conditions report's payload version. 3: v3 runs (current runs of a study, intention-to-treat
# by default, evaluation from RunEvaluation, outcomes and usability per row).
REPORT_VERSION = 3
# Columns of each row's deduplicated issue summary (study.group_summary).
_DEDUP_KEYS = (
    "apps",
    "runs",
    "raw_findings",
    "unique_issues",
    "duplicate_findings",
    "mean_unique_per_app",
    "code_unique",
    "code_unique_tests",
    "manifest_unique",
    "runtime_unique",
    "unique_per_kloc",
    "security_unique",
    "security_unique_tests",
    "dependency_unique",
    "dependency_added",
    "dependency_inherited",
    "loc",
    "test_loc",
)
# Reference runs (oracles, baselines, mutants) are instruments, never samples.
_SAMPLE_RUNS = ~Q(kind=choices.RunKind.REFERENCE)


def _sees_all(user) -> bool:
    """No user (management commands, tests) or staff: every row; anyone else: only their own."""
    return user is None or bool(getattr(user, "is_staff", False))


def _owned(qs, user, field: str = "created_by"):
    """*qs* narrowed to what *user* may report on (see :func:`_sees_all`)."""
    if _sees_all(user):
        return qs
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    return qs.filter(**{field: user})


def sample_runs(user=None) -> QuerySet[GenerationRun]:
    """Every sample run *user* may report on (reference runs are instruments, not samples)."""
    return _owned(GenerationRun.objects.filter(_SAMPLE_RUNS), user)


def latest_result_ids(run_ids) -> list:
    """Latest result per (run, tool): ``ToolResult.latest_ids_per_job`` (the analysis FK keeps its v2 name).

    Picking the latest analysis *run* instead would drop every tool a later partial
    re-analysis (one tool, or only the AI reviewer) did not re-run.
    """
    return ToolResult.latest_ids_per_job([str(r) for r in run_ids])


def _severity_counts(findings_qs) -> dict[str, int]:
    """Findings per *normalized* severity (``Finding.severity``): statistics only, shown labelled as normalized."""
    out = dict.fromkeys(_LEVELS, 0)
    for row in findings_qs.values("severity").annotate(n=Count("id")):
        sev = (row["severity"] or "").lower()
        if sev in out:
            out[sev] += row["n"]
    return out


def _reported_levels(findings_qs) -> dict[str, int]:
    """Findings per level exactly as the tool printed it (``Finding.tool_severity``); ``unrated``: it printed none."""
    counter: Counter = Counter()
    for row in findings_qs.values("tool_severity").annotate(n=Count("id")):
        counter[reported_level(row["tool_severity"])] += row["n"]
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def _num(data: Any, key: str) -> float | None:
    value = data.get(key) if isinstance(data, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _success_rate(completed: int, total: int, in_flight: int) -> float | None:
    """Completed / finished runs; queued or running runs have no outcome yet, and nothing finished is ``None``."""
    finished = total - in_flight
    return completed / finished if finished else None


def is_truncated(run: GenerationRun) -> bool:
    """Whether the run was cut off (budget, wall clock, idle, turn cap): ``validity.truncated``."""
    validity = run.validity if isinstance(run.validity, dict) else {}
    return bool(validity.get("truncated"))


def eval_summary(run: GenerationRun) -> dict[str, Any]:
    """The latest evaluation's mirror in ``run.metrics["eval"]`` (``{}`` when the run was never evaluated)."""
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    summary = metrics.get("eval")
    return summary if isinstance(summary, dict) else {}


def _run_summary(runs_qs) -> dict[str, Any]:
    """Run counts by status and outcome, success and usability rates, duration and spend."""
    agg = runs_qs.aggregate(total=Count("id"), avg_duration=Avg("duration_seconds"))
    by_status = {row["status"]: row["n"] for row in runs_qs.values("status").annotate(n=Count("id"))}
    by_outcome = {
        row["outcome"]: row["n"] for row in runs_qs.exclude(outcome="").values("outcome").annotate(n=Count("id"))
    }
    completed = by_status.get(RunStatus.COMPLETED, 0)
    total = agg["total"] or 0
    in_flight = sum(by_status.get(s, 0) for s in _IN_FLIGHT)
    truncated = usable = decided = 0
    costs: list[float] = []
    for validity, metrics, run_usable in runs_qs.values_list("validity", "metrics", "usable"):
        truncated += bool(isinstance(validity, dict) and validity.get("truncated"))
        if run_usable is not None:
            decided += 1
            usable += bool(run_usable)
        cost = _num(metrics, "cost_usd")
        if cost is not None:
            costs.append(cost)
    return {
        "total_runs": total,
        "completed_runs": completed,
        "failed_runs": by_status.get(RunStatus.FAILED, 0),
        "cancelled_runs": by_status.get(RunStatus.CANCELLED, 0),
        # Queued, running or waiting for evaluation: no outcome yet, so outside the rates.
        "in_flight_runs": in_flight,
        "success_rate": _success_rate(completed, total, in_flight),
        # Model-attributable outcomes (tested, build_fail, start_fail, output_invalid) over decided runs.
        "usable_runs": usable,
        "usable_rate": usable / decided if decided else None,
        "by_outcome": by_outcome,
        "avg_duration": float(agg["avg_duration"]) if agg["avg_duration"] is not None else None,
        "by_status": by_status,
        # Runs cut off by a budget, wall clock, idle or turn cap: their findings describe incomplete apps.
        "truncated_runs": truncated,
        # Spend recorded on the runs; None when no run recorded any (unknown, not free).
        "total_cost": round(sum(costs), 6) if costs else None,
    }


def _stat(values: list[float]) -> dict[str, Any]:
    """Mean and sample stdev over runs; ``None`` when nothing was measured, stdev ``None`` below n=2."""
    if not values:
        return {"mean": None, "stdev": None, "n": 0}
    return {
        "mean": round(statistics.mean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else None,
        "n": len(values),
    }


def _trials(runs, *, exclude_truncated: bool) -> tuple[list, list]:
    """The usable completed runs that count as trials, and the truncated ones among them.

    Infra errors and excluded runs are not trials (not model-attributable). Truncated runs are trials
    unless *exclude_truncated* (the sensitivity analysis) is set.
    """
    usable = [r for r in runs if r.status == RunStatus.COMPLETED and r.usable]
    truncated = [r for r in usable if is_truncated(r)]
    trials = [r for r in usable if not (exclude_truncated and is_truncated(r))]
    return trials, truncated


def _instrument(coverage: dict[str, Any]) -> tuple[set[str], set[str]]:
    """The counted tools (they measured every analyzed run) and the runs (run ids) they measured."""
    tools = set(coverage.get("tools_common") or [])
    return tools, (set(coverage.get("apps_measured") or []) if tools else set())


def _finding_kind(tool: str, category: str, file_path: str, dynamic: set[str]) -> str:
    """One finding's kind, decided as ``study.issue_kind`` and the rankings aggregator decide it."""
    if tool in VULN_TOOLS and category == "dependency":
        return "dependency"
    if tool in dynamic:
        return "runtime"
    if is_test_path(file_path):
        return "test"
    return "code"


def unit_of(run: GenerationRun) -> tuple:
    """The app a run measures: its condition and unit (task version or slot target); repeats are one app."""
    unit = run.task_version_id if run.kind == choices.RunKind.APP_AGENTIC else run.slot_target_id
    return (run.condition_id, run.model_id, run.kind, unit or str(run.id))


def _dynamic_slugs() -> set[str]:
    from backend.statistics.services.study import dynamic_slugs

    return dynamic_slugs()


def _tool_coverage(result_ids: list, *, include_ai: bool) -> dict[str, Any]:
    from backend.statistics.services.study import tool_coverage

    return tool_coverage(result_ids, include_ai=include_ai)


def _condition_stats(runs, *, exclude_truncated: bool = False, coverage: dict | None = None) -> dict[str, Any]:
    """Run-level statistics for one row's runs (a model on a task, or a study condition).

    Each usable completed run is one trial (``n`` counts runs; repeats are not averaged here, the spread
    across them is the point). Truncated runs are trials (intention-to-treat) unless *exclude_truncated*.
    Findings count only for *analyzed* trials, runs the counted instrument set measured (*coverage*: the
    report-wide one; computed over these trials when omitted), and only from its tools.
    ``findings_total`` is every counted finding and ``code_findings_total`` every code finding of the app
    tree (no dependency advisories, runtime findings or test code); ``critical_high_normalized``,
    ``weighted_score`` and ``weighted_per_kloc`` count the code findings in the model's own files
    (authored, modified, slot: attribution by origin) by normalized severity, over their non-test source
    LOC. AI-reviewer findings (opinions with their own run-to-run variance) are tracked separately, for the
    trials the reviewer measured.
    """
    from backend.generation.runs.view import run_views
    from backend.statistics.services.runs import normalize_path

    trials, truncated = _trials(runs, exclude_truncated=exclude_truncated)
    trial_ids = [str(r.id) for r in trials]

    # Trials should share one configuration; if they don't, the stats below blend runs of different
    # prompts or treatments and the reader needs to know.
    prompt_hashes = sorted({r.prompt_hash for r in trials if r.prompt_hash})
    fingerprints = sorted({r.config_fingerprint for r in trials if r.config_fingerprint})

    result_ids = latest_result_ids(trial_ids)
    if coverage is None:
        coverage = _tool_coverage(result_ids, include_ai=False)
    tools, measured = _instrument(coverage)
    analyzed = [r for r in trials if str(r.id) in measured]

    ai_slugs = AnalyzerTool.ai_slugs()
    result_to_run: dict[str, str] = {}
    ai_measured: set[str] = set()
    for rid, run_id, slug, status in ToolResult.objects.filter(id__in=result_ids).values_list(
        "id",
        "run__generation_job_id",
        "tool_slug",
        "status",
    ):
        result_to_run[str(rid)] = str(run_id)
        if slug in ai_slugs and status == ToolResult.Status.COMPLETED:
            ai_measured.add(str(run_id))

    dynamic = _dynamic_slugs()
    raw_by_run: Counter = Counter()
    dep_by_run: Counter = Counter()
    code_by_run: dict[str, Counter] = defaultdict(Counter)
    model_code_by_run: dict[str, Counter] = defaultdict(Counter)
    ai_by_run: dict[str, Counter] = defaultdict(Counter)
    views = run_views(trials)
    model_paths = {v.id: frozenset(e.path for e in v.files("model")) for v in views}
    for row in (
        Finding.objects.filter(result_id__in=result_ids)
        .values("result_id", "result__tool_slug", "severity", "category", "file_path")
        .annotate(n=Count("id"))
    ):
        run_id = result_to_run.get(str(row["result_id"]))
        tool, n = row["result__tool_slug"], row["n"]
        sev = (row["severity"] or "").lower()
        if tool in ai_slugs:
            ai_by_run[run_id][sev] += n
            continue
        if tool not in tools or run_id not in measured:
            continue
        raw_by_run[run_id] += n
        kind = _finding_kind(tool, row["category"] or "", row["file_path"] or "", dynamic)
        if kind == "dependency":
            dep_by_run[run_id] += n
        elif kind == "code":
            code_by_run[run_id][sev] += n
            if normalize_path(row["file_path"] or "") in model_paths.get(run_id, frozenset()):
                model_code_by_run[run_id][sev] += n

    loc_by = {v.id: loc_from_view(v)["total_loc"] for v in views}

    totals, code_totals, dep_totals, crit_high, weighted, per_kloc = [], [], [], [], [], []
    ai_totals, ai_crit_high = [], []
    costs, durations, tokens = [], [], []
    pass_rates: list[float] = []
    all_passed = not_measured = 0

    for run in trials:
        metrics = run.metrics if isinstance(run.metrics, dict) else {}
        if _num(metrics, "cost_usd") is not None:
            costs.append(float(metrics["cost_usd"]))
        duration = run.duration_seconds if run.duration_seconds is not None else _num(metrics, "wall_s")
        if duration:
            durations.append(float(duration))
        if metrics.get("total_tokens"):
            tokens.append(float(metrics["total_tokens"]))
        evaluation = eval_summary(run)
        # Not measured (tester or infra error) is excluded, never scored 0.
        if evaluation.get("measured") and evaluation.get("agent_pass_rate") is not None:
            rate = float(evaluation["agent_pass_rate"])
            pass_rates.append(rate)
            all_passed += rate == 1.0
        elif evaluation:
            not_measured += 1

        rid = str(run.id)
        if rid in ai_measured:
            ai_sev = ai_by_run.get(rid, Counter())
            ai_totals.append(float(sum(ai_sev.values())))
            ai_crit_high.append(float(ai_sev["critical"] + ai_sev["high"]))
        if rid not in measured:
            continue
        code = model_code_by_run.get(rid, Counter())
        score = float(sum(SEVERITY_WEIGHTS.get(s, 0) * n for s, n in code.items()))
        totals.append(float(raw_by_run.get(rid, 0)))
        code_totals.append(float(sum(code_by_run.get(rid, Counter()).values())))
        dep_totals.append(float(dep_by_run.get(rid, 0)))
        crit_high.append(float(code["critical"] + code["high"]))
        weighted.append(score)
        if loc_by.get(rid):
            per_kloc.append(round(score / loc_by[rid] * 1000, 4))

    # The empirical-score input, as /rankings builds it: repeats of one app
    # collapse to their mean, then means are summed (findings and LOC alike).
    units: dict[tuple, list[str]] = defaultdict(list)
    for run in analyzed:
        if loc_by.get(str(run.id)):
            units[unit_of(run)].append(str(run.id))
    unit_findings = dict.fromkeys(_LEVELS, 0.0)
    unit_loc = 0.0
    for members in units.values():
        for rid in members:
            for sev, n in model_code_by_run.get(rid, Counter()).items():
                if sev in unit_findings:
                    unit_findings[sev] += n / len(members)
        unit_loc += sum(loc_by[r] for r in members) / len(members)

    known_loc = [loc_by[str(r.id)] for r in analyzed if loc_by.get(str(r.id)) is not None]
    return {
        "unit": "run",
        "trials": len(trials),
        "analyzed": len(analyzed),
        # Non-test model-scope LOC of the analyzed trials: the denominator matching their findings.
        "analyzed_loc": sum(known_loc) if known_loc else None,
        "truncated": len(truncated),
        "truncated_excluded": len(truncated) if exclude_truncated else 0,
        "prompt_hashes": prompt_hashes,
        "mixed_prompt_versions": len(prompt_hashes) > 1,
        "config_fingerprints": len(fingerprints),
        "severity_basis": NORMALIZED,
        "findings_total": _stat(totals),
        "code_findings_total": _stat(code_totals),
        "dependency_findings_total": _stat(dep_totals),
        "critical_high_normalized": _stat(crit_high),
        "weighted_score": _stat(weighted),
        "weighted_per_kloc": _stat(per_kloc),
        "ai_analyzed": len([r for r in trials if str(r.id) in ai_measured]),
        "ai_findings_total": _stat(ai_totals),
        "ai_critical_high_normalized": _stat(ai_crit_high),
        "cost": {**_stat(costs), "total": round(sum(costs), 6) if costs else None},
        "duration": _stat(durations),
        "tokens": _stat(tokens),
        "functional": {
            "runs_measured": len(pass_rates),
            "runs_not_measured": not_measured,
            "runs_all_passed": all_passed,
            "pass_rate": _stat(pass_rates),
        },
        "density_basis": {
            "apps": len(units),
            "code_findings_normalized_heuristic": {k: round(v, 4) for k, v in unit_findings.items()}
            if units
            else None,
            "loc": round(unit_loc, 4) if units else None,
        },
    }


def _breakdowns(runs_qs) -> dict[str, Any]:
    """study.issue_breakdowns over a whole report scope: one instrument set and one dedup for every row."""
    from backend.statistics.services.study import issue_breakdowns

    return issue_breakdowns(runs_qs)


def _per_app_run_id(app: dict[str, Any]) -> str:
    """The run a per-app row of ``issue_breakdowns`` describes."""
    return str(app["run_id"])


def _dedup_columns(per_app_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-tool deduplicated issue totals of one row's runs (``study.group_summary``: apps are the unit)."""
    from backend.statistics.services.study import group_summary

    summary = group_summary(per_app_rows)
    return {k: summary.get(k) for k in _DEDUP_KEYS}


def _coverage_block(coverage: dict[str, Any]) -> dict[str, Any]:
    """Which tools every compared row was measured with, and which were left out (and why)."""
    return {
        "tools_common": coverage.get("tools_common") or [],
        "tools_partial": coverage.get("tools_partial") or {},
        "runs_measured": coverage.get("apps", 0),
        "runs_unmeasured": coverage.get("apps_unmeasured") or [],
    }


def _coverage_note(coverage: dict[str, Any]) -> str | None:
    """The table note naming what the finding counts leave out: tools that missed a run, and runs no tool measured."""
    parts = []
    partial = coverage.get("tools_partial") or {}
    common = coverage.get("tools_common") or []
    if partial:
        total = coverage.get("apps", 0)
        tools = ", ".join(f"{t} ({p.get('apps_measured', 0)} of {total} runs)" for t, p in partial.items())
        if common:
            parts.append(
                f"Finding counts use only the {len(common)} tool(s) that measured every analyzed run; "
                f"left out of every row: {tools}.",
            )
        else:
            parts.append(f"No tool measured every analyzed run, so no findings are counted: {tools}.")
    unmeasured = coverage.get("apps_unmeasured") or []
    if unmeasured:
        parts.append(
            f"{len(unmeasured)} analyzed run(s) were measured by no tool (every tool failed or was skipped, "
            f"or only the AI reviewer ran) and are left out of the counts.",
        )
    return " ".join(parts) or None


def _finding_columns(run_ids: list[str], coverage: dict[str, Any]) -> dict[str, Any]:
    """Raw finding counts of a row's analyzed runs: the tools' own levels, the normalized split (labelled), AI apart.

    Only runs the counted instrument set measured, and only its tools; a
    row with no measured run has unknown counts (``None``), not zero.
    """
    tools, measured = _instrument(coverage)
    analyzed = [r for r in run_ids if r in measured]
    result_ids = latest_result_ids(run_ids)
    base = Finding.objects.filter(result_id__in=result_ids)
    static = base.filter(result__tool_slug__in=tools, result__run__generation_job_id__in=analyzed)
    ai_slugs = AnalyzerTool.ai_slugs()
    ai_runs = {
        str(r)
        for r in ToolResult.objects.filter(
            id__in=result_ids,
            tool_slug__in=ai_slugs,
            status=ToolResult.Status.COMPLETED,
        ).values_list("run__generation_job_id", flat=True)
    }
    ai = base.filter(result__tool_slug__in=ai_slugs, result__run__generation_job_id__in=ai_runs)
    return {
        "runs_analyzed": len(analyzed),
        "total_findings": static.count() if analyzed else None,
        "reported_levels": _reported_levels(static) if analyzed else None,
        "severity_normalized_heuristic": _severity_counts(static) if analyzed else None,
        "ai_runs_analyzed": len(ai_runs),
        "ai_total": ai.count() if ai_runs else None,
        "ai_reported_levels": _reported_levels(ai) if ai_runs else None,
        "ai_severity_normalized_heuristic": _severity_counts(ai) if ai_runs else None,
    }


def _tools_for_runs(runs_qs) -> list[dict[str, Any]]:
    """Per-tool breakdown derived from each run's latest result per tool."""
    rows = (
        ToolResult.objects.filter(id__in=latest_result_ids(runs_qs.values_list("id", flat=True)))
        .values("tool_slug")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return [{"analyzer": r["tool_slug"], "tasks": r["n"]} for r in rows]


def _aggregate_tool_metrics(results_qs) -> dict[str, Any]:
    """Aggregate ``ToolResult.metrics`` per tool without per-tool special-casing.

    Numeric top-level keys are averaged (with min/max); dict-of-numbers keys
    (e.g. radon's ``rank_distribution``) are summed key-wise; anything else
    (lists such as ``top_functions``) is skipped.
    """
    numeric: dict[str, dict[str, list[float]]] = {}
    distributions: dict[str, dict[str, dict[str, float]]] = {}
    result_counts: dict[str, int] = {}

    for tool_slug, metrics in results_qs.exclude(metrics={}).values_list("tool_slug", "metrics"):
        if not isinstance(metrics, dict):
            continue
        result_counts[tool_slug] = result_counts.get(tool_slug, 0) + 1
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric.setdefault(tool_slug, {}).setdefault(key, []).append(value)
            elif isinstance(value, dict) and all(isinstance(v, (int, float)) for v in value.values()):
                summed = distributions.setdefault(tool_slug, {}).setdefault(key, {})
                for bucket, count in value.items():
                    summed[bucket] = summed.get(bucket, 0) + count

    out: dict[str, Any] = {}
    for tool_slug, count in result_counts.items():
        out[tool_slug] = {
            "results_with_metrics": count,
            "numeric": {
                key: {
                    "avg": round(sum(values) / len(values), 2),
                    "min": min(values),
                    "max": max(values),
                }
                for key, values in numeric.get(tool_slug, {}).items()
            },
            "distributions": distributions.get(tool_slug, {}),
        }
    return out


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def generate_model_analysis(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Aggregate across one model's runs and their analyses (*user*'s runs; staff or None: all)."""
    model_id = config.get("model_id") or config.get("model_slug")
    if not model_id:
        msg = "model_id required for model_analysis report"
        raise ValueError(msg)

    try:
        model = LLMModel.objects.get(model_id=model_id)
    except LLMModel.DoesNotExist as e:
        msg = f"Model not found: {model_id}"
        raise ValueError(msg) from e

    runs = sample_runs(user).filter(model=model).select_related("model")
    breakdown = _breakdowns(runs)
    coverage = breakdown["coverage"]
    issues = _dedup_columns(breakdown["per_app"])
    tool_results = ToolResult.objects.filter(id__in=latest_result_ids(runs.values_list("id", flat=True)))

    return {
        "model": {
            "model_id": model.model_id,
            "model_name": model.model_name,
            "provider": model.provider,
        },
        "generation": _run_summary(runs),
        "loc": loc_for_runs(runs),
        # Non-test model-scope LOC of the analyzed runs, the denominator matching the findings below.
        "analyzed_loc": issues["loc"],
        **_finding_columns([str(r) for r in runs.values_list("id", flat=True)], coverage),
        "issues_dedup": issues,
        "coverage": _coverage_block(coverage),
        "tools": _tools_for_runs(runs),
        "metrics_by_tool": _aggregate_tool_metrics(tool_results),
    }


def generate_task_comparison(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Compare models on one task (every version, or ``task_version``) (*user*'s runs; staff or None: all).

    Rows are grouped by ``(model, treatment_hash)`` rather than model alone: runs under different
    treatments (stack, starter, style, prompts, harness) on the same task are not comparable, so pooling
    them under one "model" row would blend results across treatments. Pass ``treatment_hash`` or
    ``prompt_hash`` in *config* to restrict the report to one treatment. Every row is measured with the
    same instrument set (``coverage``).
    """
    from backend.generation.models import TaskSpec

    task_slug = config.get("task_slug")
    if not task_slug:
        msg = "task_slug required for task comparison report"
        raise ValueError(msg)

    try:
        task = TaskSpec.objects.get(slug=task_slug)
    except TaskSpec.DoesNotExist as e:
        msg = f"Task not found: {task_slug}"
        raise ValueError(msg) from e

    runs = sample_runs(user).filter(task_version__task=task, kind=choices.RunKind.APP_AGENTIC)
    if config.get("task_version"):
        runs = runs.filter(task_version__version=int(config["task_version"]))
    if config.get("filter_models"):
        runs = runs.filter(model__model_id__in=config["filter_models"])
    if config.get("treatment_hash"):
        runs = runs.filter(treatment_hash=config["treatment_hash"])
    if config.get("prompt_hash"):
        runs = runs.filter(prompt_hash=config["prompt_hash"])

    by_model_treatment: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs.select_related("model"):
        model = run.model
        model_id = model.model_id if model else "unknown"
        key = (model_id, run.treatment_hash or "")
        bucket = by_model_treatment.setdefault(
            key,
            {
                "model_id": model_id,
                "model_name": getattr(model, "model_name", model_id),
                "provider": getattr(model, "provider", ""),
                "treatment_hash": run.treatment_hash or "",
                "runs": [],
            },
        )
        bucket["runs"].append(run)

    exclude_truncated = bool(config.get("exclude_truncated", False))
    # One instrument set and one dedup over every row's trials at once: per-row
    # coverage would let a tool that failed on one row's app vanish from that row only.
    row_of_run: dict[str, tuple[str, str]] = {}
    trial_ids: list = []
    for key, bucket in by_model_treatment.items():
        for run in _trials(bucket["runs"], exclude_truncated=exclude_truncated)[0]:
            row_of_run[str(run.id)] = key
            trial_ids.append(run.id)
    breakdown = _breakdowns(GenerationRun.objects.filter(id__in=trial_ids))
    coverage = breakdown["coverage"]
    per_row: dict[tuple[str, str], list] = defaultdict(list)
    for app in breakdown["per_app"]:
        per_row[row_of_run[_per_app_run_id(app)]].append(app)

    rows = []
    for key, bucket in by_model_treatment.items():
        sub_runs = GenerationRun.objects.filter(id__in=[r.id for r in bucket["runs"]])
        row_trials = [run_id for run_id, row in row_of_run.items() if row == key]
        rows.append(
            {
                "model_id": bucket["model_id"],
                "model_name": bucket["model_name"],
                "provider": bucket["provider"],
                "treatment_hash": bucket["treatment_hash"],
                "generation": _run_summary(sub_runs),
                "loc": loc_for_runs(bucket["runs"]),
                **_finding_columns(row_trials, coverage),
                "issues_dedup": _dedup_columns(per_row.get(key, [])),
                "stats": _condition_stats(bucket["runs"], exclude_truncated=exclude_truncated, coverage=coverage),
            },
        )

    rows.sort(
        key=lambda r: (r["generation"]["success_rate"] is not None, r["generation"]["success_rate"] or 0.0),
        reverse=True,
    )

    treatments_seen = sorted({r["treatment_hash"] for r in rows if r["treatment_hash"]})

    # Rank-sensitivity assumes one row per model; with mixed treatments take each model's first (highest
    # success-rate) *analyzed* row rather than feeding duplicate model_ids into the rank-swap comparison.
    # A row nothing measured has no score (not a perfect one), so it never stands in for the model's
    # analyzed row.
    seen_models: set[str] = set()
    sensitivity_rows = []
    for row in rows:
        if not row["stats"]["analyzed"] or row["model_id"] in seen_models:
            continue
        seen_models.add(row["model_id"])
        sensitivity_rows.append(row)

    latest = task.versions.order_by("-version").first()
    content = (latest.content if latest is not None else None) or {}
    return {
        "task": {
            "slug": task.slug,
            "name": latest.name if latest is not None else task.slug,
            "versions": sorted({v for v in runs.values_list("task_version__version", flat=True) if v}),
            "title": content.get("title"),
            "category": content.get("category"),
        },
        "models": rows,
        "total_models": len(rows),
        # More than one treatment here means results came from different stacks, prompts or harnesses:
        # filter by treatment_hash for an apples-to-apples comparison instead of reading across rows.
        "treatment_hashes": treatments_seen,
        "mixed_treatments": len(treatments_seen) > 1,
        "exclude_truncated": exclude_truncated,
        "coverage": _coverage_block(coverage),
        "coverage_note": _coverage_note(coverage),
        "sensitivity": _task_sensitivity(sensitivity_rows),
    }


def _task_sensitivity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank-stability of this task's models under alternative weights.

    The score's inputs describe one run set, each row's analyzed trials: their code findings (no
    dependency advisories, runtime findings or test code) over their non-test LOC, repeats of one app
    averaged first (the basis /rankings uses).
    """
    from backend.rankings.services import compute_empirical_quality
    from backend.rankings.services import compute_weight_sensitivity

    entries = []
    for row in rows:
        stats = row["stats"]
        basis = stats.get("density_basis") or {}
        entry = {
            "model_id": row["model_id"],
            "empirical_findings": basis.get("code_findings_normalized_heuristic") or {},
            "empirical_loc": basis.get("loc") or 0,
            "apps_completed": stats["analyzed"],
            "functional_pass_rate": None,
        }
        entry["empirical_quality_score"] = compute_empirical_quality(entry)
        entries.append(entry)
    return compute_weight_sensitivity(entries)


def generate_tool_analysis(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Effectiveness analysis of one (or all) analyzer(s) (*user*'s runs; staff or None: all).

    Deliberately spans every execution (not latest-per-run): this report
    measures tool behavior across executions, not the state of any one
    codebase. Each tool's row counts its executions by outcome, so a tool
    that ran and found nothing reads as a measured zero (completed) instead
    of being missing, and ``findings_per_measured_execution`` gives the
    findings a denominator. A tool with nothing applicable to read (skipped
    as not applicable, or a completed result recorded as not measured)
    measured nothing: it is counted under ``skipped``, never as a zero.
    Cancelled runs are left out. ``days`` narrows to runs created in that window.
    """
    from backend.statistics.services.study import tool_measured

    tool_name = config.get("tool_name")
    days = int(config["days"]) if config.get("days") else None
    runs = _owned(AnalysisRun.objects.all(), user)
    if config.get("filter_model"):
        runs = runs.filter(generation_job__model__model_id=config["filter_model"])
    if days:
        runs = runs.filter(created_at__gte=timezone.now() - timezone.timedelta(days=days))
    cancelled = runs.filter(status=AnalysisRun.Status.CANCELLED).count()
    runs = runs.exclude(status=AnalysisRun.Status.CANCELLED)

    results = ToolResult.objects.filter(run__in=runs)
    if tool_name:
        results = results.filter(tool_slug=tool_name)

    by_tool: dict[str, dict[str, Any]] = {}
    for slug, status, summary in results.values_list("tool_slug", "status", "summary"):
        row = by_tool.setdefault(
            slug,
            {
                "analyzer": slug,
                "executions": 0,
                "completed": 0,
                "failed": 0,
                "skipped": {},
                "unfinished": 0,
                "measured": 0,
                "total": 0,
                "findings_per_measured_execution": None,
                "reported_levels": {},
                "by_severity_normalized_heuristic": dict.fromkeys(_LEVELS, 0),
            },
        )
        row["executions"] += 1
        skip_reason = (summary or {}).get("skip_reason") if isinstance(summary, dict) else None
        if status == ToolResult.Status.COMPLETED:
            row["completed"] += 1
        elif status == ToolResult.Status.FAILED:
            row["failed"] += 1
        elif status == ToolResult.Status.SKIPPED:
            reason = skip_reason or "unspecified"
            row["skipped"][reason] = row["skipped"].get(reason, 0) + 1
        else:
            row["unfinished"] += 1
        # Not applicable (none of the files the tool reads) or not measured: no measurement, never a zero.
        row["measured"] += int(tool_measured(status, summary))

    findings_qs = Finding.objects.filter(result__in=results)
    levels: dict[str, Counter] = defaultdict(Counter)
    for row in findings_qs.values("result__tool_slug", "severity", "tool_severity").annotate(n=Count("id")):
        bucket = by_tool.get(row["result__tool_slug"])
        if bucket is None:
            continue
        bucket["total"] += row["n"]
        levels[row["result__tool_slug"]][reported_level(row["tool_severity"])] += row["n"]
        sev = (row["severity"] or "").lower()
        if sev in bucket["by_severity_normalized_heuristic"]:
            bucket["by_severity_normalized_heuristic"][sev] += row["n"]
    for slug, bucket in by_tool.items():
        bucket["reported_levels"] = dict(sorted(levels[slug].items(), key=lambda kv: (-kv[1], kv[0])))
        if bucket["measured"]:
            bucket["findings_per_measured_execution"] = round(bucket["total"] / bucket["measured"], 2)

    return {
        "tool_filter": tool_name,
        "window_days": days,
        "tools": sorted(by_tool.values(), key=lambda r: (-r["total"], r["analyzer"])),
        "metrics_by_tool": _aggregate_tool_metrics(results),
        # Nothing executed is unmeasured, not zero findings.
        "total_findings": sum(t["total"] for t in by_tool.values()) if by_tool else None,
        "total_executions": sum(t["executions"] for t in by_tool.values()),
        # Runs holding the tool (all runs without a tool filter).
        "total_runs": results.values("run_id").distinct().count() if tool_name else runs.count(),
        "cancelled_runs_excluded": cancelled,
    }


def generate_generation_analytics(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Run success/failure patterns over a configurable window (*user*'s runs; staff or None: all)."""
    days = int(config.get("days", 7))
    since = timezone.now() - timezone.timedelta(days=days)
    runs = sample_runs(user).filter(created_at__gte=since)

    by_model = []
    for row in (
        runs.values("model__model_id", "model__model_name")
        .annotate(
            n=Count("id"),
            completed=Count("id", filter=Q(status=RunStatus.COMPLETED)),
            usable=Count("id", filter=Q(usable=True)),
            in_flight=Count("id", filter=Q(status__in=_IN_FLIGHT)),
            cancelled=Count("id", filter=Q(status=RunStatus.CANCELLED)),
            avg_duration=Avg("duration_seconds"),
        )
        .order_by("-n", "model__model_id")
    ):
        by_model.append(
            {
                "model_id": row["model__model_id"],
                "model_name": row["model__model_name"],
                "runs": row["n"],
                "completed": row["completed"],
                "usable": row["usable"],
                "in_flight": row["in_flight"],
                "cancelled": row["cancelled"],
                "success_rate": _success_rate(row["completed"], row["n"], row["in_flight"]),
                "avg_duration": float(row["avg_duration"]) if row["avg_duration"] is not None else None,
            },
        )

    by_kind = {row["kind"]: row["n"] for row in runs.values("kind").annotate(n=Count("id"))}
    return {
        "window_days": days,
        "since": since.isoformat(),
        "summary": _run_summary(runs),
        "by_kind": by_kind,
        "loc": loc_for_runs(runs),
        "by_model": by_model,
    }


def owned_study(study_id: Any, user) -> Study:
    """The study *study_id* if *user* may report on it (owner; staff: any); else ``ValueError``.

    An unknown id, a malformed id and another user's study all read "Study not found", so a report
    request cannot probe for ids.
    """
    from django.core.exceptions import ValidationError

    if not study_id:
        msg = "study_id required for this report"
        raise ValueError(msg)
    qs = Study.objects.all()
    if user is None or not getattr(user, "is_authenticated", False):
        qs = qs.none()
    elif not getattr(user, "is_staff", False):
        qs = qs.filter(created_by=user)
    try:
        return qs.get(id=study_id)
    except (Study.DoesNotExist, ValidationError) as e:
        msg = f"Study not found: {study_id}"
        raise ValueError(msg) from e


def study_conditions(study: Study) -> list:
    """The study's conditions (the launched plan's when there is one), in plan order."""
    qs = study.conditions.select_related("model", "harness_preset")
    if study.active_plan_id:
        qs = qs.filter(plan_id=study.active_plan_id)
    return list(qs.order_by("index", "id"))


def plan_excludes_truncated(study: Study) -> bool | None:
    """The locked analysis plan's truncation rule (``exclusions.truncated``); ``None`` when no locked plan says."""
    plan = study.analysis_plan if isinstance(study.analysis_plan, dict) else {}
    rule = (plan.get("exclusions") or {}).get("truncated") if plan else None
    if study.locked_at is None or rule is None:
        return None
    return bool(rule)


def _study_results(study: Study) -> tuple[dict[str, Any] | None, str | None]:
    """The Results tab payload (``study.results.study_results``), or why it is not available yet."""
    from backend.generation.study.results import study_results

    try:
        return study_results(study), None
    except NotImplementedError:
        return None, "The study results (leaderboard and paired comparisons) are not available in this build."


def generate_study_conditions_report(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Per-condition code findings and run statistics for one study, plus its Results tab payload.

    Each condition gets the trial-level code statistics (findings, LOC, cost) over the sample the Results
    tab compares: each main-stage cell's current run (the first usable attempt), runs beyond the plan
    left out, intention-to-treat unless *exclude_truncated* (a sensitivity setting; a locked study's
    analysis plan decides it). All conditions are measured with one instrument set (``coverage``). Only
    *user*'s studies (staff: any) are reported on.
    """
    from backend.generation.runs.view import current_runs
    from backend.statistics.services.runs import condition_label

    study = owned_study(config.get("study_id"), user)
    requested = config.get("exclude_truncated")
    plan_rule = plan_excludes_truncated(study)
    if plan_rule is not None:
        exclude_truncated = plan_rule
    else:
        exclude_truncated = bool(requested) if requested is not None else False

    conditions = study_conditions(study)
    current = current_runs(study, stage="main")
    sample = list(current.select_related("model"))
    trials, truncated = _trials(sample, exclude_truncated=exclude_truncated)
    breakdown = _breakdowns(GenerationRun.objects.filter(id__in=[r.id for r in trials]))
    coverage = breakdown["coverage"]
    condition_of = {str(r.id): r.condition_id for r in trials}
    per_condition: dict[Any, list] = defaultdict(list)
    for app in breakdown["per_app"]:
        per_condition[condition_of.get(_per_app_run_id(app))].append(app)
    runs_by_condition: dict[Any, list] = defaultdict(list)
    for run in sample:
        runs_by_condition[run.condition_id].append(run)

    rows = []
    for condition in conditions:
        runs = runs_by_condition.get(condition.id, [])
        cond_trials = _trials(runs, exclude_truncated=exclude_truncated)[0]
        rows.append(
            {
                "condition_id": condition.id,
                # The study tables' name: the label, else the model id.
                "label": condition_label(condition),
                "is_baseline": condition.is_baseline,
                "model_id": condition.model.model_id,
                "model_name": condition.model.model_name,
                "condition_hash": condition.condition_hash,
                "treatment_hash": condition.treatment_hash,
                "generation": _run_summary(GenerationRun.objects.filter(id__in=[r.id for r in runs])),
                "loc": loc_for_runs(runs),
                **_finding_columns([str(r.id) for r in cond_trials], coverage),
                "issues_dedup": _dedup_columns(per_condition.get(condition.id, [])),
                "stats": _condition_stats(runs, exclude_truncated=exclude_truncated, coverage=coverage),
            },
        )

    results, unavailable = _study_results(study)
    notes = []
    if unavailable:
        notes.append(unavailable)
    if plan_rule is not None and requested is not None and bool(requested) != exclude_truncated:
        notes.append(
            f"The locked analysis plan {'excludes' if exclude_truncated else 'keeps'} truncated runs; "
            f"the report's exclude_truncated={bool(requested)} setting does not apply.",
        )
    coverage_note = _coverage_note(coverage)
    if coverage_note:
        notes.append(coverage_note)
    all_runs = GenerationRun.objects.filter(study=study)
    pilot = all_runs.filter(Q(stage=choices.Stage.PILOT) | Q(cell__stage=choices.Stage.PILOT)).distinct()
    in_plan_main = all_runs.filter(beyond_plan=False, cell__stage=choices.Stage.MAIN)
    baseline = next((c for c in conditions if c.is_baseline), None)
    return {
        "report_version": REPORT_VERSION,
        "study": {
            "id": str(study.id),
            "name": study.name,
            "slug": study.slug,
            "hypothesis": study.hypothesis,
            "kind": study.kind,
            "mode": study.mode,
            "status": study.status,
            "repeats": study.repeats,
            "locked_at": study.locked_at.isoformat() if study.locked_at else None,
            "baseline_condition_id": baseline.id if baseline else None,
        },
        "sample": {
            "planned_repeats": study.repeats,
            "runs": len(sample),
            "in_flight": sum(1 for r in sample if r.status in _IN_FLIGHT),
            "beyond_plan_excluded": all_runs.filter(beyond_plan=True).count(),
            "pilot_excluded": pilot.count(),
            # Other attempts of a cell whose current run is another attempt.
            "superseded_attempts_excluded": in_plan_main.count() - len(sample),
            "exclude_truncated": exclude_truncated,
            "truncation_rule": "analysis_plan" if plan_rule is not None else "report_config",
            "truncated": len(truncated),
            "truncated_excluded": len(truncated) if exclude_truncated else 0,
        },
        "coverage": _coverage_block(coverage),
        "conditions": rows,
        "total_conditions": len(rows),
        "results": results,
        "notes": notes,
    }


def generate_comprehensive(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Platform-wide aggregate combining all report dimensions (*user*'s data; staff or None: all).

    Generation and tool analytics cover the last ``days``; the ranking
    sensitivity and the platform totals are all-time (``periods``).
    """
    from backend.rankings.services import aggregate_rankings
    from backend.rankings.services import compute_weight_sensitivity

    days = int(config.get("days", 30))
    runs = sample_runs(user)
    analyses = _owned(AnalysisRun.objects.all(), user)
    total_analyses = analyses.count()
    return {
        "generated_at": timezone.now().isoformat(),
        "periods": {
            "generation_analytics": f"last {days} days",
            "tool_analysis": f"last {days} days",
            "rankings_sensitivity": "all time",
            "platform": "all time",
        },
        "generation_analytics": generate_generation_analytics({"days": days}, user=user),
        "tool_analysis": generate_tool_analysis({"days": days}, user=user),
        # aggregate_rankings scopes to any user it is given, staff included.
        "rankings_sensitivity": compute_weight_sensitivity(aggregate_rankings(None if _sees_all(user) else user)),
        "platform": {
            # The model catalog, not the models used.
            "catalog_models": LLMModel.objects.count(),
            "models_with_runs": runs.exclude(model__isnull=True).values("model_id").distinct().count(),
            "total_runs": runs.count(),
            "total_analysis_runs": total_analyses,
            # Nothing analyzed is unmeasured, not zero findings (as in tool_analysis).
            "total_findings": Finding.objects.filter(result__run__in=analyses).count() if total_analyses else None,
        },
    }


# Keys are ``Report.Type`` values (``reports/models.py``): the stored values ``template_comparison``
# (``Type.TASK_COMPARISON``) and ``experiment_report`` (``Type.STUDY_CONDITIONS``) are kept as-is.
GENERATORS = {
    "model_analysis": generate_model_analysis,
    "template_comparison": generate_task_comparison,
    "tool_analysis": generate_tool_analysis,
    "generation_analytics": generate_generation_analytics,
    "comprehensive": generate_comprehensive,
    "experiment_report": generate_study_conditions_report,
    "study_report": generate_study_report,
}

# Generators that take ``user=`` and report only on what that user owns
# (staff: everything). Every type does: a report must not hand one user
# another user's runs, costs or findings.
USER_SCOPED = frozenset(GENERATORS)
