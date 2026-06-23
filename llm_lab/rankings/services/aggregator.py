"""Build the rankings list from LLMModel + benchmarks + local stats."""

from __future__ import annotations

from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q

from llm_lab.analysis.models import Finding
from llm_lab.generation.models import GenerationJob
from llm_lab.llm_models.models import LLMModel
from llm_lab.rankings.models import BenchmarkResult
from llm_lab.rankings.services.scoring import compute_accessibility_score
from llm_lab.rankings.services.scoring import compute_adoption_score
from llm_lab.rankings.services.scoring import compute_benchmark_score
from llm_lab.rankings.services.scoring import compute_cost_efficiency_score
from llm_lab.rankings.services.scoring import compute_mss


def _local_app_stats() -> dict[str, dict[str, Any]]:
    """Aggregate per-model apps generated and finding rollups."""

    by_model = (
        GenerationJob.objects.exclude(model__isnull=True)
        .values("model__model_id")
        .annotate(
            apps=Count("id"),
            apps_completed=Count("id", filter=Q(status="completed")),
            avg_duration=Avg(
                "duration_seconds",
                filter=Q(status="completed"),
            ),
        )
    )
    out: dict[str, dict[str, Any]] = {}
    for row in by_model:
        out[row["model__model_id"]] = {
            "apps": int(row["apps"]),
            "apps_completed": int(row["apps_completed"]),
            "avg_duration": (round(row["avg_duration"], 1) if row["avg_duration"] else 0.0),
            "findings": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
        }

    sev_rows = (
        Finding.objects.exclude(
            result__run__generation_job__model__model_id__isnull=True,
        )
        .values(
            "result__run__generation_job__model__model_id",
            "severity",
        )
        .annotate(c=Count("id"))
    )
    for r in sev_rows:
        mid = r["result__run__generation_job__model__model_id"]
        if mid in out:
            out[mid]["findings"][r["severity"]] = int(r["c"])

    return out


def _benchmarks_by_model() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in BenchmarkResult.objects.all().values("model_id", "benchmark", "score"):
        out.setdefault(r["model_id"], {})[r["benchmark"]] = float(r["score"])
    return out


def aggregate_rankings() -> list[dict[str, Any]]:
    """Build the ranking list from LLMModel + benchmarks + local stats."""

    local = _local_app_stats()
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
            "findings": local_row.get(
                "findings",
                {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
            ),
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
        # Backward-compat aliases used by old API consumers.
        entry["composite_score"] = entry["mss_score"]

        rows.append(entry)

    rows.sort(key=lambda r: r.get("mss_score") or 0.0, reverse=True)
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
