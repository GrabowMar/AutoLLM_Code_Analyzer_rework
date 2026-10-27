"""Per-model research data for the model page.

Everything this platform measured about one model for one user — generation
outcomes, resource use, conformance results, analysis findings, the model's
place in the rankings — plus where its catalog attributes and external
benchmark scores sit relative to the rest of the catalog.

Local numbers are scoped to the requesting user's jobs (the same scope as
the jobs list and /statistics); catalog percentiles are global.
"""

from __future__ import annotations

import statistics as pystats
from bisect import bisect_left
from bisect import bisect_right
from collections import Counter
from typing import TYPE_CHECKING
from typing import Any

from django.core.cache import cache
from django.db.models import Count
from django.db.models import Min

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.rankings.models import BenchmarkResult
from backend.statistics.services.inference import wilson_interval

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

SEVERITIES = ("critical", "high", "medium", "low", "info")
RANKINGS_CACHE_SECONDS = 60
CATALOG_CACHE_SECONDS = 600
TOP_RULES = 10
TOP_TEMPLATES = 12

# Artificial Analysis indices OpenRouter embeds in the catalog payload.
AA_INDICES = ("intelligence_index", "coding_index", "agentic_index")


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _mean(values: list[float]) -> float | None:
    return round(pystats.fmean(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(pystats.median(values), 4) if values else None


def _rate(successes: int, n: int) -> dict[str, Any]:
    ci = wilson_interval(successes, n) if n else None
    return {
        "successes": successes,
        "n": n,
        "rate": round(successes / n, 4) if n else None,
        "ci_low": _round(ci[0]) if ci else None,
        "ci_high": _round(ci[1]) if ci else None,
    }


def _percentile_rank(sorted_values: list[float], value: float | None) -> float | None:
    """Share of the catalog strictly below ``value`` plus half the ties, 0..1."""
    if value is None or not sorted_values:
        return None
    below = bisect_left(sorted_values, value)
    ties = bisect_right(sorted_values, value) - below
    return round((below + ties / 2) / len(sorted_values), 4)


# ── Generation ──────────────────────────────────────────────────────────────


def _num(metrics: dict, key: str) -> float | None:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _generation_stats(jobs) -> dict[str, Any]:
    total = jobs.count()
    by_status = dict(jobs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
    by_mode = dict(jobs.values_list("mode").annotate(c=Count("id")).values_list("mode", "c"))
    completed = int(by_status.get(GenerationJob.Status.COMPLETED, 0))
    finished = completed + int(by_status.get(GenerationJob.Status.FAILED, 0))

    series: dict[str, list[float]] = {
        k: []
        for k in (
            "cost",
            "duration",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cached_tokens",
            "total_tokens",
            "llm_calls",
            "lines_of_code",
            "pass_rate",
            "output_tps",
        )
    }
    engines: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    terminations: Counter[str] = Counter()
    functional_status: Counter[str] = Counter()
    functional_passed = functional_evaluated = 0
    repair_used = continuations = 0
    total_cost = 0.0

    rows = jobs.filter(status=GenerationJob.Status.COMPLETED).values_list("metrics", "duration_seconds")
    for metrics, duration in rows:
        if not isinstance(metrics, dict):
            continue
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cached_tokens",
            "total_tokens",
            "llm_calls",
            "lines_of_code",
        ):
            if (v := _num(metrics, key)) is not None:
                series[key].append(v)
        if (cost := _num(metrics, "cost")) is not None:
            series["cost"].append(cost)
            total_cost += cost
        dur = duration if duration else _num(metrics, "total_duration")
        if dur:
            series["duration"].append(float(dur))
            if (out := _num(metrics, "completion_tokens")) is not None:
                series["output_tps"].append(out / float(dur))
        if metrics.get("engine"):
            engines[str(metrics["engine"]).split("@", 1)[0]] += 1
        for provider, n in (metrics.get("providers") or {}).items():
            if isinstance(n, (int, float)):
                providers[str(provider)] += int(n)
        if metrics.get("termination"):
            terminations[str(metrics["termination"])] += 1
        repair_used += bool(metrics.get("repair_used"))
        continuations += int(metrics.get("continuations_used") or 0)
        functional = metrics.get("functional")
        if isinstance(functional, dict) and functional:
            functional_evaluated += 1
            functional_passed += bool(functional.get("passed"))
            functional_status[
                str(functional.get("status") or ("passed" if functional.get("passed") else "failed"))
            ] += 1
            if isinstance(functional.get("pass_rate"), (int, float)):
                series["pass_rate"].append(float(functional["pass_rate"]))

    prompt_sum = sum(series["prompt_tokens"])
    loc_sum = sum(series["lines_of_code"])

    return {
        "jobs_total": total,
        "by_status": by_status,
        "by_mode": by_mode,
        "completed": completed,
        "success": _rate(completed, finished),
        "functional": {
            **_rate(functional_passed, functional_evaluated),
            "mean_pass_rate": _mean(series["pass_rate"]),
            "by_status": dict(functional_status),
        },
        "resources": {
            "total_cost": round(total_cost, 4),
            "mean_cost": _mean(series["cost"]),
            "cost_per_kloc": round(total_cost / loc_sum * 1000, 4) if loc_sum and series["cost"] else None,
            "mean_duration": _mean(series["duration"]),
            "median_duration": _median(series["duration"]),
            "mean_prompt_tokens": _mean(series["prompt_tokens"]),
            "mean_completion_tokens": _mean(series["completion_tokens"]),
            "mean_reasoning_tokens": _mean(series["reasoning_tokens"]),
            "mean_total_tokens": _mean(series["total_tokens"]),
            "cache_hit_ratio": round(sum(series["cached_tokens"]) / prompt_sum, 4)
            if prompt_sum and series["cached_tokens"]
            else None,
            "mean_llm_calls": _mean(series["llm_calls"]),
            "mean_lines_of_code": _mean(series["lines_of_code"]),
            "median_output_tps": _median(series["output_tps"]),
        },
        "repair_rate": round(repair_used / completed, 4) if completed else None,
        "continuations_used": continuations,
        "engines": dict(engines.most_common()),
        "providers": dict(providers.most_common()),
        "terminations": dict(terminations.most_common()),
        "first_job_at": jobs.order_by("created_at").values_list("created_at", flat=True).first(),
        "last_job_at": jobs.order_by("-created_at").values_list("created_at", flat=True).first(),
    }


def _template_breakdown(jobs) -> list[dict[str, Any]]:
    rows = (
        jobs.exclude(app_requirement__isnull=True)
        .values("app_requirement__slug", "app_requirement__name")
        .annotate(n=Count("id"))
        .order_by("-n")[:TOP_TEMPLATES]
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        scoped = jobs.filter(app_requirement__slug=row["app_requirement__slug"])
        completed = passed = evaluated = 0
        for status, metrics in scoped.values_list("status", "metrics"):
            completed += status == GenerationJob.Status.COMPLETED
            functional = (metrics or {}).get("functional") if isinstance(metrics, dict) else None
            if status == GenerationJob.Status.COMPLETED and isinstance(functional, dict) and functional:
                evaluated += 1
                passed += bool(functional.get("passed"))
        out.append(
            {
                "slug": row["app_requirement__slug"],
                "name": row["app_requirement__name"],
                "jobs": int(row["n"]),
                "completed": completed,
                "functional": _rate(passed, evaluated),
            },
        )
    return out


def _experiments(jobs) -> list[dict[str, Any]]:
    rows = (
        jobs.exclude(experiment__isnull=True)
        .values("experiment_id", "experiment__name", "experiment__status", "experiment__created_at")
        .annotate(n=Count("id"))
        .order_by("-experiment__created_at")[:20]
    )
    return [
        {
            "id": str(r["experiment_id"]),
            "name": r["experiment__name"],
            "status": r["experiment__status"],
            "jobs": int(r["n"]),
            "created_at": r["experiment__created_at"],
        }
        for r in rows
    ]


# ── Analysis ────────────────────────────────────────────────────────────────


def _analysis_stats(jobs) -> dict[str, Any]:
    job_ids = list(jobs.values_list("id", flat=True))
    # Latest result per (job, tool): a partial re-analysis keeps the other tools' findings.
    latest = ToolResult.latest_ids_per_job(job_ids) if job_ids else []
    findings = Finding.objects.filter(result_id__in=latest)
    ai_slugs = AnalyzerTool.ai_slugs()

    static = dict.fromkeys(SEVERITIES, 0)
    ai = dict.fromkeys(SEVERITIES, 0)
    by_tool: dict[str, dict[str, Any]] = {}
    for row in findings.values("result__tool_slug", "severity").annotate(c=Count("id")):
        tool, sev, c = row["result__tool_slug"], row["severity"], int(row["c"])
        target = ai if tool in ai_slugs else static
        if sev in target:
            target[sev] += c
        entry = by_tool.setdefault(
            tool,
            {"tool": tool, "ai": tool in ai_slugs, "total": 0, **dict.fromkeys(SEVERITIES, 0)},
        )
        entry["total"] += c
        if sev in SEVERITIES:
            entry[sev] += c

    # Titles vary with the matched code; Min() picks one representative per rule.
    top_rules = [
        {
            "tool": r["result__tool_slug"],
            "rule_id": r["rule_id"],
            "title": r["title"] or r["rule_id"],
            "severity": r["severity"],
            "count": int(r["c"]),
        }
        for r in findings.exclude(rule_id="")
        .values("result__tool_slug", "rule_id", "severity")
        .annotate(c=Count("id"), title=Min("title"))
        .order_by("-c")[:TOP_RULES]
    ]

    return {
        "analyzed_jobs": ToolResult.objects.filter(id__in=latest).values("run__generation_job_id").distinct().count(),
        "findings_static": static,
        "findings_ai": ai,
        "by_tool": sorted(by_tool.values(), key=lambda t: -t["total"]),
        "top_rules": top_rules,
    }


# ── Rankings ────────────────────────────────────────────────────────────────


def _rankings_for(user: AbstractBaseUser | None) -> list[dict[str, Any]]:
    from backend.rankings.services import aggregate_rankings

    key = f"llm_models:insights:rankings:{getattr(user, 'pk', 'anon')}"
    rows = cache.get(key)
    if rows is None:
        rows = aggregate_rankings(user=user)
        cache.set(key, rows, RANKINGS_CACHE_SECONDS)
    return rows


def _position(rows: list[dict[str, Any]], model_id: str, field: str) -> dict[str, Any] | None:
    scored = sorted((r for r in rows if r.get(field) is not None), key=lambda r: -(r[field] or 0.0))
    for index, row in enumerate(scored):
        if row["model_id"] == model_id:
            return {"rank": index + 1, "of": len(scored), "score": row[field]}
    return None


def _ranking_stats(model: LLMModel, user: AbstractBaseUser | None) -> dict[str, Any] | None:
    rows = _rankings_for(user)
    row = next((r for r in rows if r["model_id"] == model.model_id), None)
    if row is None:
        return None
    exercised = [r for r in rows if r.get("apps")]
    return {
        "scores": {
            key: row.get(key)
            for key in (
                "composite_score",
                "mss_score",
                "empirical_quality_score",
                "benchmark_score",
                "cost_efficiency_score",
                "accessibility_score",
                "adoption_score",
            )
        },
        "positions": {
            "composite": _position(rows, model.model_id, "composite_score"),
            "mss": _position(rows, model.model_id, "mss_score"),
            "empirical": _position(exercised, model.model_id, "empirical_quality_score"),
            "benchmark": _position(
                [r for r in rows if (r.get("benchmark_score") or 0) > 0],
                model.model_id,
                "benchmark_score",
            ),
        },
        "variance": row.get("variance"),
        "local_loc": row.get("local_loc", 0),
        "n_trials": row.get("n_trials", 0),
        "models_exercised": len(exercised),
    }


# ── Catalog context & external benchmarks ───────────────────────────────────


def _catalog_distributions() -> dict[str, list[float]]:
    key = "llm_models:insights:catalog:v1"
    cached = cache.get(key)
    if cached is not None:
        return cached
    dists: dict[str, list[float]] = {
        "input_price": [],
        "output_price": [],
        "context_window": [],
        "max_output_tokens": [],
        **{f"aa_{k}": [] for k in AA_INDICES},
    }
    rows = LLMModel.objects.filter(endpoint__isnull=True).values_list(
        "input_price_per_token",
        "output_price_per_token",
        "context_window",
        "max_output_tokens",
        "is_free",
        "capabilities_json",
    )
    for inp, out, ctx, max_out, is_free, payload in rows:
        if not is_free and inp > 0:
            dists["input_price"].append(inp * 1_000_000)
        if not is_free and out > 0:
            dists["output_price"].append(out * 1_000_000)
        if ctx > 0:
            dists["context_window"].append(float(ctx))
        if max_out > 0:
            dists["max_output_tokens"].append(float(max_out))
        aa = ((payload or {}).get("benchmarks") or {}).get("artificial_analysis") or {}
        for k in AA_INDICES:
            if isinstance(aa.get(k), (int, float)):
                dists[f"aa_{k}"].append(float(aa[k]))
    for values in dists.values():
        values.sort()
    cache.set(key, dists, CATALOG_CACHE_SECONDS)
    return dists


def _catalog_context(model: LLMModel) -> dict[str, Any]:
    dists = _catalog_distributions()
    paid = not model.is_free

    def entry(name: str, value: float | None) -> dict[str, Any]:
        values = dists[name]
        return {
            "value": value,
            "percentile": _percentile_rank(values, value),
            "n": len(values),
            "median": _median(values),
        }

    aa = ((model.capabilities_json or {}).get("benchmarks") or {}).get("artificial_analysis") or {}
    provider_models = LLMModel.objects.filter(endpoint__isnull=True, provider=model.provider).count()
    return {
        "input_price": entry("input_price", model.input_price_per_token * 1_000_000 if paid else None),
        "output_price": entry("output_price", model.output_price_per_token * 1_000_000 if paid else None),
        "context_window": entry("context_window", float(model.context_window) or None),
        "max_output_tokens": entry("max_output_tokens", float(model.max_output_tokens) or None),
        "artificial_analysis": {
            k: entry(f"aa_{k}", float(aa[k]) if isinstance(aa.get(k), (int, float)) else None) for k in AA_INDICES
        },
        "catalog_size": LLMModel.objects.filter(endpoint__isnull=True).count(),
        "provider_models": provider_models,
    }


def _local_benchmarks(model: LLMModel) -> list[dict[str, Any]]:
    rows = BenchmarkResult.objects.filter(model_id=model.model_id).order_by("benchmark")
    out: list[dict[str, Any]] = []
    for row in rows:
        others = sorted(BenchmarkResult.objects.filter(benchmark=row.benchmark).values_list("score", flat=True))
        out.append(
            {
                "benchmark": row.benchmark,
                "score": row.score,
                "source": row.source,
                "fetched_at": row.fetched_at,
                "percentile": _percentile_rank(others, row.score),
                "n": len(others),
            },
        )
    return out


# ── Entry point ─────────────────────────────────────────────────────────────


def model_insights(model: LLMModel, user: AbstractBaseUser | None) -> dict[str, Any]:
    jobs = GenerationJob.objects.filter(model=model)
    if user is not None and getattr(user, "is_authenticated", False):
        jobs = jobs.filter(created_by=user)
    return {
        "generation": _generation_stats(jobs),
        "templates": _template_breakdown(jobs),
        "experiments": _experiments(jobs),
        "analysis": _analysis_stats(jobs),
        "ranking": _ranking_stats(model, user),
        "catalog": _catalog_context(model),
        "benchmarks": _local_benchmarks(model),
    }
