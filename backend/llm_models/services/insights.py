"""Per-model research data for the model page.

Everything this platform measured about one model for one user — generation
outcomes, resource use, conformance results, analysis findings, the model's
place in the rankings — plus where its catalog attributes and external
benchmark scores sit relative to the rest of the catalog.

Local numbers are scoped to the requesting user's generation runs (the same
scope as the samples list and /statistics); catalog percentiles are global.
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
from django.db.models import Max
from django.db.models import Min

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services.base import is_dependency_advisory
from backend.generation.models import GenerationRun
from backend.generation.models.choices import RunStatus
from backend.generation.runs.view import run_views
from backend.llm_models.models import LLMModel
from backend.rankings.models import BenchmarkResult
from backend.statistics.services.inference import wilson_interval

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

SEVERITIES = ("critical", "high", "medium", "low", "info")
_SEVERITY_RANK = {s: len(SEVERITIES) - i for i, s in enumerate(SEVERITIES)}
UNRATED = "unrated"
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


def _llm_calls(metrics: dict) -> float | None:
    by_class = metrics.get("requests_by_class")
    if not isinstance(by_class, dict) or not by_class:
        return None
    return float(sum(n for n in by_class.values() if isinstance(n, int) and not isinstance(n, bool)))


def _eval_summary(metrics: Any) -> dict | None:
    """``metrics["eval"]``: the mirror of the run's latest evaluation (None when never evaluated)."""
    evaluation = metrics.get("eval") if isinstance(metrics, dict) else None
    return evaluation if isinstance(evaluation, dict) else None


def _providers(run_ids: list) -> Counter[str]:
    """Calls per serving provider over every attempt of the runs (``RunAttempt.served``)."""
    from backend.generation.models import RunAttempt

    providers: Counter[str] = Counter()
    for served in RunAttempt.objects.filter(run_id__in=run_ids).values_list("served", flat=True):
        for provider, n in ((served or {}).get("providers") or {}).items():
            if isinstance(n, int) and not isinstance(n, bool):
                providers[str(provider)] += n
    return providers


def _generation_stats(runs) -> dict[str, Any]:
    total = runs.count()
    by_status = dict(runs.values_list("status").annotate(c=Count("id")).values_list("status", "c"))
    by_kind = dict(runs.values_list("kind").annotate(c=Count("id")).values_list("kind", "c"))
    by_outcome = dict(
        runs.exclude(outcome="").values_list("outcome").annotate(c=Count("id")).values_list("outcome", "c"),
    )
    completed = int(by_status.get(RunStatus.COMPLETED, 0))
    finished = completed + int(by_status.get(RunStatus.FAILED, 0))

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
    harnesses: Counter[str] = Counter()
    terminations: Counter[str] = Counter()
    functional_status: Counter[str] = Counter()
    functional_passed = functional_evaluated = 0
    total_cost = 0.0

    cost_with_loc = loc_with_cost = 0.0
    done = list(runs.filter(status=RunStatus.COMPLETED))
    for run, view in zip(done, run_views(done), strict=True):
        metrics = run.metrics if isinstance(run.metrics, dict) else {}
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "total_tokens"):
            if (v := _num(metrics, key)) is not None:
                series[key].append(v)
        if (calls := _llm_calls(metrics)) is not None:
            series["llm_calls"].append(calls)
        # Source LOC of the model's own files (tests and lockfiles excluded), as in the study
        # tables; None when no tree was collected (left out, never 0).
        loc = _num(metrics, "loc_model")
        if loc is None:
            measured_loc = view.loc("model")
            loc = float(measured_loc) if measured_loc is not None else None
        if loc:
            series["lines_of_code"].append(loc)
        if (cost := _num(metrics, "cost_usd")) is not None:
            series["cost"].append(cost)
            total_cost += cost
            if loc:
                cost_with_loc += cost
                loc_with_cost += loc
        dur = run.duration_seconds or _num(metrics, "wall_s")
        if dur:
            series["duration"].append(float(dur))
            if (out := _num(metrics, "completion_tokens")) is not None:
                series["output_tps"].append(out / float(dur))
        if view.harness is not None:
            harnesses[view.harness[0]] += 1
        termination = (run.result or {}).get("termination") if isinstance(run.result, dict) else None
        if termination:
            terminations[str(termination)] += 1
        evaluation = _eval_summary(metrics)
        if evaluation is not None:
            functional_status[str(evaluation.get("status") or "unknown")] += 1
            rate = evaluation.get("agent_pass_rate")
            # Only a measured evaluation grades the run; anything else is ungraded, not 0.
            if evaluation.get("measured") and isinstance(rate, int | float) and not isinstance(rate, bool):
                functional_evaluated += 1
                functional_passed += rate >= 1.0
                series["pass_rate"].append(float(rate))

    prompt_sum = sum(series["prompt_tokens"])

    return {
        "jobs_total": total,
        "by_status": by_status,
        # Run kinds (app_agentic, slot_single_shot, ...); the key name is kept for the model page.
        "by_mode": by_kind,
        "by_outcome": by_outcome,
        "completed": completed,
        "success": _rate(completed, finished),
        "functional": {
            # Successes = measured evaluations that passed every acceptance check.
            **_rate(functional_passed, functional_evaluated),
            "mean_pass_rate": _mean(series["pass_rate"]),
            "by_status": dict(functional_status),
        },
        "resources": {
            "total_cost": round(total_cost, 4),
            "mean_cost": _mean(series["cost"]),
            # Over the runs with both a cost and code, so numerator and denominator describe the same apps.
            "cost_per_kloc": round(cost_with_loc / loc_with_cost * 1000, 4) if loc_with_cost else None,
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
        # v3 runs have no repair rounds or continuations: not measured, never 0.
        "repair_rate": None,
        "continuations_used": None,
        "engines": dict(harnesses.most_common()),
        "providers": dict(_providers([run.id for run in done]).most_common()),
        "terminations": dict(terminations.most_common()),
        "first_job_at": runs.order_by("created_at").values_list("created_at", flat=True).first(),
        "last_job_at": runs.order_by("-created_at").values_list("created_at", flat=True).first(),
    }


def _template_breakdown(runs) -> list[dict[str, Any]]:
    """Per task: runs, completed runs and the functional rate of the measured evaluations."""
    rows = (
        runs.exclude(task_version__isnull=True)
        .values("task_version__task__slug")
        .annotate(n=Count("id"), name=Max("task_version__name"))
        .order_by("-n", "task_version__task__slug")[:TOP_TEMPLATES]
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        scoped = runs.filter(task_version__task__slug=row["task_version__task__slug"])
        completed = passed = evaluated = 0
        for status, metrics in scoped.values_list("status", "metrics"):
            completed += status == RunStatus.COMPLETED
            evaluation = _eval_summary(metrics)
            rate = evaluation.get("agent_pass_rate") if evaluation else None
            if (
                status == RunStatus.COMPLETED
                and evaluation is not None
                and evaluation.get("measured")
                and isinstance(rate, int | float)
                and not isinstance(rate, bool)
            ):
                evaluated += 1
                passed += rate >= 1.0
        out.append(
            {
                "slug": row["task_version__task__slug"],
                "name": row["name"],
                "jobs": int(row["n"]),
                "completed": completed,
                "functional": _rate(passed, evaluated),
            },
        )
    return out


def _studies(runs) -> list[dict[str, Any]]:
    rows = (
        runs.exclude(study__isnull=True)
        .values("study_id", "study__name", "study__status", "study__created_at")
        .annotate(n=Count("id"))
        .order_by("-study__created_at")[:20]
    )
    return [
        {
            "id": str(r["study_id"]),
            "name": r["study__name"],
            "status": r["study__status"],
            "jobs": int(r["n"]),
            "created_at": r["study__created_at"],
        }
        for r in rows
    ]


# ── Analysis ────────────────────────────────────────────────────────────────


def _label(tool_severity: str) -> str:
    return tool_severity or UNRATED


def _labels_text(counts: Counter[str]) -> str:
    """The tool's own labels for a rule, most frequent first ("HIGH", or "HIGH / MEDIUM" when it varies)."""
    return " / ".join(label for label, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _analysis_stats(jobs) -> dict[str, Any]:
    """Findings of the model's apps (each run's latest result per tool).

    Analysis runs on the whole app (``code_scope="app"``), so these counts
    include findings in the starter's files; model-scope attribution is the
    rankings' and the study tables' job.

    ``findings_static``/``findings_ai`` and the per-tool level columns are the
    *normalized* severity (a heuristic mapping, for aggregates only); a
    finding whose tool printed no level is counted under ``unrated`` instead
    of a level nobody gave it. ``tool_severities`` and the top rules'
    ``tool_severity`` are the labels exactly as the tools printed them.
    Dependency advisories of the known-vulnerability scanners describe
    packages (mostly the starter's pins), not the model's code: they are
    ``findings_dependency``, not part of ``findings_static``
    (:func:`~backend.analysis.services.base.is_dependency_advisory`, the one
    P2 definition; deptry/depcheck manifest findings stay code issues).
    """
    job_ids = list(jobs.values_list("id", flat=True))
    # Latest result per (job, tool): a partial re-analysis keeps the other tools' findings.
    latest = ToolResult.latest_ids_per_job(job_ids) if job_ids else []
    findings = Finding.objects.filter(result_id__in=latest)
    ai_slugs = AnalyzerTool.ai_slugs()

    static = dict.fromkeys((*SEVERITIES, UNRATED), 0)
    ai = dict.fromkeys((*SEVERITIES, UNRATED), 0)
    dependency = dict.fromkeys((*SEVERITIES, UNRATED), 0)
    by_tool: dict[str, dict[str, Any]] = {}
    for row in findings.values("result__tool_slug", "category", "severity", "tool_severity").annotate(c=Count("id")):
        tool, sev, label, c = row["result__tool_slug"], row["severity"], row["tool_severity"], int(row["c"])
        bucket = sev if label and sev in SEVERITIES else UNRATED
        if tool in ai_slugs:
            ai[bucket] += c
        elif is_dependency_advisory(tool, row["category"]):
            dependency[bucket] += c
        else:
            static[bucket] += c
        entry = by_tool.setdefault(
            tool,
            {
                "tool": tool,
                "ai": tool in ai_slugs,
                "total": 0,
                **dict.fromkeys((*SEVERITIES, UNRATED), 0),
                "tool_severities": Counter(),
            },
        )
        entry["total"] += c
        entry[bucket] += c
        entry["tool_severities"][_label(label)] += c
    for entry in by_tool.values():
        entry["tool_severities"] = dict(entry["tool_severities"].most_common())

    # One row per (tool, rule): titles vary with the matched code, so Min()
    # picks one representative, and the rule's levels are listed, not split into rows.
    rules = list(
        findings.exclude(rule_id="")
        .values("result__tool_slug", "rule_id")
        .annotate(c=Count("id"), title=Min("title"))
        .order_by("-c", "result__tool_slug", "rule_id")[:TOP_RULES],
    )
    labels: dict[tuple[str, str], Counter[str]] = {}
    normalized: dict[tuple[str, str], str] = {}
    if rules:
        for row in (
            findings.filter(
                result__tool_slug__in={r["result__tool_slug"] for r in rules},
                rule_id__in={r["rule_id"] for r in rules},
            )
            .values("result__tool_slug", "rule_id", "tool_severity", "severity")
            .annotate(c=Count("id"))
        ):
            key = (row["result__tool_slug"], row["rule_id"])
            labels.setdefault(key, Counter())[_label(row["tool_severity"])] += int(row["c"])
            if row["tool_severity"] and _SEVERITY_RANK.get(row["severity"], 0) > _SEVERITY_RANK.get(
                normalized.get(key, ""),
                0,
            ):
                normalized[key] = row["severity"]
    top_rules = []
    for r in rules:
        key = (r["result__tool_slug"], r["rule_id"])
        counts = labels.get(key, Counter())
        top_rules.append(
            {
                "tool": r["result__tool_slug"],
                "rule_id": r["rule_id"],
                "title": r["title"] or r["rule_id"],
                # As the tool printed it ("unrated" when it rates nothing).
                "tool_severity": _labels_text(counts) or UNRATED,
                "tool_severities": dict(counts.most_common()),
                # Highest normalized level among rated findings: heuristic, for sorting/statistics only.
                "normalized_severity": normalized.get(key),
                "count": int(r["c"]),
            },
        )

    return {
        "analyzed_jobs": ToolResult.objects.filter(id__in=latest).values("run__generation_job_id").distinct().count(),
        "findings_static": static,
        "findings_ai": ai,
        "findings_dependency": dependency,
        "severity_note": "Levels are normalized (heuristic) across tools; unrated = the tool printed no level.",
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
    runs = GenerationRun.objects.filter(model=model, beyond_plan=False)
    if user is not None and getattr(user, "is_authenticated", False):
        runs = runs.filter(created_by=user)
    return {
        "generation": _generation_stats(runs),
        "templates": _template_breakdown(runs),
        # The studies this model ran in (key name kept for the model page).
        "experiments": _studies(runs),
        "analysis": _analysis_stats(runs),
        "ranking": _ranking_stats(model, user),
        "catalog": _catalog_context(model),
        "benchmarks": _local_benchmarks(model),
    }
