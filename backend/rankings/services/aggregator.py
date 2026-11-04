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
from backend.generation.models import GenerationRun
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus
from backend.generation.models.choices import Stage
from backend.generation.runs.view import run_views
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
from backend.statistics.services.runs import study_current_runs

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

_ZERO_ROLLUP = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
# Usable outcomes that never reached the tests: the model's failure, 0 pass (intention-to-treat).
_MODEL_FAILURES = frozenset({Outcome.BUILD_FAIL, Outcome.START_FAIL, Outcome.OUTPUT_INVALID})


def _is_test_path(path: str) -> bool:
    """``reports.services.loc.is_test_path``, the one test-path rule (imported per call, like ``tool_coverage``)."""
    from backend.reports.services.loc import is_test_path

    return is_test_path(path)


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(pystats.stdev(values), 4)


def _unit(condition_id: Any, task_id: Any, run_id: str) -> tuple:
    """The unit of analysis: a task in a condition (repeats collapse into it); a run without a task is its own unit."""
    return (condition_id, task_id) if task_id is not None else ("run", run_id)


def _pooling_note(conditions: int) -> str | None:
    if conditions <= 1:
        return None
    return (
        f"Pools {conditions} study conditions of this model (harness, prompt or other study "
        "arms); the study reports compare conditions separately."
    )


def _pass_rate(outcome: str, usable: bool | None, raw_metrics: Any) -> float | None:
    """A run's functional pass rate, intention-to-treat (spec 6.13).

    A usable run that never reached the tests (``build_fail``, ``start_fail``,
    ``output_invalid``) scores 0: the failure is the model's. A tested run
    scores its latest evaluation's ``agent_pass_rate`` (``metrics["eval"]``)
    only when that evaluation measured it; otherwise, and for runs that are
    not usable (``infra_error``, ``excluded``), the run is ungraded (None),
    left out of the mean rather than scored 0.
    """
    if usable is not True:
        return None
    if outcome in _MODEL_FAILURES:
        return 0.0
    if outcome != Outcome.TESTED:
        return None
    metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
    evaluation = metrics.get("eval")
    if not isinstance(evaluation, dict) or not evaluation.get("measured"):
        return None
    rate = evaluation.get("agent_pass_rate")
    if isinstance(rate, bool) or not isinstance(rate, int | float):
        return None
    return float(rate)


def _norm_path(path: str | None) -> str:
    return (path or "").removeprefix("./")


def _compared_runs(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    config_fingerprint: str | None = None,
    study_id: str | None = None,
) -> QuerySet[GenerationRun]:
    """The runs a ranking compares: ad hoc runs plus each main-stage study cell's current run.

    A study cell contributes one run, its current attempt (``runs.view.current_runs``: the first
    usable attempt, else the latest, so attrition stays visible as a non-usable run), never every
    attempt. Pilot cells (and pilot-stage runs outside a cell) are not part of the sample
    (Appendix A #11), and runs created beyond a study's plan are never counted.
    """
    runs = (
        GenerationRun.objects.exclude(model__isnull=True)
        .filter(beyond_plan=False)
        .exclude(cell__stage=Stage.PILOT)
        .exclude(cell__isnull=True, stage=Stage.PILOT)
    )
    if user is not None and getattr(user, "is_authenticated", False):
        runs = runs.filter(created_by=user)
    if prompt_hash:
        runs = runs.filter(prompt_hash=prompt_hash)
    if config_fingerprint:
        runs = runs.filter(config_fingerprint=config_fingerprint)
    if study_id:
        runs = runs.filter(study_id=study_id)
    current: set[Any] = set()
    study_ids = runs.filter(cell__isnull=False).values_list("study_id", flat=True).distinct()
    for sid in study_ids:
        if sid is not None:
            current.update(study_current_runs(sid, Stage.MAIN).values_list("pk", flat=True))
    return runs.filter(Q(cell__isnull=True) | Q(pk__in=current))


def _local_app_stats(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    config_fingerprint: str | None = None,
    study_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate per-model apps generated and finding rollups.

    The ``findings`` rollup that the empirical score is computed from holds
    the compared apps' *code* issues, measured with one instrument set:

    * each run's latest result per tool (a re-analysis never double-counts),
    * only tools that measured every analyzed app in scope
      (:func:`backend.statistics.services.study.tool_coverage`), so a model
      whose apps saw fewer tools cannot look cleaner,
    * not AI-reviewer findings (opinions; collected under ``ai_findings``),
    * not dependency advisories from the known-vulnerability scanners
      (``dependency_findings``: packages, mostly the starter's pins, not
      lines the model wrote; the shared rule
      :func:`~backend.analysis.services.base.is_dependency_advisory`),
    * not dynamic-tool findings (``runtime_findings``: the running app),
    * only findings in files the model wrote (the run's model scope,
      ``RunView.files("model")``): analysis runs on the whole app, and the
      starter's files are the same for every model,
    * not findings in test code (``test_findings``; the source LOC excludes
      test files too).

    A count nothing measured is None, not 0: every count of a model with no
    measured app, ``dependency_findings`` when no advisory scanner is in the
    instrument set, ``runtime_findings`` when no dynamic tool is. A run
    without a collected tree (no model-scope LOC) is not measured.
    ``conditions_pooled`` says how many study conditions a model's row
    pools (a scope with several conditions per model mixes their arms).

    The density uses the model scope's source LOC (``RunView.loc``). Repeats
    of one task in one condition collapse to their mean before the per-model
    aggregate, so a task generated five times does not weigh five times
    (``empirical_findings`` / ``empirical_loc``, ``variance``).

    ``prompt_hash``/``config_fingerprint`` narrow the comparison to runs
    generated from the same prompt material / configuration; without this,
    runs of different prompt versions on the same task are pooled together.
    ``study_id`` narrows to one study's runs directly. The run set is
    :func:`_compared_runs` (one current run per main-stage cell, no pilot,
    nothing beyond plan); ``apps`` counts every compared run, so attrition
    stays visible, but findings and LOC come only from usable runs.
    """
    from backend.analysis.models import ToolResult
    from backend.statistics.services.study import tool_coverage

    runs = _compared_runs(
        user,
        prompt_hash=prompt_hash,
        config_fingerprint=config_fingerprint,
        study_id=study_id,
    )

    by_model = runs.values("model__model_id").annotate(
        apps=Count("id"),
        apps_completed=Count("id", filter=Q(status=RunStatus.COMPLETED)),
        apps_failed=Count("id", filter=Q(status=RunStatus.FAILED)),
        conditions=Count("condition_id", distinct=True),
        avg_duration=Avg(
            "duration_seconds",
            filter=Q(status=RunStatus.COMPLETED),
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

    # Each compared run's latest result per tool (otherwise re-analyzing a
    # run double-counts its findings, and re-running a single tool would drop
    # the others) and the one instrument set every compared app was measured with.
    latest_result_ids = ToolResult.latest_ids_per_job(runs.values_list("id", flat=True))
    coverage = tool_coverage(latest_result_ids, include_ai=False)
    dynamic_slugs = set(
        AnalyzerTool.objects.filter(analysis_mode=AnalysisMode.DYNAMIC).values_list("slug", flat=True),
    )
    common = set(coverage["tools_common"])
    code_tools = common - dynamic_slugs
    # An app the counted tools did not all measure has unknown counts, not zero.
    analysed = {str(run_id) for run_id in coverage["apps_measured"]} if common else set()

    # Model-scope source LOC and pass rate per completed run, with the run's
    # unit (task x condition) for collapsing repeats.
    completed = list(runs.filter(status=RunStatus.COMPLETED).select_related("model"))
    run_info: dict[str, dict[str, Any]] = {}
    model_paths: dict[str, set[str]] = {}
    measured_jobs: set[str] = set()
    for run, view in zip(completed, run_views(completed), strict=True):
        model_id = run.model.model_id
        if model_id not in out:
            continue
        key = str(run.id)
        metrics = run.metrics if isinstance(run.metrics, dict) else {}
        loc = metrics.get("loc_model")
        if isinstance(loc, bool) or not isinstance(loc, int):
            # None when no tree was collected: unknown, never 0.
            loc = view.loc("model")
        run_info[key] = {
            "model": model_id,
            "unit": _unit(run.condition_id, run.task_version_id, key),
            "loc": loc,
            "pass_rate": _pass_rate(run.outcome, run.usable, run.metrics),
        }
        # A run without a collected tree cannot attribute findings to the
        # model's files, so it is not measured (never counted as clean). A
        # run that is not usable (infra_error, excluded) is attrition, not
        # sample: its findings and LOC never enter the density.
        if key in analysed and loc is not None and run.usable is True:
            measured_jobs.add(key)
            model_paths[key] = {_norm_path(entry.path) for entry in view.files("model")}
            # LOC is summed over measured runs only: findings exist only for
            # those, so the density's numerator and denominator describe the same apps.
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
        elif _norm_path(r["file_path"]) not in model_paths.get(job, set()):
            # The starter's (or a parent's) file: not the model's code.
            continue
        elif _is_test_path(r["file_path"]):
            out[mid]["test_findings"] += count
        elif tool in code_tools and r["severity"] in _ZERO_ROLLUP:
            out[mid]["findings"][r["severity"]] += count
            sev = job_severities.setdefault(job, {})
            sev[r["severity"]] = sev.get(r["severity"], 0) + count

    ai_rows = (
        base.filter(result__tool_slug__in=ai_slugs, result__run__generation_job__usable=True)
        .values("result__run__generation_job__model__model_id", "severity")
        .annotate(c=Count("id"))
    )
    for r in ai_rows:
        mid = r["result__run__generation_job__model__model_id"]
        if mid in out and r["severity"] in _ZERO_ROLLUP:
            out[mid]["ai_findings"][r["severity"]] += int(r["c"])

    # Repeats collapse to their unit's mean first: the per-model density is
    # the sum of unit-mean weighted findings over the sum of unit-mean LOC,
    # and the spread is over units. Runs never measured are excluded: a
    # missing analysis is not a clean bill of health.
    density_jobs: dict[str, dict[tuple, list[str]]] = defaultdict(lambda: defaultdict(list))
    for job_id in measured_jobs:
        info = run_info.get(job_id)
        if info is not None and info["loc"] > 0:
            density_jobs[info["model"]][info["unit"]].append(job_id)
    rate_units: dict[str, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
    for info in run_info.values():
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
            empirical_loc += sum(run_info[j]["loc"] for j in members) / k
            unit_densities.append(
                pystats.fmean(
                    sum(SEVERITY_WEIGHTS.get(sev, 0) * c for sev, c in job_severities.get(j, {}).items())
                    / run_info[j]["loc"]
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
    measured_models = {run_info[j]["model"] for j in measured_jobs}
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
    config_fingerprint: str | None = None,
    study_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build the ranking list from LLMModel + benchmarks + local stats.

    With ``user`` given, local stats (apps, findings, empirical scores) are
    scoped to that user's generation runs; benchmarks and metadata are global
    either way. ``prompt_hash``/``config_fingerprint``/``study_id`` further
    narrow local stats to runs generated from one prompt version, one
    configuration or one study: comparisons across models are only
    apples-to-apples when they used the same prompt material.
    """

    local = _local_app_stats(
        user,
        prompt_hash=prompt_hash,
        config_fingerprint=config_fingerprint,
        study_id=study_id,
    )
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
