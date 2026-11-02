"""Build the rankings list from LLMModel + benchmarks + local stats."""

from __future__ import annotations

import statistics as pystats
from collections import defaultdict
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q

from backend.analysis.models import AnalysisMode
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.services.base import ADVISORY_TOOLS
from backend.analysis.services.base import is_dependency_advisory
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
from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_from_result_data
from backend.statistics.services.study import tool_coverage

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

_ZERO_ROLLUP = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(pystats.stdev(values), 4)


def _unit(condition_id: Any, app_id: Any, job_id: str) -> tuple:
    """The unit of analysis: an app in a condition (repeats collapse into it); a job without an app is its own unit."""
    return (condition_id, app_id) if app_id is not None else ("job", job_id)


def _pooling_note(conditions: int) -> str | None:
    if conditions <= 1:
        return None
    return (
        f"Pools {conditions} experiment conditions of this model (harness, prompt-profile or experiment "
        "arms); the study reports compare conditions separately."
    )


def _local_app_stats(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate per-model apps generated and finding rollups.

    The ``findings`` rollup that the empirical score is computed from holds
    the compared apps' *code* issues, measured with one instrument set:

    * each job's latest result per tool (a re-analysis never double-counts),
    * only tools that measured every analyzed app in scope
      (:func:`backend.statistics.services.study.tool_coverage`), so a model
      whose apps saw fewer tools cannot look cleaner,
    * not AI-reviewer findings (opinions; collected under ``ai_findings``),
    * not dependency advisories from the known-vulnerability scanners
      (``dependency_findings``: packages, mostly the scaffold's pins, not
      lines the model wrote; the shared rule
      :func:`~backend.analysis.services.base.is_dependency_advisory`),
    * not dynamic-tool findings (``runtime_findings``: the running app),
    * not findings in test code (``test_findings``; the source LOC excludes
      test files too).

    A count nothing measured is None, not 0: every count of a model with no
    measured app, ``dependency_findings`` when no advisory scanner is in the
    instrument set, ``runtime_findings`` when no dynamic tool is.
    ``conditions_pooled`` says how many experiment conditions a model's row
    pools (a scope with several conditions per model mixes their arms).

    The density uses source LOC (``reports.services.loc``). Repeats of one
    app in one condition collapse to their mean before the per-model
    aggregate, so an app generated five times does not weigh five times
    (``empirical_findings`` / ``empirical_loc``, ``variance``).

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
        apps_failed=Count("id", filter=Q(status="failed")),
        conditions=Count("condition_id", distinct=True),
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
            "apps_failed": int(row["apps_failed"]),
            # None (not 0.0) when no generation completed: nothing was timed.
            "avg_duration": (round(row["avg_duration"], 1) if row["avg_duration"] is not None else None),
            "conditions_pooled": int(row["conditions"]),
            "findings": dict(_ZERO_ROLLUP),
            "ai_findings": dict(_ZERO_ROLLUP),
            "dependency_findings": 0,
            "runtime_findings": 0,
            "test_findings": 0,
            "local_loc": 0,
            "functional_pass_rate": None,
        }

    # Each compared job's latest result per tool — otherwise re-analyzing a
    # job double-counts its findings (and re-running a single tool would drop
    # the others) — and the one instrument set every compared app was measured with.
    from backend.analysis.models import ToolResult

    latest_result_ids = ToolResult.latest_ids_per_job(jobs.values_list("id", flat=True))
    coverage = tool_coverage(latest_result_ids, include_ai=False)
    dynamic_slugs = set(
        AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True),
    )
    common = set(coverage["tools_common"])
    code_tools = common - dynamic_slugs
    # An app the counted tools did not all measure has unknown counts, not zero.
    measured_jobs = set(coverage["apps_measured"]) if common else set()

    # Source LOC (result_data) + smoke results (metrics) per completed job,
    # with the job's unit (app x condition) for collapsing repeats.
    job_info: dict[str, dict[str, Any]] = {}
    for job_id, model_id, raw_metrics, result_data, condition_id, app_id in jobs.filter(
        status="completed",
    ).values_list(
        "id",
        "model__model_id",
        "metrics",
        "result_data",
        "condition_id",
        "app_requirement_id",
    ):
        if model_id not in out:
            continue
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        # Jobs without stored code (legacy rows) fall back to the generation-time count.
        loc = loc_from_result_data(result_data)["total_loc"] or int(metrics.get("lines_of_code") or 0)
        rate = None
        functional = metrics.get("functional")
        # ``pass_rate`` is null when the suite never ran (infra_error): that
        # job is ungraded, so it is left out of the mean rather than scored 0.
        if isinstance(functional, dict) and functional.get("pass_rate") is not None:
            try:
                rate = float(functional["pass_rate"])
            except (TypeError, ValueError):
                rate = None
        key = str(job_id)
        job_info[key] = {"model": model_id, "unit": _unit(condition_id, app_id, key), "loc": loc, "pass_rate": rate}
        # LOC is summed over measured jobs only: findings exist only for
        # those, so the density's numerator and denominator describe the same apps.
        if key in measured_jobs:
            out[model_id]["local_loc"] += loc

    base = Finding.objects.filter(result_id__in=latest_result_ids)
    ai_slugs = AnalyzerTool.ai_slugs()

    job_severities: dict[str, dict[str, int]] = {}
    static_rows = (
        base.exclude(result__tool_slug__in=ai_slugs)
        .filter(result__tool_slug__in=common)
        .values(
            "result__run__generation_job__model__model_id",
            "result__run__generation_job_id",
            "result__tool_slug",
            "category",
            "severity",
            "file_path",
        )
        .annotate(c=Count("id"))
    )
    for r in static_rows:
        mid = r["result__run__generation_job__model__model_id"]
        job = str(r["result__run__generation_job_id"])
        if mid not in out or job not in measured_jobs:
            continue
        tool, count = r["result__tool_slug"], int(r["c"])
        if tool in dynamic_slugs:
            out[mid]["runtime_findings"] += count
        elif is_dependency_advisory(tool, r["category"]):
            out[mid]["dependency_findings"] += count
        elif is_test_path(r["file_path"]):
            out[mid]["test_findings"] += count
        elif tool in code_tools and r["severity"] in _ZERO_ROLLUP:
            out[mid]["findings"][r["severity"]] += count
            sev = job_severities.setdefault(job, {})
            sev[r["severity"]] = sev.get(r["severity"], 0) + count

    ai_rows = (
        base.filter(result__tool_slug__in=ai_slugs)
        .values("result__run__generation_job__model__model_id", "severity")
        .annotate(c=Count("id"))
    )
    for r in ai_rows:
        mid = r["result__run__generation_job__model__model_id"]
        if mid in out and r["severity"] in _ZERO_ROLLUP:
            out[mid]["ai_findings"][r["severity"]] += int(r["c"])

    # Repeats collapse to their unit's mean first: the per-model density is
    # the sum of unit-mean weighted findings over the sum of unit-mean LOC,
    # and the spread is over units. Jobs never measured are excluded — a
    # missing analysis is not a clean bill of health.
    density_jobs: dict[str, dict[tuple, list[str]]] = defaultdict(lambda: defaultdict(list))
    for job_id in measured_jobs:
        info = job_info.get(job_id)
        if info is not None and info["loc"] > 0:
            density_jobs[info["model"]][info["unit"]].append(job_id)
    rate_units: dict[str, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
    for info in job_info.values():
        if info["pass_rate"] is not None:
            rate_units[info["model"]][info["unit"]].append(info["pass_rate"])

    for model_id, stats in out.items():
        units = density_jobs.get(model_id, {})
        empirical = dict.fromkeys(_ZERO_ROLLUP, 0.0)
        empirical_loc = 0.0
        unit_densities = []
        for members in units.values():
            k = len(members)
            for job_id in members:
                for sev, count in job_severities.get(job_id, {}).items():
                    empirical[sev] += count / k
            empirical_loc += sum(job_info[j]["loc"] for j in members) / k
            unit_densities.append(
                pystats.fmean(
                    sum(SEVERITY_WEIGHTS.get(sev, 0) * c for sev, c in job_severities.get(j, {}).items())
                    / job_info[j]["loc"]
                    * 1000
                    for j in members
                ),
            )
        stats["empirical_findings"] = {sev: round(v, 4) for sev, v in empirical.items()}
        stats["empirical_loc"] = round(empirical_loc, 4)
        unit_rates = [pystats.fmean(v) for v in rate_units.get(model_id, {}).values()]
        if unit_rates:
            stats["functional_pass_rate"] = round(pystats.fmean(unit_rates), 4)
        stats["variance"] = {
            "n_jobs": sum(len(m) for m in units.values()),
            "n_apps": len(unit_densities),
            "density_per_kloc_mean": (round(pystats.mean(unit_densities), 4) if unit_densities else None),
            "density_per_kloc_stdev": _stdev(unit_densities),
            "smoke_pass_rate_stdev": _stdev(unit_rates),
        }
    stats_scope = {
        "tools_counted": sorted(code_tools),
        "tools_left_out": sorted(set(coverage["tools_partial"])),
        "apps_measured": len(measured_jobs),
    }
    # Unmeasured is never 0: a model none of whose apps the instrument set
    # measured has unknown counts, and a kind of finding no counted tool can
    # report (no advisory scanner, no dynamic tool) was not looked for.
    measured_models = set(jobs.filter(id__in=measured_jobs).values_list("model__model_id", flat=True))
    advisories_measured = bool(common & ADVISORY_TOOLS)
    runtime_measured = bool(common & dynamic_slugs)
    for model_id, stats in out.items():
        stats["instrument_set"] = stats_scope
        if model_id not in measured_models:
            for key in ("findings", "dependency_findings", "runtime_findings", "test_findings"):
                stats[key] = None
            continue
        if not advisories_measured:
            stats["dependency_findings"] = None
        if not runtime_measured:
            stats["runtime_findings"] = None

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
            "apps_failed": local_row.get("apps_failed", 0),
            "avg_duration": local_row.get("avg_duration"),
            "conditions_pooled": local_row.get("conditions_pooled", 0),
            "pooling_note": _pooling_note(local_row.get("conditions_pooled", 0)),
            # A model never exercised here was not measured: None, not zero counts.
            "findings": local_row.get("findings"),
            "ai_findings": local_row.get("ai_findings", dict(_ZERO_ROLLUP)),
            "dependency_findings": local_row.get("dependency_findings"),
            "runtime_findings": local_row.get("runtime_findings"),
            "test_findings": local_row.get("test_findings"),
            "local_loc": local_row.get("local_loc", 0),
            "empirical_findings": local_row.get("empirical_findings", dict(_ZERO_ROLLUP)),
            "empirical_loc": local_row.get("empirical_loc", 0),
            "functional_pass_rate": local_row.get("functional_pass_rate"),
            "n_trials": local_row.get("apps_completed", 0),
            "variance": local_row.get("variance"),
            "instrument_set": local_row.get("instrument_set"),
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
