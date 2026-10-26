"""Report generators — produce JSON content for each report type.

Every analysis number in a report comes from the canonical measured runs of
the report's jobs under ONE frozen analysis plan (``rankings.services.
measured.select_measured``): stored measurements and ``measurement.
outcomes()``, never raw Finding rows recounted with report-specific rules.
Jobs that could not be measured are counted explicitly (``analysis_coverage``)
instead of contributing zeros. Statistics follow one convention: missing ->
None, ``stdev`` None below n=2, no deltas when a side has n=0. Generators are
scoped to the requesting user's data (staff: everything).
"""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.utils import timezone

from backend.analysis.engine import sample as sample_engine
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.rankings.services.aggregator import empirical_inputs
from backend.rankings.services.constants import SCORING_VERSION

# Severity weights shared with the rankings scoring so reports and /rankings
# weight the same measured issues the same way.
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.measured import FUNCTIONAL_SOURCE
from backend.rankings.services.measured import MEASUREMENT_PROBLEMS
from backend.rankings.services.measured import SEVERITIES
from backend.rankings.services.measured import MeasuredRun
from backend.rankings.services.measured import Selection
from backend.rankings.services.measured import add_severities
from backend.rankings.services.measured import default_rules
from backend.rankings.services.measured import descriptive_runs
from backend.rankings.services.measured import exclusion_reasons
from backend.rankings.services.measured import functional_is_conformance
from backend.rankings.services.measured import functional_rate
from backend.rankings.services.measured import select_measured
from backend.rankings.services.measured import weighted_issue_score
from backend.rankings.services.measured import zero_severities

from .loc import loc_for_jobs

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

REPORT_SCHEMA_VERSION = 2
# Boolean eligibility rules a report config may override (sample.DEFAULT_EXCLUSION_RULES).
RULE_KEYS = ("exclude_invalid", "exclude_empty_output", "exclude_truncated", "exclude_run_limits")
_RULE_KEYS = RULE_KEYS
# Why a trial has no conformance pass rate; ``pass_rate.n_missing`` is their sum.
FUNCTIONAL_MISSING_REASONS = ("not_evaluated", "not_applicable", "infra_error", "other_instrument", "no_rate")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_scoped(user) -> bool:
    """True when results must be restricted to *user*'s own data (non-staff)."""
    return bool(user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_staff", False))


def _owned(qs, user, field: str = "created_by"):
    return qs.filter(**{field: user}) if _is_scoped(user) else qs


def _rules(config: dict[str, Any]) -> dict[str, Any]:
    """Eligibility rules: the sample defaults, overridable per report config."""
    rules = default_rules()
    for key in _RULE_KEYS:
        if key in config:
            rules[key] = bool(config[key])
    return rules


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _stat(values: list[float], *, n_missing: int | None = None) -> dict[str, Any]:
    """Mean/median/sample stdev of the known values; None when undefined (stdev: n<2)."""
    out: dict[str, Any] = {
        "mean": round(statistics.mean(values), 4) if values else None,
        "median": round(statistics.median(values), 4) if values else None,
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else None,
        "n": len(values),
    }
    if n_missing is not None:
        out["n_missing"] = n_missing
    return out


def _safe_target_meta(job: GenerationJob) -> dict[str, Any]:
    try:
        return sample_engine.target_meta(job) or {}
    except Exception:
        # A job whose result shape the resolver rejects is reported as having
        # unknown meta (so it is not excluded on a guess), not a crashed report.
        logger.exception("target_meta failed for job %s", job.id)
        return {}


def _job_summary(jobs_qs) -> dict[str, Any]:
    agg = jobs_qs.aggregate(
        total=Count("id"),
        avg_duration=Avg("duration_seconds"),
    )
    by_status = {row["status"]: row["n"] for row in jobs_qs.values("status").annotate(n=Count("id"))}
    completed = by_status.get(GenerationJob.Status.COMPLETED, 0)
    failed = by_status.get(GenerationJob.Status.FAILED, 0)
    cancelled = by_status.get(GenerationJob.Status.CANCELLED, 0)
    finished = completed + failed + cancelled
    total = agg["total"] or 0
    truncated = run_stopped = 0
    costs: list[float] = []
    for job in jobs_qs.select_related("model", "app_requirement"):
        if job.status == GenerationJob.Status.COMPLETED:
            # Output truncation and run-level stops are different things
            # (a stopped agent run is a valid sample): counted apart.
            flags = sample_engine.truncation_flags(_safe_target_meta(job))
            truncated += 1 if flags["truncated"] else 0
            run_stopped += 1 if flags["run_stopped"] else 0
        cost = _number((job.metrics or {}).get("cost")) if isinstance(job.metrics, dict) else None
        if cost is not None:
            costs.append(cost)
    return {
        "total_jobs": total,
        "completed_jobs": completed,
        "failed_jobs": failed,
        # Of the jobs that reached a terminal state; queued/running jobs are
        # not failures, and no finished job means the rate is unknown.
        "success_rate": round(completed / finished, 4) if finished else None,
        "avg_duration": round(float(agg["avg_duration"]), 2) if agg["avg_duration"] is not None else None,
        "by_status": by_status,
        # Completed jobs whose generation was cut off (token ceiling, truncated
        # agent calls): their code describes an incomplete app.
        "truncated_jobs": truncated,
        # Completed agent jobs that ended at a run-level limit (wall clock,
        # budget, memory cap): valid samples unless ``exclude_run_limits``.
        "run_stopped_jobs": run_stopped,
        "total_cost": round(sum(costs), 6) if costs else None,
    }


def _analysis_block(runs: list[MeasuredRun]) -> dict[str, Any]:
    """Summed counted static findings/issues and AI consensus of measured runs."""
    static_runs = [r for r in runs if r.static_available]
    ai_runs = [r for r in runs if r.ai_available]
    findings, issues, ai = zero_severities(), zero_severities(), zero_severities()
    for run in static_runs:
        add_severities(findings, run.findings_by_severity())
        add_severities(issues, run.issues_by_severity())
    for run in ai_runs:
        add_severities(ai, run.ai_by_severity())
    return {
        "measured_jobs": len(runs),
        "static_measured_jobs": len(static_runs),
        "ai_reviewed_jobs": len(ai_runs),
        "findings": findings if static_runs else None,
        "total_findings": sum(r.findings_total() for r in static_runs) if static_runs else None,
        "issues": issues if static_runs else None,
        "total_issues": sum(r.issues_total() for r in static_runs) if static_runs else None,
        "ai_findings": ai if ai_runs else None,
        "ai_total": sum(r.ai_total() for r in ai_runs) if ai_runs else None,
    }


def _loc_block(runs: list[MeasuredRun]) -> dict[str, Any]:
    """SLOC of the measured runs — the same jobs the findings come from."""
    per_job = [
        {
            "job_id": str(r.job_id),
            "run_id": str(r.run_id),
            "measured_loc": r.measured_sloc(),
            "total_loc": r.total_sloc(),
        }
        for r in runs
    ]
    measured = [p["measured_loc"] for p in per_job if p["measured_loc"] is not None]
    total = [p["total_loc"] for p in per_job if p["total_loc"] is not None]
    return {
        "source": "analysis_measurement",
        # Code the model wrote (measured scope: authored/added/modified lines).
        "measured_loc": sum(measured) if measured else None,
        # Every included file (lockfiles, build output and assets excluded).
        "total_loc": sum(total) if total else None,
        "counted": len(per_job),
        "per_job": per_job,
    }


def _tools_for_runs(run_ids: list[Any]) -> list[dict[str, Any]]:
    """Per-tool execution outcome in the measured runs (a failed tool is never 'clean')."""
    by_tool: dict[str, dict[str, Any]] = {}
    for slug, status, version in ToolResult.objects.filter(run_id__in=run_ids).values_list(
        "tool_slug",
        "status",
        "tool_version",
    ):
        row = by_tool.setdefault(
            slug,
            {"analyzer": slug, "tasks": 0, "statuses": {}, "versions": set()},
        )
        row["tasks"] += 1
        row["statuses"][status] = row["statuses"].get(status, 0) + 1
        if version:
            row["versions"].add(version)
    out = []
    for row in by_tool.values():
        row["succeeded"] = row["statuses"].get(ToolResult.Status.SUCCEEDED, 0)
        row["versions"] = sorted(row["versions"])
        out.append(row)
    return sorted(out, key=lambda r: (-r["tasks"], r["analyzer"]))


def _aggregate_tool_metrics(results_qs) -> dict[str, Any]:
    """Aggregate ``ToolResult.metrics`` of *succeeded* results per tool.

    Numeric top-level keys are averaged (with min/max); dict-of-numbers keys
    (e.g. a ``rank_distribution``) are summed key-wise; anything else (lists
    such as top functions) is skipped. Failed executions have no trustworthy
    metrics and are left out rather than averaged in.
    """

    numeric: dict[str, dict[str, list[float]]] = {}
    distributions: dict[str, dict[str, dict[str, float]]] = {}
    result_counts: dict[str, int] = {}

    for tool_slug, metrics in (
        results_qs.filter(status=ToolResult.Status.SUCCEEDED).exclude(metrics={}).values_list("tool_slug", "metrics")
    ):
        if not isinstance(metrics, dict):
            continue
        result_counts[tool_slug] = result_counts.get(tool_slug, 0) + 1
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric.setdefault(tool_slug, {}).setdefault(key, []).append(value)
            elif (
                isinstance(value, dict)
                and value
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value.values())
            ):
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


def _classify_jobs(
    jobs: list[GenerationJob],
    selection: Selection,
    rules: dict[str, Any],
) -> tuple[list[GenerationJob], dict[Any, list[str]]]:
    """Split completed jobs into eligible trials and excluded jobs (with reasons).

    A measured job is judged on its run's ``target_meta`` snapshot (done by
    the selection); any other job on its current meta, by the same rules.
    """
    trials: list[GenerationJob] = []
    excluded: dict[Any, list[str]] = {}
    for job in jobs:
        if job.id in selection.measured:
            trials.append(job)
            continue
        reasons = [r for r in selection.excluded.get(job.id, []) if r not in MEASUREMENT_PROBLEMS]
        if not reasons and job.id not in selection.excluded:
            reasons = exclusion_reasons(_safe_target_meta(job), rules)
        if reasons:
            excluded[job.id] = reasons
        else:
            trials.append(job)
    return trials, excluded


class _JobView:
    """A job's metrics/result_data as ``sample.functional_from_job`` reads them."""

    def __init__(self, job: GenerationJob, metrics: dict[str, Any]) -> None:
        self.id = job.id
        self.pk = job.pk
        self.metrics = metrics
        self.result_data = job.result_data


def _job_functional(job: GenerationJob) -> dict[str, Any]:
    """The conformance record of a job without a measured run (None-safe).

    ``sample.functional_from_job`` on the job, except that a
    ``metrics.functional`` written by another instrument (the runtime smoke
    probe of older data: no ``source``/``status``) is ignored, so only the
    evaluator's own record (``result_data.evaluation``) can supply a rate.
    ``other_source`` names the ignored instrument. (The probe now writes
    ``metrics.smoke`` and never this slot; that key is never read here.)
    """
    metrics = job.metrics if isinstance(job.metrics, dict) else {}
    functional = metrics.get("functional")
    other_source = None
    if isinstance(functional, dict) and functional and functional.get("source") != FUNCTIONAL_SOURCE:
        other_source = str(functional.get("source") or "smoke")
        job = _JobView(job, {k: v for k, v in metrics.items() if k != "functional"})
    try:
        record = dict(sample_engine.functional_from_job(job) or {})
    except Exception:
        logger.exception("functional_from_job failed for job %s", getattr(job, "id", "?"))
        record = {}
    record["other_source"] = other_source
    return record


def _functional_stats(trials: list[GenerationJob], runs_by_job: dict[Any, MeasuredRun]) -> dict[str, Any]:
    """Conformance pass rate of the trials, one instrument, missing data kept apart.

    A measured trial is read from its run's measurement snapshot only — the
    same value /rankings and the report's ``empirical`` block use, never the
    job's live ``metrics.functional``; any other trial from its job's
    conformance record. Only the conformance evaluator counts.

    Every trial is either measured (``jobs_probed`` = ``pass_rate.n``) or
    missing (``pass_rate.n_missing``), and ``missing_reasons`` splits the
    missing ones (they sum to ``n_missing``): ``not_evaluated`` (no
    evaluation), ``not_applicable`` / ``infra_error`` (the evaluator could
    not judge the app; never a 0%), ``other_instrument`` (the only record is
    another instrument's) and ``no_rate`` (an evaluator status without a
    usable rate). ``jobs_unknown`` = ``n_missing`` - ``not_evaluated``.
    ``other_instruments`` counts, per instrument, the trials carrying a
    result of that instrument that was not counted (the only record, or a
    legacy smoke result found in the job's functional slot).
    """
    rates: list[float] = []
    passed = from_measurement = from_job = 0
    by_status: dict[str, int] = {}
    missing = dict.fromkeys(FUNCTIONAL_MISSING_REASONS, 0)
    other_instruments: dict[str, int] = {}
    versions: set[str] = set()
    for job in trials:
        run = runs_by_job.get(job.id)
        if run is not None:
            block = run.functional
            rate = run.functional_pass_rate()
            from_measurement += 1
            ignored = None
        else:
            block = _job_functional(job)
            rate = functional_rate(block)
            from_job += 1
            ignored = block.get("other_source")
        own_other = None if functional_is_conformance(block) else str(block.get("source"))
        status = str(block.get("status") or "not_evaluated")
        by_status[status] = by_status.get(status, 0) + 1
        for instrument in {i for i in (own_other, ignored) if i}:
            other_instruments[instrument] = other_instruments.get(instrument, 0) + 1
        if rate is None:
            if own_other:
                reason = "other_instrument"
            elif status in missing:
                reason = status
            else:
                reason = "no_rate"
            missing[reason] += 1
            continue
        rates.append(rate)
        passed += 1 if status == "passed" else 0
        if block.get("evaluator_version") is not None:
            versions.add(str(block["evaluator_version"]))
    n_missing = len(trials) - len(rates)
    return {
        "source": FUNCTIONAL_SOURCE,
        "jobs_probed": len(rates),
        "jobs_passed": passed,
        # A record exists but carries no rate (evaluator infra error, no app
        # spec, another instrument): unknown, never 0.
        "jobs_unknown": n_missing - missing["not_evaluated"],
        "pass_rate": _stat(rates, n_missing=n_missing),
        "missing_reasons": missing,
        "by_status": dict(sorted(by_status.items())),
        "other_instruments": dict(sorted(other_instruments.items())),
        "evaluator_versions": sorted(versions),
        "mixed_evaluator_versions": len(versions) > 1,
        "from_measurement": from_measurement,
        "from_job": from_job,
    }


def _values(runs: Iterable[MeasuredRun], getter) -> tuple[list[float], int]:
    values, missing = [], 0
    for run in runs:
        value = getter(run)
        if value is None:
            missing += 1
        else:
            values.append(float(value))
    return values, missing


def _crit_high(counts: dict[str, int] | None) -> int | None:
    return None if counts is None else counts["critical"] + counts["high"]


def _weighted_per_kloc(run: MeasuredRun) -> float | None:
    sloc = run.measured_sloc()
    # Density only where the measurement defines one (min SLOC, static available).
    if not sloc or run.outcomes.get("static_issues_per_kloc") is None:
        return None
    return round(weighted_issue_score(run.issues_by_severity(), SEVERITY_WEIGHTS) / sloc * 1000, 4)


def _outcome_stats(runs: list[MeasuredRun]) -> dict[str, Any]:
    keys = sorted({key for run in runs for key in run.outcomes})
    out: dict[str, Any] = {}
    for key in keys:
        raw = [run.outcomes.get(key) for run in runs]
        if any(isinstance(v, bool) for v in raw if v is not None):
            known = [bool(v) for v in raw if v is not None]
            out[key] = {
                "share_true": round(sum(known) / len(known), 4) if known else None,
                "n": len(known),
                "n_missing": len(raw) - len(known),
            }
            continue
        values = [float(v) for v in raw if _number(v) is not None]
        out[key] = _stat(values, n_missing=len(raw) - len(values))
    return out


def _experiment_stats(
    jobs_qs,
    *,
    exclude_truncated: bool = True,
    selection: Selection | None = None,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trial-level statistics for one group of jobs (a model, a condition, ...).

    Each eligible completed job is one trial. Generation-side numbers (cost,
    duration, tokens) cover all trials; analysis numbers cover the trials
    with a canonical measured run under the selection's plan, and
    ``n_missing`` / ``analysis`` say how many trials lack them. The
    functional pass rate covers all trials through one reader
    (:func:`_functional_stats`). Pass the same *selection* to every group of
    one report so they share one plan.
    """
    if rules is None:
        rules = _rules({"exclude_truncated": exclude_truncated})
    jobs = list(jobs_qs.select_related("model", "app_requirement"))
    completed = [j for j in jobs if j.status == GenerationJob.Status.COMPLETED]
    if selection is None:
        selection = select_measured([j.id for j in completed], rules=rules)
    trials, excluded = _classify_jobs(completed, selection, rules)
    trial_ids = [j.id for j in trials]
    runs = selection.runs_for(trial_ids)

    excluded_reasons: dict[str, int] = {}
    for reasons in excluded.values():
        for reason in reasons:
            excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1

    # Trials should share one prompt version; if they don't, the stats below
    # blend results from different templates and the reader needs to know.
    prompt_hashes = sorted({j.prompt_hash for j in trials if j.prompt_hash})

    costs, durations, tokens = [], [], []
    for job in trials:
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        if (cost := _number(metrics.get("cost"))) is not None:
            costs.append(cost)
        duration = _number(metrics.get("total_duration"))
        if duration is None:
            duration = _number(metrics.get("duration_seconds"))
        if duration is not None:
            durations.append(duration)
        if (token_count := _number(metrics.get("total_tokens"))) is not None:
            tokens.append(token_count)

    findings_total, findings_missing = _values(runs, MeasuredRun.findings_total)
    crit_high, crit_high_missing = _values(runs, lambda r: _crit_high(r.findings_by_severity()))
    issues_total, issues_missing = _values(runs, MeasuredRun.issues_total)
    weighted, weighted_missing = _values(
        runs,
        lambda r: weighted_issue_score(r.issues_by_severity(), SEVERITY_WEIGHTS),
    )
    per_kloc, per_kloc_missing = _values(runs, _weighted_per_kloc)
    ai_totals, ai_missing = _values(runs, MeasuredRun.ai_total)
    ai_crit_high, ai_crit_missing = _values(runs, lambda r: _crit_high(r.ai_by_severity()))
    not_measured = len(trials) - len(runs)
    coverage = selection.coverage(trial_ids)
    coverage.pop("run_ids", None)

    return {
        "trials": len(trials),
        "analyzed": len(runs),
        "truncated_excluded": sum(1 for reasons in excluded.values() if any("truncat" in r for r in reasons)),
        "excluded": len(excluded),
        "excluded_reasons": dict(sorted(excluded_reasons.items())),
        "analysis": coverage,
        "prompt_hashes": prompt_hashes,
        "mixed_prompt_versions": len(prompt_hashes) > 1,
        # n_missing counts trials without the value (not measured, or the
        # measurement says it is unavailable) — never folded in as zeros.
        "findings_total": _stat(findings_total, n_missing=findings_missing + not_measured),
        "critical_high": _stat(crit_high, n_missing=crit_high_missing + not_measured),
        "issues_total": _stat(issues_total, n_missing=issues_missing + not_measured),
        "weighted_score": _stat(weighted, n_missing=weighted_missing + not_measured),
        "weighted_per_kloc": _stat(per_kloc, n_missing=per_kloc_missing + not_measured),
        "ai_findings_total": _stat(ai_totals, n_missing=ai_missing + not_measured),
        "ai_critical_high": _stat(ai_crit_high, n_missing=ai_crit_missing + not_measured),
        "outcomes": _outcome_stats(runs),
        # Generation-side numbers cover every trial; n_missing = trials whose
        # job recorded no such value (as for the analysis numbers above).
        "cost": {**_stat(costs, n_missing=len(trials) - len(costs)), "total": round(sum(costs), 6) if costs else None},
        "duration": _stat(durations, n_missing=len(trials) - len(durations)),
        "tokens": _stat(tokens, n_missing=len(trials) - len(tokens)),
        "functional": _functional_stats(trials, {r.job_id: r for r in runs}),
    }


def _provenance(selection: Selection, job_ids: Iterable[Any] | None = None) -> dict[str, Any]:
    coverage = selection.coverage(job_ids)
    runs = selection.runs_for(job_ids) if job_ids is not None else list(selection.measured.values())
    versions = sorted(
        {str(r.functional_evaluator_version()) for r in runs if r.functional_evaluator_version() is not None},
    )
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": timezone.now().isoformat(),
        "scoring_version": SCORING_VERSION,
        "severity_weights": dict(SEVERITY_WEIGHTS),
        "plan_hash": selection.plan_hash,
        "plan_explicit": selection.explicit_plan,
        "plan_rule": coverage["plan_rule"],
        "plans": coverage["plans"],
        "plan_details": coverage["plan_details"],
        "mixed_plans": coverage["mixed_plans"],
        "complete_only": selection.complete_only,
        "exclusion_rules": dict(selection.rules),
        "run_ids": coverage["run_ids"],
        # Functional pass rates: the conformance evaluator only, measured
        # trials from their run's snapshot (as /rankings), others from the
        # job's own conformance record.
        "functional": {
            "source": FUNCTIONAL_SOURCE,
            "basis": "measurement snapshot for measured trials; the job's conformance record otherwise",
            "evaluator_versions": versions,
        },
    }


def _coverage(selection: Selection, job_ids: Iterable[Any] | None = None) -> dict[str, Any]:
    coverage = selection.coverage(job_ids)
    coverage.pop("run_ids", None)
    return coverage


def _completed_ids(jobs_qs) -> list[Any]:
    return list(jobs_qs.filter(status=GenerationJob.Status.COMPLETED).values_list("id", flat=True))


def _group_row(jobs_qs, selection: Selection, rules: dict[str, Any]) -> dict[str, Any]:
    """Generation summary + measured analysis of one group of jobs."""
    completed_ids = _completed_ids(jobs_qs)
    runs = selection.runs_for(completed_ids)
    analysis = _analysis_block(runs)
    return {
        "generation": _job_summary(jobs_qs),
        "loc": _loc_block(runs),
        "findings": analysis["findings"],
        "total_findings": analysis["total_findings"],
        "issues": analysis["issues"],
        "total_issues": analysis["total_issues"],
        "ai_findings": analysis["ai_findings"],
        "ai_total": analysis["ai_total"],
        "stats": _experiment_stats(jobs_qs, selection=selection, rules=rules),
        "empirical": {k: v for k, v in empirical_inputs(runs).items() if k != "run_ids"},
    }


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def generate_model_analysis(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Aggregate across one model's generation jobs + their measured analyses."""

    model_id = config.get("model_id") or config.get("model_slug")
    if not model_id:
        msg = "model_id required for model_analysis report"
        raise ValueError(msg)

    try:
        model = LLMModel.objects.get(model_id=model_id)
    except LLMModel.DoesNotExist as e:
        msg = f"Model not found: {model_id}"
        raise ValueError(msg) from e

    rules = _rules(config)
    jobs = _owned(GenerationJob.objects.filter(model=model), user)
    completed_ids = _completed_ids(jobs)
    selection = select_measured(completed_ids, plan_hash=config.get("plan_hash") or None, rules=rules)
    row = _group_row(jobs, selection, rules)
    run_ids = [r.run_id for r in selection.runs_for(completed_ids)]

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": {
            "model_id": model.model_id,
            "model_name": model.model_name,
            "provider": model.provider,
        },
        **row,
        "tools": _tools_for_runs(run_ids),
        "metrics_by_tool": _aggregate_tool_metrics(ToolResult.objects.filter(run_id__in=run_ids)),
        "analysis_coverage": _coverage(selection, completed_ids),
        "provenance": _provenance(selection, completed_ids),
    }


def generate_template_comparison(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Compare models on a single application requirement template.

    Rows are grouped by ``(model, bundle_key)`` rather than model alone —
    jobs generated from different prompt/bundle versions on the same app
    are not comparable, so pooling them under one "model" row would blend
    results across template changes. Pass ``bundle_key`` or ``prompt_hash``
    in *config* to restrict the report to one specific prompt version. All
    rows share one analysis plan (``plan_hash`` in config pins it).
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

    rules = _rules(config)
    if "exclude_truncated" not in config:
        rules["exclude_truncated"] = True
    completed_ids = _completed_ids(jobs)
    selection = select_measured(completed_ids, plan_hash=config.get("plan_hash") or None, rules=rules)

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
                "job_ids": [],
            },
        )
        bucket["job_ids"].append(job.id)

    rows = []
    for bucket in by_model_bundle.values():
        sub_jobs = GenerationJob.objects.filter(id__in=bucket["job_ids"])
        rows.append(
            {
                "model_id": bucket["model_id"],
                "model_name": bucket["model_name"],
                "provider": bucket["provider"],
                "bundle_key": bucket["bundle_key"],
                **_group_row(sub_jobs, selection, rules),
            },
        )

    rows.sort(key=lambda r: (-(r["generation"]["success_rate"] or 0.0), r["model_id"], r["bundle_key"]))

    bundle_keys_seen = sorted({r["bundle_key"] for r in rows if r["bundle_key"]})
    mixed_bundle_versions = len(bundle_keys_seen) > 1

    # Rank-sensitivity assumes one row per model; with mixed bundle versions
    # take each model's first (highest success-rate) row rather than feeding
    # duplicate model_ids into the rank-swap comparison.
    seen_models: set[str] = set()
    sensitivity_rows = []
    for row in rows:
        if row["model_id"] in seen_models:
            continue
        seen_models.add(row["model_id"])
        sensitivity_rows.append(row)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
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
        "sensitivity": _template_sensitivity(sensitivity_rows),
        "analysis_coverage": _coverage(selection, completed_ids),
        "provenance": _provenance(selection, completed_ids),
    }


def _template_sensitivity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank-stability of the rows' models under alternative severity weights.

    Uses each row's measured empirical inputs (issues + measured SLOC of the
    same analysed jobs + their functional pass rate), i.e. the /rankings score.
    """

    from backend.rankings.services import compute_empirical_quality
    from backend.rankings.services import compute_weight_sensitivity

    entries = []
    for row in rows:
        entry = {"model_id": row["model_id"], **(row.get("empirical") or {})}
        entry["empirical_quality_score"] = compute_empirical_quality(entry)
        entries.append(entry)
    return compute_weight_sensitivity(entries)


def generate_tool_analysis(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Behaviour of one (or all) analyzer(s) across executions.

    Deliberately spans every non-canary run (not one per job): this report
    measures tool behaviour, not the state of any codebase. Only succeeded
    executions count as scans; failures are reported by status and reason.
    """

    tool_name = config.get("tool_name")
    tasks = _owned(AnalysisRun.objects.exclude(purpose=AnalysisRun.Purpose.CANARY), user)
    if config.get("filter_model"):
        tasks = tasks.filter(generation_job__model__model_id=config["filter_model"])

    results = ToolResult.objects.filter(run__in=tasks)
    if tool_name:
        results = results.filter(tool_slug=tool_name)

    by_tool: dict[str, dict[str, Any]] = {}
    durations: dict[str, list[float]] = {}
    for slug, status, reason, version, duration in results.values_list(
        "tool_slug",
        "status",
        "failure_reason",
        "tool_version",
        "duration_seconds",
    ):
        row = by_tool.setdefault(
            slug,
            {
                "analyzer": slug,
                "executions": 0,
                "statuses": {},
                "failure_reasons": {},
                "versions": set(),
                "total": 0,
                "counted": 0,
                "by_severity": zero_severities(),
            },
        )
        row["executions"] += 1
        row["statuses"][status] = row["statuses"].get(status, 0) + 1
        if reason:
            row["failure_reasons"][reason] = row["failure_reasons"].get(reason, 0) + 1
        if version:
            row["versions"].add(version)
        if status == ToolResult.Status.SUCCEEDED and duration is not None:
            durations.setdefault(slug, []).append(float(duration))

    findings_qs = Finding.objects.filter(result__in=results.filter(status=ToolResult.Status.SUCCEEDED))
    for row in findings_qs.values("tool_slug", "severity", "counted").annotate(n=Count("id")):
        bucket = by_tool.get(row["tool_slug"])
        if bucket is None:
            continue
        bucket["total"] += row["n"]
        if row["counted"]:
            bucket["counted"] += row["n"]
        sev = (row["severity"] or "").lower()
        if sev in SEVERITIES:
            bucket["by_severity"][sev] += row["n"]

    tools = []
    for slug, row in by_tool.items():
        scans = row["statuses"].get(ToolResult.Status.SUCCEEDED, 0)
        terminal = sum(n for s, n in row["statuses"].items() if s in ToolResult.TERMINAL_STATUSES)
        attempted = terminal - row["statuses"].get(ToolResult.Status.NOT_APPLICABLE, 0)
        attempted -= row["statuses"].get(ToolResult.Status.SKIPPED, 0)
        attempted -= row["statuses"].get(ToolResult.Status.CANCELLED, 0)
        tools.append(
            {
                **row,
                "versions": sorted(row["versions"]),
                "scans": scans,
                "failed": row["statuses"].get(ToolResult.Status.FAILED, 0),
                "timed_out": row["statuses"].get(ToolResult.Status.TIMED_OUT, 0),
                "not_applicable": row["statuses"].get(ToolResult.Status.NOT_APPLICABLE, 0),
                # Of the executions that actually ran the tool on files.
                "success_rate": round(scans / attempted, 4) if attempted else None,
                "findings_per_scan": round(row["total"] / scans, 2) if scans else None,
                "mean_duration_s": round(statistics.mean(durations[slug]), 2) if durations.get(slug) else None,
            },
        )
    tools.sort(key=lambda r: (-r["total"], r["analyzer"]))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool_filter": tool_name,
        "tools": tools,
        "metrics_by_tool": _aggregate_tool_metrics(results),
        "total_findings": sum(t["total"] for t in tools),
        "total_tasks": tasks.count(),
        "total_executions": sum(t["executions"] for t in tools),
        "provenance": {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": timezone.now().isoformat(),
            "scope": "all non-canary runs (tool behaviour, not per-job measurement)",
        },
    }


def generate_generation_analytics(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Generation success/failure patterns over a configurable window."""

    days = int(config.get("days", 7))
    since = timezone.now() - timezone.timedelta(days=days)
    jobs = _owned(GenerationJob.objects.filter(created_at__gte=since), user)

    by_model = []
    for row in (
        jobs.values("model__model_id", "model__model_name")
        .annotate(
            n=Count("id"),
            avg_duration=Avg("duration_seconds"),
        )
        .order_by("-n", "model__model_id")
    ):
        model_jobs = jobs.filter(model__model_id=row["model__model_id"])
        completed = model_jobs.filter(status=GenerationJob.Status.COMPLETED).count()
        finished = model_jobs.filter(
            status__in=[
                GenerationJob.Status.COMPLETED,
                GenerationJob.Status.FAILED,
                GenerationJob.Status.CANCELLED,
            ],
        ).count()
        by_model.append(
            {
                "model_id": row["model__model_id"],
                "model_name": row["model__model_name"],
                "jobs": row["n"],
                "completed": completed,
                "success_rate": round(completed / finished, 4) if finished else None,
                "avg_duration": round(float(row["avg_duration"]), 2) if row["avg_duration"] is not None else None,
            },
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "window_days": days,
        "since": since.isoformat(),
        "summary": _job_summary(jobs),
        "loc": loc_for_jobs(jobs.filter(status=GenerationJob.Status.COMPLETED)),
        "by_model": by_model,
    }


_DELTA_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("weighted_per_kloc", ("stats", "weighted_per_kloc")),
    ("findings_total", ("stats", "findings_total")),
    ("critical_high", ("stats", "critical_high")),
    ("issues_total", ("stats", "issues_total")),
    ("functional_pass_rate", ("stats", "functional", "pass_rate")),
    ("cost", ("stats", "cost")),
    ("duration", ("stats", "duration")),
)


def _dig(row: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = row
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _pairwise_condition_deltas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every unordered condition pair, diffed on the headline trial metrics.

    Descriptive only (the study report carries the tests and effect sizes).
    A metric gets no delta when either side has no observations (n=0) —
    a difference against "no data" is not a difference.
    """

    comparisons = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            deltas: dict[str, Any] = {}
            for metric_name, path in _DELTA_METRICS:
                stat_a, stat_b = _dig(a, path), _dig(b, path)
                va = _number((stat_a or {}).get("mean")) if isinstance(stat_a, dict) else None
                vb = _number((stat_b or {}).get("mean")) if isinstance(stat_b, dict) else None
                n_a = (stat_a or {}).get("n", 0) if isinstance(stat_a, dict) else 0
                n_b = (stat_b or {}).get("n", 0) if isinstance(stat_b, dict) else 0
                if va is None or vb is None or not n_a or not n_b:
                    deltas[metric_name] = None
                    continue
                diff = round(vb - va, 4)
                deltas[metric_name] = {
                    "a": va,
                    "b": vb,
                    "n_a": n_a,
                    "n_b": n_b,
                    "delta": diff,
                    "pct_change": round((diff / va) * 100, 2) if va else None,
                }
            comparisons.append(
                {
                    "condition_a": {"id": a["condition_id"], "label": a["label"]},
                    "condition_b": {"id": b["condition_id"], "label": b["label"]},
                    "deltas": deltas,
                },
            )
    return comparisons


def generate_experiment_report(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Per-condition stats and pairwise A/B deltas for one designed experiment.

    Each :class:`ExperimentCondition` is one arm of the model x bundle matrix.
    All conditions are measured under one analysis plan (the one covering
    most of the experiment's jobs unless ``plan_hash`` pins it), so their
    numbers are comparable; jobs analysed otherwise are counted, not mixed in.
    """

    from django.core.exceptions import ValidationError

    from backend.generation.models import Experiment

    experiment_id = config.get("experiment_id")
    if not experiment_id:
        msg = "experiment_id required for experiment_report report"
        raise ValueError(msg)

    try:
        experiment = _owned(Experiment.objects.all(), user).get(id=experiment_id)
    except (Experiment.DoesNotExist, ValidationError, ValueError) as e:
        msg = f"Experiment not found: {experiment_id}"
        raise ValueError(msg) from e

    rules = _rules(config)
    if "exclude_truncated" not in config:
        rules["exclude_truncated"] = True
    experiment_jobs = GenerationJob.objects.filter(experiment=experiment)
    completed_ids = _completed_ids(experiment_jobs)
    selection = select_measured(completed_ids, plan_hash=config.get("plan_hash") or None, rules=rules)

    conditions = list(experiment.conditions.select_related("model", "profile", "harness_preset"))
    rows = []
    for condition in conditions:
        jobs = experiment_jobs.filter(condition=condition)
        rows.append(
            {
                "condition_id": condition.id,
                # Agent conditions may have no profile: the harness preset names the method.
                "label": condition.label or f"{condition.model.model_id} / {condition.method_label()}",
                "model_id": condition.model.model_id,
                "model_name": condition.model.model_name,
                "bundle_key": condition.method_ref(),
                **_group_row(jobs, selection, rules),
            },
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "experiment": {
            "id": str(experiment.id),
            "name": experiment.name,
            "slug": experiment.slug,
            "hypothesis": experiment.hypothesis,
            "status": experiment.status,
            "repeats": experiment.repeats,
        },
        "conditions": rows,
        "total_conditions": len(rows),
        "comparisons": _pairwise_condition_deltas(rows),
        "analysis_coverage": _coverage(selection, completed_ids),
        "provenance": _provenance(selection, completed_ids),
    }


def generate_comprehensive(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    """Platform-wide aggregate combining all report dimensions."""

    from backend.rankings.services import build_rankings
    from backend.rankings.services import compute_weight_sensitivity

    days = int(config.get("days", 30))
    jobs = _owned(GenerationJob.objects.all(), user)
    runs = _owned(AnalysisRun.objects.exclude(purpose=AnalysisRun.Purpose.CANARY), user)
    # Descriptive totals over each job's representative complete measurement
    # (any plan; a narrower re-run never hides the full static measurement)
    # and its latest AI review: counts, not a comparison.
    described = descriptive_runs(_completed_ids(jobs))
    latest = described.runs
    static = list(described.static.values())
    reviewed = list(described.ai.values())
    rankings = build_rankings(user if _is_scoped(user) else None)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": timezone.now().isoformat(),
        "generation_analytics": generate_generation_analytics({"days": days}, user=user),
        "tool_analysis": generate_tool_analysis({}, user=user),
        "rankings_sensitivity": {
            **compute_weight_sensitivity(rankings["rows"]),
            "comparability": rankings["comparability"],
        },
        "platform": {
            "total_models": LLMModel.objects.count(),
            "total_jobs": jobs.count(),
            "total_tasks": runs.count(),
            "measured_jobs": len(latest),
            "static_measured_jobs": len(static),
            "ai_reviewed_jobs": len(reviewed),
            "plans_in_use": len({r.plan_hash for r in latest.values()} | {r.plan_hash for r in reviewed}),
            "total_findings": sum(r.findings_total() for r in static) if static else None,
            "total_issues": sum(r.issues_total() for r in static) if static else None,
            "total_ai_findings": sum(r.ai_total() for r in reviewed) if reviewed else None,
        },
    }


GENERATORS = {
    "model_analysis": generate_model_analysis,
    "template_comparison": generate_template_comparison,
    "tool_analysis": generate_tool_analysis,
    "generation_analytics": generate_generation_analytics,
    "comprehensive": generate_comprehensive,
    "experiment_report": generate_experiment_report,
}
