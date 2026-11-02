"""Report generators — produce JSON content for each report type.

Adapted from the legacy `app/services/reports/*` modules but driven by the
new Django models (LLMModel, GenerationJob, AnalysisTask, Finding) instead
of filesystem-stored applications.

The comparison reports (model analysis, template comparison, experiment
report) measure every compared row with one instrument set: the study
tables' :func:`~backend.statistics.services.study.issue_breakdowns` runs once
over the whole report, and finding counts come only from the tools that
measured every analyzed run (``coverage``). A run nothing measured shows
``None``, never zero findings. Per-kLOC densities and the empirical score
count code findings only: dependency advisories, dynamic (runtime) findings
and findings in test code are reported apart, as in the study tables and
/rankings. Severity columns show the levels the tools printed
(``reported_levels``); the normalized severity (a heuristic) only appears
in keys labelled ``normalized``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections import defaultdict
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel

# Findings weighted by how much they matter for cross-model comparisons; raw
# counts let a flood of style nits outweigh one exploitable hole. Shared with
# the rankings scoring so reports and /rankings weigh findings the same way.
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.statistics.services.dedup import VULN_TOOLS
from backend.statistics.services.dedup import reported_level
from backend.statistics.services.study import dynamic_slugs
from backend.statistics.services.study import group_summary
from backend.statistics.services.study import issue_breakdowns
from backend.statistics.services.study import study_jobs
from backend.statistics.services.study import tool_coverage

from .loc import is_test_path
from .loc import loc_for_jobs
from .loc import loc_from_job
from .study_report import generate_study_report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEVELS = ("critical", "high", "medium", "low", "info")
_IN_FLIGHT = (GenerationJob.Status.PENDING, GenerationJob.Status.RUNNING)
# Finding.severity is normalized by a heuristic (tools that print no level get
# one assigned): keys holding it say so, and are statistics, not the tool's label.
NORMALIZED = "normalized (heuristic)"
# The experiment report's payload version. 2: code issues and densities leave
# out dependency advisories, runtime (DAST-only) issues and test code, all
# conditions share one instrument set, and issues_dedup counts apps and runs apart.
REPORT_VERSION = 2
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


def _num(data: Any, key: str) -> float:
    value = data.get(key) if isinstance(data, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _success_rate(completed: int, total: int, in_flight: int) -> float | None:
    """Completed / finished jobs; queued or running jobs have no outcome yet, and nothing finished is ``None``."""
    finished = total - in_flight
    return completed / finished if finished else None


def _job_summary(jobs_qs) -> dict[str, Any]:
    from backend.generation.services.experiment_results import output_truncated

    agg = jobs_qs.aggregate(
        total=Count("id"),
        avg_duration=Avg("duration_seconds"),
    )
    by_status = {row["status"]: row["n"] for row in jobs_qs.values("status").annotate(n=Count("id"))}
    completed = by_status.get(GenerationJob.Status.COMPLETED, 0)
    total = agg["total"] or 0
    in_flight = sum(by_status.get(s, 0) for s in _IN_FLIGHT)
    truncated = sum(1 for data in jobs_qs.values_list("result_data", flat=True) if output_truncated(data))
    # A requeued job's earlier attempts keep their spend in execution.prior_attempts_cost.
    total_cost = sum(
        _num(metrics, "cost") + _num(execution, "prior_attempts_cost")
        for metrics, execution in jobs_qs.values_list("metrics", "execution")
    )
    return {
        "total_jobs": total,
        "completed_jobs": completed,
        "failed_jobs": by_status.get(GenerationJob.Status.FAILED, 0),
        "cancelled_jobs": by_status.get(GenerationJob.Status.CANCELLED, 0),
        # Queued or running: no outcome yet, so outside the success rate.
        "in_flight_jobs": in_flight,
        "success_rate": _success_rate(completed, total, in_flight),
        "avg_duration": float(agg["avg_duration"]) if agg["avg_duration"] is not None else None,
        "by_status": by_status,
        # Jobs whose generated code was cut off at max_tokens — their findings
        # describe incomplete apps.
        "truncated_jobs": truncated,
        "total_cost": round(total_cost, 6),
    }


def _latest_result_ids(jobs_qs) -> list:
    """Latest result per (job, tool) — see ToolResult.latest_ids_per_job.

    Picking the latest *run* instead would drop every tool a later partial
    re-analysis (one tool, or only the AI reviewer) did not re-run.
    """
    return ToolResult.latest_ids_per_job(jobs_qs.values_list("id", flat=True))


def _stat(values: list[float]) -> dict[str, Any]:
    """Mean and sample stdev over runs; ``None`` when nothing was measured, stdev ``None`` below n=2."""
    if not values:
        return {"mean": None, "stdev": None, "n": 0}
    return {
        "mean": round(statistics.mean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else None,
        "n": len(values),
    }


def _is_truncated_job(job: GenerationJob) -> bool:
    from backend.generation.services.experiment_results import output_truncated

    return output_truncated(job.result_data)


def _trials(jobs, *, exclude_truncated: bool) -> tuple[list, list]:
    """The completed jobs that count as trials, and the truncated ones among the completed."""
    completed = [j for j in jobs if j.status == GenerationJob.Status.COMPLETED]
    truncated = [j for j in completed if _is_truncated_job(j)]
    trials = [j for j in completed if not (exclude_truncated and _is_truncated_job(j))]
    return trials, truncated


def _instrument(coverage: dict[str, Any]) -> tuple[set[str], set[str]]:
    """The counted tools (they measured every analyzed run) and the runs (job ids) they measured."""
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


def _unit(job: GenerationJob) -> tuple:
    """The app a run measures (study.app_unit): its condition and app; repeats of one cell are one app."""
    return (job.condition_id, job.model_id, job.mode, job.app_requirement_id or str(job.id))


def _experiment_stats(jobs_qs, *, exclude_truncated: bool = True, coverage: dict | None = None) -> dict[str, Any]:
    """Run-level statistics for one row's jobs (a model on a template, or an experiment condition).

    Each completed job is one trial (``n`` counts runs; repeats are not
    averaged here, the spread across them is the point). Truncated
    generations measure the token ceiling rather than the model, so they are
    excluded by default (but reported). Findings count only for *analyzed*
    trials, runs the counted instrument set measured (*coverage*: the
    report-wide one; computed over these trials when omitted), and only from
    its tools. ``findings_total`` is every counted finding;
    ``critical_high_normalized``, ``weighted_score`` and ``weighted_per_kloc``
    count code findings only (no dependency advisories, runtime findings or
    test code) by normalized severity, over non-test source LOC.
    AI-reviewer findings — opinions with their own run-to-run variance —
    are tracked separately, for the trials the reviewer measured.
    """
    trials, truncated = _trials(jobs_qs, exclude_truncated=exclude_truncated)
    trial_ids = [str(j.id) for j in trials]

    # Trials should share one prompt version; if they don't, the stats below
    # blend results from different templates and the reader needs to know.
    prompt_hashes = sorted({j.prompt_hash for j in trials if j.prompt_hash})

    result_ids = ToolResult.latest_ids_per_job(trial_ids)
    if coverage is None:
        coverage = tool_coverage(result_ids, include_ai=False)
    tools, measured = _instrument(coverage)
    analyzed = [j for j in trials if str(j.id) in measured]

    ai_slugs = AnalyzerTool.ai_slugs()
    result_to_job: dict[str, str] = {}
    ai_measured: set[str] = set()
    for rid, job_id, slug, status in ToolResult.objects.filter(id__in=result_ids).values_list(
        "id",
        "run__generation_job_id",
        "tool_slug",
        "status",
    ):
        result_to_job[str(rid)] = str(job_id)
        if slug in ai_slugs and status == ToolResult.Status.COMPLETED:
            ai_measured.add(str(job_id))

    dynamic = dynamic_slugs()
    raw_by_job: Counter = Counter()
    dep_by_job: Counter = Counter()
    code_by_job: dict[str, Counter] = defaultdict(Counter)
    ai_by_job: dict[str, Counter] = defaultdict(Counter)
    for row in (
        Finding.objects.filter(result_id__in=result_ids)
        .values("result_id", "result__tool_slug", "severity", "category", "file_path")
        .annotate(n=Count("id"))
    ):
        job_id = result_to_job.get(str(row["result_id"]))
        tool, n = row["result__tool_slug"], row["n"]
        sev = (row["severity"] or "").lower()
        if tool in ai_slugs:
            ai_by_job[job_id][sev] += n
            continue
        if tool not in tools or job_id not in measured:
            continue
        raw_by_job[job_id] += n
        kind = _finding_kind(tool, row["category"] or "", row["file_path"] or "", dynamic)
        if kind == "dependency":
            dep_by_job[job_id] += n
        elif kind == "code":
            code_by_job[job_id][sev] += n

    loc_by_job = {str(j.id): int(loc_from_job(j)["total_loc"] or 0) for j in trials}

    totals, code_totals, dep_totals, crit_high, weighted, per_kloc = [], [], [], [], [], []
    ai_totals, ai_crit_high = [], []
    costs, durations, tokens = [], [], []
    functional_rates, functional_passed = [], 0

    for job in trials:
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        if metrics.get("cost") is not None:
            costs.append(float(metrics["cost"]))
        duration = metrics.get("total_duration") or metrics.get("duration_seconds")
        if duration:
            durations.append(float(duration))
        if metrics.get("total_tokens"):
            tokens.append(float(metrics["total_tokens"]))
        functional = metrics.get("functional")
        # null pass_rate = ungraded (infra_error): excluded, not scored 0.
        if isinstance(functional, dict) and functional.get("pass_rate") is not None:
            functional_rates.append(float(functional["pass_rate"]))
            functional_passed += 1 if functional.get("passed") else 0

        jid = str(job.id)
        if jid in ai_measured:
            ai_sev = ai_by_job.get(jid, Counter())
            ai_totals.append(float(sum(ai_sev.values())))
            ai_crit_high.append(float(ai_sev["critical"] + ai_sev["high"]))
        if jid not in measured:
            continue
        code = code_by_job.get(jid, Counter())
        score = float(sum(SEVERITY_WEIGHTS.get(s, 0) * n for s, n in code.items()))
        totals.append(float(raw_by_job.get(jid, 0)))
        code_totals.append(float(sum(code.values())))
        dep_totals.append(float(dep_by_job.get(jid, 0)))
        crit_high.append(float(code["critical"] + code["high"]))
        weighted.append(score)
        if loc_by_job[jid]:
            per_kloc.append(round(score / loc_by_job[jid] * 1000, 4))

    # The empirical-score input, as /rankings builds it: repeats of one app
    # collapse to their mean, then means are summed (findings and LOC alike).
    units: dict[tuple, list[str]] = defaultdict(list)
    for job in analyzed:
        if loc_by_job[str(job.id)] > 0:
            units[_unit(job)].append(str(job.id))
    unit_findings = dict.fromkeys(_LEVELS, 0.0)
    unit_loc = 0.0
    for members in units.values():
        for jid in members:
            for sev, n in code_by_job.get(jid, Counter()).items():
                if sev in unit_findings:
                    unit_findings[sev] += n / len(members)
        unit_loc += sum(loc_by_job[j] for j in members) / len(members)

    return {
        "unit": "run",
        "trials": len(trials),
        "analyzed": len(analyzed),
        # Non-test source LOC of the analyzed trials: the denominator matching their findings.
        "analyzed_loc": sum(loc_by_job[str(j.id)] for j in analyzed),
        "truncated_excluded": len(truncated) if exclude_truncated else 0,
        "prompt_hashes": prompt_hashes,
        "mixed_prompt_versions": len(prompt_hashes) > 1,
        "severity_basis": NORMALIZED,
        "findings_total": _stat(totals),
        "code_findings_total": _stat(code_totals),
        "dependency_findings_total": _stat(dep_totals),
        "critical_high_normalized": _stat(crit_high),
        "weighted_score": _stat(weighted),
        "weighted_per_kloc": _stat(per_kloc),
        "ai_analyzed": len([j for j in trials if str(j.id) in ai_measured]),
        "ai_findings_total": _stat(ai_totals),
        "ai_critical_high_normalized": _stat(ai_crit_high),
        "cost": {**_stat(costs), "total": round(sum(costs), 6) if costs else None},
        "duration": _stat(durations),
        "tokens": _stat(tokens),
        "functional": {
            "jobs_probed": len(functional_rates),
            "jobs_passed": functional_passed,
            "pass_rate": _stat(functional_rates),
        },
        "density_basis": {
            "apps": len(units),
            "code_findings_normalized_heuristic": {k: round(v, 4) for k, v in unit_findings.items()}
            if units
            else None,
            "loc": round(unit_loc, 4) if units else None,
        },
    }


def _breakdowns(jobs_qs) -> dict[str, Any]:
    """study.issue_breakdowns over a whole report scope: one instrument set and one dedup for every row."""
    return issue_breakdowns(
        jobs_qs.select_related(
            "model",
            "app_requirement",
            "condition__model",
            "condition__profile",
            "condition__harness_preset",
        ),
    )


def _dedup_columns(per_app_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-tool deduplicated issue totals of one row's runs (``study.group_summary``: apps are the unit)."""
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


def _finding_columns(job_ids: list[str], coverage: dict[str, Any]) -> dict[str, Any]:
    """Raw finding counts of a row's analyzed runs: the tools' own levels, the normalized split (labelled), AI apart.

    Only runs the counted instrument set measured, and only its tools; a
    row with no measured run has unknown counts (``None``), not zero.
    """
    tools, measured = _instrument(coverage)
    analyzed = [j for j in job_ids if j in measured]
    result_ids = ToolResult.latest_ids_per_job(job_ids)
    base = Finding.objects.filter(result_id__in=result_ids)
    static = base.filter(result__tool_slug__in=tools, result__run__generation_job_id__in=analyzed)
    ai_slugs = AnalyzerTool.ai_slugs()
    ai_runs = {
        str(j)
        for j in ToolResult.objects.filter(
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


def _tools_for_jobs(jobs_qs) -> list[dict[str, Any]]:
    """Per-tool breakdown derived from each job's latest result per tool."""

    rows = (
        ToolResult.objects.filter(id__in=_latest_result_ids(jobs_qs))
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
    """Aggregate across one model's generation jobs + their analyses (*user*'s jobs; staff or None: all)."""

    model_id = config.get("model_id") or config.get("model_slug")
    if not model_id:
        msg = "model_id required for model_analysis report"
        raise ValueError(msg)

    try:
        model = LLMModel.objects.get(model_id=model_id)
    except LLMModel.DoesNotExist as e:
        msg = f"Model not found: {model_id}"
        raise ValueError(msg) from e

    jobs = _owned(GenerationJob.objects.filter(model=model), user).select_related("model")
    breakdown = _breakdowns(jobs)
    coverage = breakdown["coverage"]
    issues = _dedup_columns(breakdown["per_app"])
    tool_results = ToolResult.objects.filter(id__in=_latest_result_ids(jobs))

    return {
        "model": {
            "model_id": model.model_id,
            "model_name": model.model_name,
            "provider": model.provider,
        },
        "generation": _job_summary(jobs),
        "loc": loc_for_jobs(jobs),
        # Non-test source LOC of the analyzed runs, the denominator matching the findings below.
        "analyzed_loc": issues["loc"],
        **_finding_columns([str(j) for j in jobs.values_list("id", flat=True)], coverage),
        "issues_dedup": issues,
        "coverage": _coverage_block(coverage),
        "tools": _tools_for_jobs(jobs),
        "metrics_by_tool": _aggregate_tool_metrics(tool_results),
    }


def generate_template_comparison(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Compare models on a single application requirement template (*user*'s jobs; staff or None: all).

    Rows are grouped by ``(model, bundle_key)`` rather than model alone —
    jobs generated from different prompt/bundle versions on the same app
    are not comparable, so pooling them under one "model" row would blend
    results across template changes. Pass ``bundle_key`` or ``prompt_hash``
    in *config* to restrict the report to one specific prompt version. Every
    row is measured with the same instrument set (``coverage``).
    """

    template_slug = config.get("template_slug")
    if not template_slug:
        msg = "template_slug required for template_comparison report"
        raise ValueError(msg)

    try:
        template = AppRequirementTemplate.objects.get(slug=template_slug)
    except AppRequirementTemplate.DoesNotExist as e:
        msg = f"Template not found: {template_slug}"
        raise ValueError(msg) from e

    filter_models = config.get("filter_models") or []
    jobs = _owned(GenerationJob.objects.filter(app_requirement=template), user)
    if filter_models:
        jobs = jobs.filter(model__model_id__in=filter_models)
    if config.get("bundle_key"):
        jobs = jobs.filter(bundle_key=config["bundle_key"])
    if config.get("prompt_hash"):
        jobs = jobs.filter(prompt_hash=config["prompt_hash"])

    by_model_bundle: dict[tuple[str, str], dict[str, Any]] = {}
    for job in jobs.select_related("model"):
        model = job.model
        model_id = model.model_id if model else "unknown"
        key = (model_id, job.bundle_key or "")
        bucket = by_model_bundle.setdefault(
            key,
            {
                "model_id": model_id,
                "model_name": getattr(model, "model_name", model_id),
                "provider": getattr(model, "provider", ""),
                "bundle_key": job.bundle_key or "",
                "jobs": [],
            },
        )
        bucket["jobs"].append(job)

    exclude_truncated = bool(config.get("exclude_truncated", True))
    # One instrument set and one dedup over every row's trials at once: per-row
    # coverage would let a tool that failed on one row's app vanish from that row only.
    row_of_job: dict[str, tuple[str, str]] = {}
    trial_ids: list = []
    for key, bucket in by_model_bundle.items():
        for job in _trials(bucket["jobs"], exclude_truncated=exclude_truncated)[0]:
            row_of_job[str(job.id)] = key
            trial_ids.append(job.id)
    breakdown = _breakdowns(GenerationJob.objects.filter(id__in=trial_ids))
    coverage = breakdown["coverage"]
    per_row: dict[tuple[str, str], list] = defaultdict(list)
    for app in breakdown["per_app"]:
        per_row[row_of_job[app["job_id"]]].append(app)

    rows = []
    for key, bucket in by_model_bundle.items():
        sub_jobs = GenerationJob.objects.filter(
            id__in=[j.id for j in bucket["jobs"]],
        )
        row_trials = [job_id for job_id, row in row_of_job.items() if row == key]
        rows.append(
            {
                "model_id": bucket["model_id"],
                "model_name": bucket["model_name"],
                "provider": bucket["provider"],
                "bundle_key": bucket["bundle_key"],
                "generation": _job_summary(sub_jobs),
                "loc": loc_for_jobs(bucket["jobs"]),
                **_finding_columns(row_trials, coverage),
                "issues_dedup": _dedup_columns(per_row.get(key, [])),
                "stats": _experiment_stats(bucket["jobs"], exclude_truncated=exclude_truncated, coverage=coverage),
            },
        )

    rows.sort(
        key=lambda r: (r["generation"]["success_rate"] is not None, r["generation"]["success_rate"] or 0.0),
        reverse=True,
    )

    bundle_keys_seen = sorted({r["bundle_key"] for r in rows if r["bundle_key"]})
    mixed_bundle_versions = len(bundle_keys_seen) > 1

    # Rank-sensitivity assumes one row per model; with mixed bundle versions
    # take each model's first (highest success-rate) *analyzed* row rather
    # than feeding duplicate model_ids into the rank-swap comparison. A row
    # nothing measured has no score (not a perfect one), so it never stands
    # in for the model's analyzed row.
    seen_models: set[str] = set()
    sensitivity_rows = []
    for row in rows:
        if not row["stats"]["analyzed"] or row["model_id"] in seen_models:
            continue
        seen_models.add(row["model_id"])
        sensitivity_rows.append(row)

    return {
        "template": {
            "slug": template.slug,
            "name": template.name,
            "category": template.category,
        },
        "models": rows,
        "total_models": len(rows),
        # More than one bundle_key here means results came from different
        # prompt/bundle versions — filter by bundle_key for an apples-to-
        # apples comparison instead of reading across rows.
        "bundle_keys": bundle_keys_seen,
        "mixed_bundle_versions": mixed_bundle_versions,
        "coverage": _coverage_block(coverage),
        "coverage_note": _coverage_note(coverage),
        "sensitivity": _template_sensitivity(sensitivity_rows),
    }


def _template_sensitivity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank-stability of this template's models under alternative weights.

    The score's inputs describe one job set, each row's analyzed trials:
    their code findings (no dependency advisories, runtime findings or test
    code) over their non-test LOC, repeats of one app averaged first — the
    basis /rankings uses.
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

    Deliberately spans every execution (not latest-per-job): this report
    measures tool behavior across executions, not the state of any one
    codebase. Each tool's row counts its executions by outcome, so a tool
    that ran and found nothing reads as a measured zero (completed, or
    skipped as not applicable) instead of being missing, and
    ``findings_per_measured_execution`` gives the findings a denominator.
    Cancelled runs are left out. ``days`` narrows to runs created in that window.
    """
    from backend.analysis.services.runner import SKIP_NOT_APPLICABLE

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
    for slug, status, skip_reason in results.values_list("tool_slug", "status", "summary__skip_reason"):
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
        if status == ToolResult.Status.COMPLETED:
            row["completed"] += 1
            row["measured"] += 1
        elif status == ToolResult.Status.FAILED:
            row["failed"] += 1
        elif status == ToolResult.Status.SKIPPED:
            reason = skip_reason or "unspecified"
            row["skipped"][reason] = row["skipped"].get(reason, 0) + 1
            # Not applicable: the app has none of the files the tool reads, a measured zero.
            row["measured"] += int(reason == SKIP_NOT_APPLICABLE)
        else:
            row["unfinished"] += 1

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
    """Generation success/failure patterns over a configurable window (*user*'s jobs; staff or None: all)."""

    days = int(config.get("days", 7))
    since = timezone.now() - timezone.timedelta(days=days)
    jobs = _owned(GenerationJob.objects.filter(created_at__gte=since), user)

    by_model = []
    for row in (
        jobs.values("model__model_id", "model__model_name")
        .annotate(
            n=Count("id"),
            completed=Count("id", filter=Q(status=GenerationJob.Status.COMPLETED)),
            in_flight=Count("id", filter=Q(status__in=_IN_FLIGHT)),
            cancelled=Count("id", filter=Q(status=GenerationJob.Status.CANCELLED)),
            avg_duration=Avg("duration_seconds"),
        )
        .order_by("-n", "model__model_id")
    ):
        by_model.append(
            {
                "model_id": row["model__model_id"],
                "model_name": row["model__model_name"],
                "jobs": row["n"],
                "completed": row["completed"],
                "in_flight": row["in_flight"],
                "cancelled": row["cancelled"],
                "success_rate": _success_rate(row["completed"], row["n"], row["in_flight"]),
                "avg_duration": float(row["avg_duration"]) if row["avg_duration"] is not None else None,
            },
        )

    return {
        "window_days": days,
        "since": since.isoformat(),
        "summary": _job_summary(jobs),
        "loc": loc_for_jobs(jobs),
        "by_model": by_model,
    }


def owned_experiment(experiment_id: Any, user):
    """The experiment *experiment_id* if *user* may report on it (owner; staff: any); else ``ValueError``.

    An unknown id, a malformed id and another user's experiment all read
    "Experiment not found", so a report request cannot probe for ids.
    """
    from django.core.exceptions import ValidationError

    from backend.generation.models import Experiment

    if not experiment_id:
        msg = "experiment_id required for experiment_report report"
        raise ValueError(msg)
    qs = Experiment.objects.all()
    if user is None or not getattr(user, "is_authenticated", False):
        qs = qs.none()
    elif not getattr(user, "is_staff", False):
        qs = qs.filter(created_by=user)
    try:
        return qs.get(id=experiment_id)
    except (Experiment.DoesNotExist, ValidationError) as e:
        msg = f"Experiment not found: {experiment_id}"
        raise ValueError(msg) from e


def generate_experiment_report(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Results and per-condition code findings for one designed experiment.

    The statistics are ``experiment_results.compute`` -- the same leaderboard
    and app-blocked paired comparison vs the pre-registered baseline the
    Results tab shows (no all-pairs deltas). Each condition also gets the
    trial-level code statistics (findings, LOC, cost) from
    ``_experiment_stats``, over the sample the Results tab compares: each
    cell's current attempt, the repeats planned at launch, and the frozen
    plan's truncation rule. All conditions are measured with one instrument
    set (``coverage``). Only *user*'s experiments (staff: any) are reported on.
    """
    from backend.generation.services.analysis_plan import plan_excludes_truncated
    from backend.generation.services.experiment_results import ResultFilters
    from backend.generation.services.experiment_results import compute
    from backend.generation.services.experiment_results import frozen_plan
    from backend.generation.services.experiment_results import planned_repeats
    from backend.generation.services.experiments import condition_label as experiment_condition_label

    experiment = owned_experiment(config.get("experiment_id"), user)
    # A frozen analysis plan fixes the truncation rule, as on the Results tab.
    plan = frozen_plan(experiment)
    requested = config.get("exclude_truncated")
    exclude_truncated = plan_excludes_truncated(plan) if plan else bool(config.get("exclude_truncated", True))

    conditions = list(experiment.conditions.select_related("model", "profile", "harness_preset").order_by("id"))
    # Current attempts only (a retried cell counts once) and the planned
    # repeats only (repeats added after launch are beyond the plan).
    planned = planned_repeats(experiment)
    current = study_jobs(None, experiment_id=experiment.id)
    in_plan = Q(repeat_index__lt=planned) | Q(repeat_index__isnull=True)  # no index (legacy rows): the first repeat
    sample = list(current.filter(in_plan))
    trials, truncated = _trials(sample, exclude_truncated=exclude_truncated)
    breakdown = _breakdowns(GenerationJob.objects.filter(id__in=[j.id for j in trials]))
    coverage = breakdown["coverage"]
    per_condition: dict[Any, list] = defaultdict(list)
    for app in breakdown["per_app"]:
        per_condition[app["condition_id"]].append(app)
    jobs_by_condition: dict[Any, list] = defaultdict(list)
    for job in sample:
        jobs_by_condition[job.condition_id].append(job)

    rows = []
    for condition in conditions:
        jobs = jobs_by_condition.get(condition.id, [])
        cond_trials = _trials(jobs, exclude_truncated=exclude_truncated)[0]
        rows.append(
            {
                "condition_id": condition.id,
                # The leaderboard's name for the condition, so every table names it alike.
                "label": experiment_condition_label(condition),
                "model_id": condition.model.model_id,
                "model_name": condition.model.model_name,
                "bundle_key": condition.method_ref(),
                "generation": _job_summary(GenerationJob.objects.filter(id__in=[j.id for j in jobs])),
                "loc": loc_for_jobs(jobs),
                **_finding_columns([str(j.id) for j in cond_trials], coverage),
                "issues_dedup": _dedup_columns(per_condition.get(condition.id, [])),
                "stats": _experiment_stats(jobs, exclude_truncated=exclude_truncated, coverage=coverage),
            },
        )

    results = compute(experiment, None, ResultFilters(exclude_truncated=exclude_truncated), user)
    notes = list(results["notes"])
    if plan and requested is not None and bool(requested) != exclude_truncated:
        notes.append(
            f"The frozen analysis plan {'excludes' if exclude_truncated else 'keeps'} truncated runs; "
            f"the report's exclude_truncated={bool(requested)} setting does not apply.",
        )
    coverage_note = _coverage_note(coverage)
    if coverage_note:
        notes.append(coverage_note)
    return {
        "report_version": REPORT_VERSION,
        "experiment": {
            "id": str(experiment.id),
            "name": experiment.name,
            "slug": experiment.slug,
            "hypothesis": experiment.hypothesis,
            "status": experiment.status,
            "repeats": experiment.repeats,
            "baseline_condition_id": experiment.baseline_condition_id,
        },
        "sample": {
            "planned_repeats": planned,
            "runs": len(sample),
            "in_flight": sum(1 for j in sample if j.status in _IN_FLIGHT),
            "beyond_plan_excluded": current.exclude(in_plan).count(),
            "superseded_attempts_excluded": GenerationJob.objects.filter(experiment=experiment).count()
            - current.count(),
            "exclude_truncated": exclude_truncated,
            "truncation_rule": "analysis_plan" if plan else "report_config",
            "truncated_excluded": len(truncated) if exclude_truncated else 0,
        },
        "coverage": _coverage_block(coverage),
        "conditions": rows,
        "total_conditions": len(rows),
        "results": {
            "baseline_condition_id": results["baseline_condition_id"],
            "baseline_source": results["baseline_source"],
            "interim": results["interim"],
            "balanced": results["balanced"],
            "leaderboard": results["leaderboard"],
            "paired": results["paired"],
            "notes": notes,
            "generated_at": results["generated_at"].isoformat(),
        },
    }


def generate_comprehensive(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Platform-wide aggregate combining all report dimensions (*user*'s data; staff or None: all).

    Generation and tool analytics cover the last ``days``; the ranking
    sensitivity and the platform totals are all-time (``periods``).
    """

    from backend.rankings.services import aggregate_rankings
    from backend.rankings.services import compute_weight_sensitivity

    days = int(config.get("days", 30))
    jobs = _owned(GenerationJob.objects.all(), user)
    runs = _owned(AnalysisRun.objects.all(), user)
    total_runs = runs.count()
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
            "models_with_jobs": jobs.exclude(model__isnull=True).values("model_id").distinct().count(),
            "total_jobs": jobs.count(),
            "total_runs": total_runs,
            # Nothing analyzed is unmeasured, not zero findings (as in tool_analysis).
            "total_findings": Finding.objects.filter(result__run__in=runs).count() if total_runs else None,
        },
    }


GENERATORS = {
    "model_analysis": generate_model_analysis,
    "template_comparison": generate_template_comparison,
    "tool_analysis": generate_tool_analysis,
    "generation_analytics": generate_generation_analytics,
    "comprehensive": generate_comprehensive,
    "experiment_report": generate_experiment_report,
    "study_report": generate_study_report,
}

# Generators that take ``user=`` and report only on what that user owns
# (staff: everything). Every type does: a report must not hand one user
# another user's jobs, costs or findings.
USER_SCOPED = frozenset(GENERATORS)
