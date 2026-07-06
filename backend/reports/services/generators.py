"""Report generators — produce JSON content for each report type.

Adapted from the legacy `app/services/reports/*` modules but driven by the
new Django models (LLMModel, GenerationJob, AnalysisTask, Finding) instead
of filesystem-stored applications.
"""

from __future__ import annotations

import statistics
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.generation.models import AppRequirementTemplate
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel

from .loc import loc_for_jobs

# Findings weighted by how much they matter for cross-model comparisons; raw
# counts let a flood of style nits outweigh one exploitable hole.
SEVERITY_WEIGHTS = {"critical": 10, "high": 5, "medium": 2, "low": 1, "info": 0}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _severity_counts(findings_qs) -> dict[str, int]:
    out = dict.fromkeys(("critical", "high", "medium", "low", "info"), 0)
    for row in findings_qs.values("severity").annotate(n=Count("id")):
        sev = (row["severity"] or "").lower()
        if sev in out:
            out[sev] = row["n"]
    return out


def _job_summary(jobs_qs) -> dict[str, Any]:
    agg = jobs_qs.aggregate(
        total=Count("id"),
        avg_duration=Avg("duration_seconds"),
    )
    by_status = {row["status"]: row["n"] for row in jobs_qs.values("status").annotate(n=Count("id"))}
    completed = by_status.get(GenerationJob.Status.COMPLETED, 0)
    total = agg["total"] or 0
    truncated = sum(
        1
        for data in jobs_qs.values_list("result_data", flat=True)
        if isinstance(data, dict) and any(data.get(f"{part}_truncated") for part in ("backend", "frontend"))
    )
    total_cost = sum(
        float(m["cost"]) for m in jobs_qs.values_list("metrics", flat=True) if isinstance(m, dict) and m.get("cost")
    )
    return {
        "total_jobs": total,
        "completed_jobs": completed,
        "failed_jobs": by_status.get(GenerationJob.Status.FAILED, 0),
        "success_rate": (completed / total) if total else 0.0,
        "avg_duration": float(agg["avg_duration"] or 0.0),
        "by_status": by_status,
        # Jobs whose generated code was cut off at max_tokens — their findings
        # describe incomplete apps.
        "truncated_jobs": truncated,
        "total_cost": round(total_cost, 6),
    }


def _latest_run_ids(jobs_qs) -> list:
    """Latest finished run per job — see AnalysisRun.latest_ids_per_job."""
    return AnalysisRun.latest_ids_per_job(jobs_qs.values_list("id", flat=True))


def _stat(values: list[float]) -> dict[str, float]:
    """Mean and sample stdev; stdev is 0.0 below n=2."""
    if not values:
        return {"mean": 0.0, "stdev": 0.0, "n": 0}
    return {
        "mean": round(statistics.mean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "n": len(values),
    }


def _is_truncated_job(job: GenerationJob) -> bool:
    data = job.result_data if isinstance(job.result_data, dict) else {}
    return any(data.get(f"{part}_truncated") for part in ("backend", "frontend"))


def _experiment_stats(jobs_qs, *, exclude_truncated: bool = True) -> dict[str, Any]:
    """Trial-level statistics for one model's jobs on one template.

    Each completed job is one independent trial; its findings come from its
    latest finished analysis run. Truncated generations measure the token
    ceiling rather than the model, so they are excluded by default (but
    reported), and stdev across trials is what makes single-run flukes —
    e.g. an AI reviewer choosing to report 6 vs 21 findings — visible.
    """
    completed = [j for j in jobs_qs if j.status == GenerationJob.Status.COMPLETED]
    truncated = [j for j in completed if _is_truncated_job(j)]
    trials = [j for j in completed if not (exclude_truncated and _is_truncated_job(j))]
    trial_ids = [j.id for j in trials]

    run_ids = AnalysisRun.latest_ids_per_job(trial_ids)
    run_to_job = dict(
        AnalysisRun.objects.filter(id__in=run_ids).values_list("id", "generation_job_id"),
    )
    sev_by_job: dict[Any, dict[str, int]] = {}
    for row in (
        Finding.objects.filter(result__run_id__in=run_ids).values("result__run_id", "severity").annotate(n=Count("id"))
    ):
        job_id = run_to_job.get(row["result__run_id"])
        sev_by_job.setdefault(job_id, {})[(row["severity"] or "").lower()] = row["n"]

    loc_by_job = {
        entry["job_id"]: entry["total_loc"]
        for entry in loc_for_jobs(GenerationJob.objects.filter(id__in=trial_ids)).get("per_job", [])
    }

    analyzed_ids = set(run_to_job.values())
    totals, crit_high, weighted, per_kloc = [], [], [], []
    costs, durations, tokens = [], [], []
    functional_rates, functional_passed = [], 0

    for job in trials:
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        if metrics.get("cost"):
            costs.append(float(metrics["cost"]))
        duration = metrics.get("total_duration") or metrics.get("duration_seconds")
        if duration:
            durations.append(float(duration))
        if metrics.get("total_tokens"):
            tokens.append(float(metrics["total_tokens"]))
        functional = metrics.get("functional")
        if isinstance(functional, dict) and "pass_rate" in functional:
            functional_rates.append(float(functional["pass_rate"]))
            functional_passed += 1 if functional.get("passed") else 0

        if job.id not in analyzed_ids:
            continue
        sev = sev_by_job.get(str(job.id)) or sev_by_job.get(job.id) or {}
        total = sum(sev.values())
        score = float(sum(SEVERITY_WEIGHTS.get(s, 0) * n for s, n in sev.items()))
        totals.append(float(total))
        crit_high.append(float(sev.get("critical", 0) + sev.get("high", 0)))
        weighted.append(score)
        loc = loc_by_job.get(str(job.id)) or loc_by_job.get(job.id) or 0
        if loc:
            per_kloc.append(round(score / loc * 1000, 4))

    return {
        "trials": len(trials),
        "analyzed": len(analyzed_ids),
        "truncated_excluded": len(truncated) if exclude_truncated else 0,
        "findings_total": _stat(totals),
        "critical_high": _stat(crit_high),
        "weighted_score": _stat(weighted),
        "weighted_per_kloc": _stat(per_kloc),
        "cost": {**_stat(costs), "total": round(sum(costs), 6)},
        "duration": _stat(durations),
        "tokens": _stat(tokens),
        "functional": {
            "jobs_probed": len(functional_rates),
            "jobs_passed": functional_passed,
            "pass_rate": _stat(functional_rates),
        },
    }


def _findings_for_jobs(jobs_qs):
    return Finding.objects.filter(
        result__run_id__in=_latest_run_ids(jobs_qs),
    )


def _tools_for_jobs(jobs_qs) -> list[dict[str, Any]]:
    """Per-tool breakdown derived from AnalysisResult rows."""

    from backend.analysis.models import ToolResult

    rows = (
        ToolResult.objects.filter(run_id__in=_latest_run_ids(jobs_qs))
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


def generate_model_analysis(config: dict[str, Any]) -> dict[str, Any]:
    """Aggregate across one model's generation jobs + their analyses."""

    model_id = config.get("model_id") or config.get("model_slug")
    if not model_id:
        msg = "model_id required for model_analysis report"
        raise ValueError(msg)

    try:
        model = LLMModel.objects.get(model_id=model_id)
    except LLMModel.DoesNotExist as e:
        msg = f"Model not found: {model_id}"
        raise ValueError(msg) from e

    jobs = GenerationJob.objects.filter(model=model).select_related("model")
    findings = _findings_for_jobs(jobs)

    from backend.analysis.models import ToolResult

    tool_results = ToolResult.objects.filter(run_id__in=_latest_run_ids(jobs))

    return {
        "model": {
            "model_id": model.model_id,
            "model_name": model.model_name,
            "provider": model.provider,
        },
        "generation": _job_summary(jobs),
        "loc": loc_for_jobs(jobs),
        "findings": _severity_counts(findings),
        "total_findings": findings.count(),
        "tools": _tools_for_jobs(jobs),
        "metrics_by_tool": _aggregate_tool_metrics(tool_results),
    }


def generate_template_comparison(config: dict[str, Any]) -> dict[str, Any]:
    """Compare models on a single application requirement template."""

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
    jobs = GenerationJob.objects.filter(app_requirement=template)
    if filter_models:
        jobs = jobs.filter(model__model_id__in=filter_models)

    by_model: dict[str, dict[str, Any]] = {}
    for job in jobs.select_related("model"):
        model = job.model
        key = model.model_id if model else "unknown"
        bucket = by_model.setdefault(
            key,
            {
                "model_id": key,
                "model_name": getattr(model, "model_name", key),
                "provider": getattr(model, "provider", ""),
                "jobs": [],
            },
        )
        bucket["jobs"].append(job)

    exclude_truncated = bool(config.get("exclude_truncated", True))
    rows = []
    for key, bucket in by_model.items():
        sub_jobs = GenerationJob.objects.filter(
            id__in=[j.id for j in bucket["jobs"]],
        )
        findings = _findings_for_jobs(sub_jobs)
        rows.append(
            {
                "model_id": key,
                "model_name": bucket["model_name"],
                "provider": bucket["provider"],
                "generation": _job_summary(sub_jobs),
                "loc": loc_for_jobs(sub_jobs),
                "findings": _severity_counts(findings),
                "total_findings": findings.count(),
                "stats": _experiment_stats(sub_jobs, exclude_truncated=exclude_truncated),
            },
        )

    rows.sort(
        key=lambda r: r["generation"]["success_rate"],
        reverse=True,
    )

    return {
        "template": {
            "slug": template.slug,
            "name": template.name,
            "category": template.category,
        },
        "models": rows,
        "total_models": len(rows),
    }


def generate_tool_analysis(config: dict[str, Any]) -> dict[str, Any]:
    """Effectiveness analysis of one (or all) analyzer(s).

    Deliberately spans every run (not latest-per-job): this report measures
    tool behavior across executions, not the state of any one codebase.
    """

    tool_name = config.get("tool_name")
    tasks = AnalysisRun.objects.all()
    if config.get("filter_model"):
        tasks = tasks.filter(generation_job__model__model_id=config["filter_model"])

    findings_qs = Finding.objects.filter(result__run__in=tasks)
    if tool_name:
        findings_qs = findings_qs.filter(result__tool_slug=tool_name)

    by_tool: dict[str, dict[str, Any]] = {}
    for row in findings_qs.values("result__tool_slug", "severity").annotate(
        n=Count("id"),
    ):
        analyzer = row["result__tool_slug"]
        sev = (row["severity"] or "").lower()
        bucket = by_tool.setdefault(
            analyzer,
            {
                "analyzer": analyzer,
                "total": 0,
                "by_severity": {
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "info": 0,
                },
            },
        )
        bucket["total"] += row["n"]
        if sev in bucket["by_severity"]:
            bucket["by_severity"][sev] += row["n"]

    from backend.analysis.models import ToolResult

    tool_results = ToolResult.objects.filter(run__in=tasks)
    if tool_name:
        tool_results = tool_results.filter(tool_slug=tool_name)

    return {
        "tool_filter": tool_name,
        "tools": sorted(by_tool.values(), key=lambda r: r["total"], reverse=True),
        "metrics_by_tool": _aggregate_tool_metrics(tool_results),
        "total_findings": sum(t["total"] for t in by_tool.values()),
        "total_tasks": tasks.count(),
    }


def generate_generation_analytics(config: dict[str, Any]) -> dict[str, Any]:
    """Generation success/failure patterns over a configurable window."""

    days = int(config.get("days", 7))
    since = timezone.now() - timezone.timedelta(days=days)
    jobs = GenerationJob.objects.filter(created_at__gte=since)

    by_model = []
    for row in (
        jobs.values("model__model_id", "model__model_name")
        .annotate(
            n=Count("id"),
            avg_duration=Avg("duration_seconds"),
        )
        .order_by("-n")
    ):
        completed = jobs.filter(
            model__model_id=row["model__model_id"],
            status=GenerationJob.Status.COMPLETED,
        ).count()
        by_model.append(
            {
                "model_id": row["model__model_id"],
                "model_name": row["model__model_name"],
                "jobs": row["n"],
                "completed": completed,
                "success_rate": (completed / row["n"]) if row["n"] else 0.0,
                "avg_duration": float(row["avg_duration"] or 0.0),
            },
        )

    return {
        "window_days": days,
        "since": since.isoformat(),
        "summary": _job_summary(jobs),
        "loc": loc_for_jobs(jobs),
        "by_model": by_model,
    }


def generate_comprehensive(config: dict[str, Any]) -> dict[str, Any]:
    """Platform-wide aggregate combining all report dimensions."""

    days = int(config.get("days", 30))
    return {
        "generated_at": timezone.now().isoformat(),
        "generation_analytics": generate_generation_analytics({"days": days}),
        "tool_analysis": generate_tool_analysis({}),
        "platform": {
            "total_models": LLMModel.objects.count(),
            "total_jobs": GenerationJob.objects.count(),
            "total_tasks": AnalysisRun.objects.count(),
            "total_findings": Finding.objects.count(),
        },
    }


GENERATORS = {
    "model_analysis": generate_model_analysis,
    "template_comparison": generate_template_comparison,
    "tool_analysis": generate_tool_analysis,
    "generation_analytics": generate_generation_analytics,
    "comprehensive": generate_comprehensive,
}
