"""Build the rankings list from LLMModel + benchmarks + local stats."""

from __future__ import annotations

import statistics as pystats
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q

from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.rankings.models import BenchmarkResult
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.scoring import compute_accessibility_score
from backend.rankings.services.scoring import compute_adoption_score
from backend.rankings.services.scoring import compute_benchmark_score
from backend.rankings.services.scoring import compute_composite
from backend.rankings.services.scoring import compute_cost_efficiency_score
from backend.rankings.services.scoring import compute_empirical_quality
from backend.rankings.services.scoring import compute_mss

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

_ZERO_ROLLUP = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(pystats.stdev(values), 4)


def _local_app_stats(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate per-model apps generated and finding rollups.

    Only findings from deterministic (container) tools feed the ``findings``
    rollup that the empirical score is computed from; AI-reviewer findings
    are collected separately under ``ai_findings``. Findings are grouped per
    job (rows grow as models x jobs x severities — fine at this scale) so
    per-trial density variance can be reported alongside the totals.

    ``prompt_hash``/``bundle_key`` narrow the comparison to jobs generated
    from the same prompt material — without this, jobs from different
    template/bundle versions on the same app are silently pooled together.
    ``experiment_id`` narrows to one designed experiment's jobs directly,
    without needing to know its prompt_hash/bundle_key in advance.
    """

    jobs = GenerationJob.objects.exclude(model__isnull=True)
    if user is not None and getattr(user, "is_authenticated", False):
        jobs = jobs.filter(created_by=user)
    if prompt_hash:
        jobs = jobs.filter(prompt_hash=prompt_hash)
    if bundle_key:
        jobs = jobs.filter(bundle_key=bundle_key)
    if experiment_id:
        jobs = jobs.filter(experiment_id=experiment_id)

    by_model = jobs.values("model__model_id").annotate(
        apps=Count("id"),
        apps_completed=Count("id", filter=Q(status="completed")),
        avg_duration=Avg(
            "duration_seconds",
            filter=Q(status="completed"),
        ),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in by_model:
        out[row["model__model_id"]] = {
            "apps": int(row["apps"]),
            "apps_completed": int(row["apps_completed"]),
            "avg_duration": (round(row["avg_duration"], 1) if row["avg_duration"] else 0.0),
            "findings": dict(_ZERO_ROLLUP),
            "ai_findings": dict(_ZERO_ROLLUP),
            "local_loc": 0,
            "functional_pass_rate": None,
        }

    # LOC + smoke results live in job metrics; roll both up so the empirical
    # quality score can normalize findings by code volume, and keep per-job
    # values for the variance stats.
    functional_rates: dict[str, list[float]] = {}
    job_loc: dict[Any, int] = {}
    job_model: dict[Any, str] = {}
    for job_id, model_id, metrics in jobs.filter(status="completed").values_list(
        "id",
        "model__model_id",
        "metrics",
    ):
        if model_id not in out or not isinstance(metrics, dict):
            continue
        loc = int(metrics.get("lines_of_code") or 0)
        out[model_id]["local_loc"] += loc
        job_loc[job_id] = loc
        job_model[job_id] = model_id
        functional = metrics.get("functional")
        if isinstance(functional, dict) and "pass_rate" in functional:
            functional_rates.setdefault(model_id, []).append(float(functional["pass_rate"]))
    for model_id, rates in functional_rates.items():
        out[model_id]["functional_pass_rate"] = round(sum(rates) / len(rates), 4)

    # Only each job's latest finished run — otherwise re-analyzing a job
    # double-counts its findings in the rollup.
    from backend.analysis.models import AnalysisRun

    latest_run_ids = AnalysisRun.latest_ids_per_job()
    base = Finding.objects.filter(result__run_id__in=latest_run_ids).exclude(
        result__run__generation_job__model__model_id__isnull=True,
    )
    if user is not None and getattr(user, "is_authenticated", False):
        base = base.filter(result__run__generation_job__created_by=user)
    if prompt_hash:
        base = base.filter(result__run__generation_job__prompt_hash=prompt_hash)
    if bundle_key:
        base = base.filter(result__run__generation_job__bundle_key=bundle_key)
    if experiment_id:
        base = base.filter(result__run__generation_job__experiment_id=experiment_id)

    ai_slugs = AnalyzerTool.ai_slugs()

    # Deterministic tools only, grouped per job for the variance stats.
    job_severities: dict[Any, dict[str, int]] = {}
    static_rows = (
        base.exclude(result__tool_slug__in=ai_slugs)
        .values(
            "result__run__generation_job__model__model_id",
            "result__run__generation_job_id",
            "severity",
        )
        .annotate(c=Count("id"))
    )
    for r in static_rows:
        mid = r["result__run__generation_job__model__model_id"]
        if mid in out:
            out[mid]["findings"][r["severity"]] += int(r["c"])
            job_severities.setdefault(r["result__run__generation_job_id"], {})[r["severity"]] = int(r["c"])

    ai_rows = (
        base.filter(result__tool_slug__in=ai_slugs)
        .values("result__run__generation_job__model__model_id", "severity")
        .annotate(c=Count("id"))
    )
    for r in ai_rows:
        mid = r["result__run__generation_job__model__model_id"]
        if mid in out:
            out[mid]["ai_findings"][r["severity"]] = int(r["c"])

    # Per-trial variance over analyzed jobs: weighted findings density per
    # KLOC and smoke pass rate. Jobs never analyzed are excluded — a missing
    # analysis is not a clean bill of health.
    analyzed_job_ids = set(
        AnalysisRun.objects.filter(id__in=latest_run_ids).values_list(
            "generation_job_id",
            flat=True,
        ),
    )
    densities: dict[str, list[float]] = {}
    for job_id in analyzed_job_ids:
        model_id = job_model.get(job_id)
        loc = job_loc.get(job_id, 0)
        if model_id is None or loc <= 0:
            continue
        weighted = sum(SEVERITY_WEIGHTS.get(sev, 0) * count for sev, count in job_severities.get(job_id, {}).items())
        densities.setdefault(model_id, []).append(weighted / loc * 1000)

    for model_id, stats in out.items():
        model_densities = densities.get(model_id, [])
        stats["variance"] = {
            "n_jobs": len(model_densities),
            "density_per_kloc_mean": (round(pystats.mean(model_densities), 4) if model_densities else None),
            "density_per_kloc_stdev": _stdev(model_densities),
            "smoke_pass_rate_stdev": _stdev(functional_rates.get(model_id, [])),
        }

    return out


def _benchmarks_by_model() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in BenchmarkResult.objects.all().values("model_id", "benchmark", "score"):
        out.setdefault(r["model_id"], {})[r["benchmark"]] = float(r["score"])
    return out


def aggregate_rankings(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build the ranking list from LLMModel + benchmarks + local stats.

    With ``user`` given, local stats (apps, findings, empirical scores) are
    scoped to that user's generation jobs; benchmarks and metadata are global
    either way. ``prompt_hash``/``bundle_key``/``experiment_id`` further narrow
    local stats to jobs generated from one specific prompt version or designed
    experiment — comparisons across models are only apples-to-apples when they
    used the same prompt material.
    """

    local = _local_app_stats(user, prompt_hash=prompt_hash, bundle_key=bundle_key, experiment_id=experiment_id)
    bench = _benchmarks_by_model()

    rows: list[dict[str, Any]] = []
    for m in LLMModel.objects.all():
        local_row = local.get(m.model_id, {})
        bench_row = bench.get(m.model_id, {})

        entry: dict[str, Any] = {
            "model_id": m.model_id,
            "model_name": m.model_name,
            "provider": m.provider,
            "is_free": m.is_free,
            "context_length": m.context_window,
            "price_per_million_input": (m.input_price_per_token * 1_000_000 if m.input_price_per_token else None),
            "price_per_million_output": (m.output_price_per_token * 1_000_000 if m.output_price_per_token else None),
            "apps": local_row.get("apps", 0),
            "apps_completed": local_row.get("apps_completed", 0),
            "avg_duration": local_row.get("avg_duration", 0.0),
            "findings": local_row.get("findings", dict(_ZERO_ROLLUP)),
            "ai_findings": local_row.get("ai_findings", dict(_ZERO_ROLLUP)),
            "local_loc": local_row.get("local_loc", 0),
            "functional_pass_rate": local_row.get("functional_pass_rate"),
            "n_trials": local_row.get("apps_completed", 0),
            "variance": local_row.get("variance"),
        }
        # Hoist benchmark scores into entry for normalization
        entry.update(bench_row)

        # Pull metadata-driven attributes (license_type, api_stability, etc.)
        meta = m.metadata or {}
        for k in (
            "license_type",
            "api_stability",
            "documentation_quality",
            "openrouter_programming_rank",
        ):
            if k in meta and entry.get(k) is None:
                entry[k] = meta[k]

        # Compute component scores
        entry["benchmark_score"] = round(compute_benchmark_score(entry), 4)
        entry["cost_efficiency_score"] = round(
            compute_cost_efficiency_score(entry),
            4,
        )
        entry["accessibility_score"] = round(
            compute_accessibility_score(entry),
            4,
        )
        entry["adoption_score"] = round(compute_adoption_score(entry), 4)
        entry["mss_score"] = compute_mss(entry)
        entry["empirical_quality_score"] = compute_empirical_quality(entry)
        # Metadata-based MSS blended with locally measured quality; models
        # never exercised here keep composite == MSS.
        entry["composite_score"] = compute_composite(
            entry["mss_score"],
            entry["empirical_quality_score"],
        )

        rows.append(entry)

    rows.sort(key=lambda r: r.get("composite_score") or 0.0, reverse=True)
    return rows


def get_status() -> dict[str, Any]:
    """Diagnostic info: counts of seeded benchmarks per benchmark name."""

    counts: dict[str, int] = {}
    for r in BenchmarkResult.objects.values("benchmark").annotate(c=Count("id")):
        counts[r["benchmark"]] = int(r["c"])
    return {
        "benchmarks": counts,
        "total_benchmark_rows": sum(counts.values()),
        "models_with_benchmarks": (BenchmarkResult.objects.values("model_id").distinct().count()),
        "total_models": LLMModel.objects.count(),
    }
