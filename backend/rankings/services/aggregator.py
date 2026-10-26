"""Build the rankings list from LLMModel + benchmarks + measured local stats.

Local (empirical) stats come from the canonical measured runs of the scoped
jobs under one frozen plan (``services/measured.py``): counted static
findings/issues and measured SLOC of the *same* analysed jobs, AI-reviewer
consensus findings kept separate, ``n_trials`` = analysed complete jobs.
"""

from __future__ import annotations

import statistics as pystats
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q

from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.rankings.models import BenchmarkResult
from backend.rankings.services.constants import EMPIRICAL_DENSITY_CAP
from backend.rankings.services.constants import SCORING_VERSION
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.measured import MeasuredRun
from backend.rankings.services.measured import Selection
from backend.rankings.services.measured import add_severities
from backend.rankings.services.measured import select_measured
from backend.rankings.services.measured import weighted_issue_score
from backend.rankings.services.measured import zero_severities
from backend.rankings.services.scoring import compute_accessibility_score
from backend.rankings.services.scoring import compute_adoption_score
from backend.rankings.services.scoring import compute_benchmark_score
from backend.rankings.services.scoring import compute_composite
from backend.rankings.services.scoring import compute_cost_efficiency_score
from backend.rankings.services.scoring import compute_empirical_quality
from backend.rankings.services.scoring import compute_mss

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import AbstractBaseUser

_ZERO_ROLLUP = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(pystats.stdev(values), 4)


def _mean(values: list[float]) -> float | None:
    return round(pystats.mean(values), 4) if values else None


def scoped_jobs(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
):
    """Generation jobs the rankings are computed over (with a model)."""
    jobs = GenerationJob.objects.exclude(model__isnull=True)
    if user is not None and getattr(user, "is_authenticated", False):
        jobs = jobs.filter(created_by=user)
    if prompt_hash:
        jobs = jobs.filter(prompt_hash=prompt_hash)
    if bundle_key:
        jobs = jobs.filter(bundle_key=bundle_key)
    if experiment_id:
        jobs = jobs.filter(experiment_id=experiment_id)
    return jobs


def empirical_inputs(runs: Iterable[MeasuredRun]) -> dict[str, Any]:
    """Roll canonical measured runs up into the empirical inputs of one ranking row.

    ``findings``/``issues``/``measured_sloc`` cover exactly the runs whose
    static measurement is available, so the density's numerator and
    denominator describe the same jobs; ``ai_findings`` covers runs with a
    successful AI review and never feeds the score.
    """
    findings = zero_severities()
    issues = zero_severities()
    ai_findings = zero_severities()
    measured_sloc = 0
    n_trials = 0
    n_static = 0
    n_ai = 0
    densities: list[float] = []
    functional_rates: list[float] = []
    run_ids: list[str] = []
    for run in runs:
        n_trials += 1
        run_ids.append(str(run.run_id))
        rate = run.functional_pass_rate()
        if rate is not None:
            functional_rates.append(rate)
        if run.static_available:
            n_static += 1
            add_severities(findings, run.findings_by_severity())
            run_issues = run.issues_by_severity()
            add_severities(issues, run_issues)
            sloc = run.measured_sloc() or 0
            measured_sloc += sloc
            # Per-trial density only where the measurement itself defines one
            # (the outcome is None below the plan's min_sloc_for_density).
            if sloc > 0 and run.outcomes.get("static_issues_per_kloc") is not None:
                densities.append(weighted_issue_score(run_issues, SEVERITY_WEIGHTS) / sloc * 1000)
        ai = run.ai_by_severity()
        if ai is not None:
            n_ai += 1
            add_severities(ai_findings, ai)
    return {
        "n_trials": n_trials,
        "n_static": n_static,
        "n_ai": n_ai,
        "findings": findings,
        "issues": issues,
        "ai_findings": ai_findings,
        "measured_sloc": measured_sloc if n_static else None,
        "functional_pass_rate": _mean(functional_rates),
        "functional_n": len(functional_rates),
        "variance": {
            "n_jobs": len(densities),
            "density_per_kloc_mean": _mean(densities),
            "density_per_kloc_stdev": _stdev(densities),
            "smoke_pass_rate_stdev": _stdev(functional_rates),
        },
        "run_ids": run_ids,
    }


def _local_app_stats(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
    plan_hash: str | None = None,
    selection: Selection | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-model generation counts and measured empirical inputs.

    ``prompt_hash``/``bundle_key``/``experiment_id`` narrow the comparison to
    jobs generated from the same prompt material or one designed experiment;
    ``plan_hash`` pins the analysis plan (default: the plan that measured the
    most of these jobs). Pass a precomputed *selection* to reuse it.
    """
    jobs = scoped_jobs(user, prompt_hash=prompt_hash, bundle_key=bundle_key, experiment_id=experiment_id)

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
        }

    job_model = dict(jobs.filter(status="completed").values_list("id", "model__model_id"))
    if selection is None:
        selection = select_measured(list(job_model), plan_hash=plan_hash)
    jobs_by_model: dict[str, list[Any]] = {}
    for job_id, model_id in job_model.items():
        jobs_by_model.setdefault(model_id, []).append(job_id)

    for model_id, stats in out.items():
        model_jobs = jobs_by_model.get(model_id, [])
        inputs = empirical_inputs(selection.runs_for(model_jobs))
        coverage = selection.coverage(model_jobs)
        stats.update(
            {
                "findings": inputs["findings"],
                "issues": inputs["issues"],
                "ai_findings": inputs["ai_findings"],
                # Measured SLOC of the analysed jobs (model-written lines only),
                # the same jobs as the findings: never all completed jobs.
                "local_loc": inputs["measured_sloc"] or 0,
                "measured_sloc": inputs["measured_sloc"],
                "functional_pass_rate": inputs["functional_pass_rate"],
                "functional_n": inputs["functional_n"],
                "n_trials": inputs["n_trials"],
                "n_static": inputs["n_static"],
                "n_ai": inputs["n_ai"],
                "variance": inputs["variance"],
                "analysis": {
                    "measured": coverage["measured"],
                    "excluded": coverage["excluded"],
                    "excluded_reasons": coverage["excluded_reasons"],
                    "incomplete": coverage["incomplete"],
                    "other_plan": coverage["other_plan"],
                    "analysis_failed": coverage["analysis_failed"],
                    "in_progress": coverage["in_progress"],
                    "not_analysed": coverage["not_analysed"],
                },
                "run_ids": inputs["run_ids"],
            },
        )
    return out


def _benchmarks_by_model() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in BenchmarkResult.objects.all().values("model_id", "benchmark", "score"):
        out.setdefault(r["model_id"], {})[r["benchmark"]] = float(r["score"])
    return out


def build_rankings(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
    plan_hash: str | None = None,
) -> dict[str, Any]:
    """Ranking rows plus the comparability context they were computed under.

    With ``user`` given, local stats are scoped to that user's generation
    jobs; benchmarks and metadata are global either way. All empirical
    numbers come from one plan: ``comparability`` names it and counts the
    jobs left out (not analysed, incomplete, other plan, excluded).
    """
    jobs = scoped_jobs(user, prompt_hash=prompt_hash, bundle_key=bundle_key, experiment_id=experiment_id)
    completed_ids = list(jobs.filter(status="completed").values_list("id", flat=True))
    selection = select_measured(completed_ids, plan_hash=plan_hash)
    local = _local_app_stats(
        user,
        prompt_hash=prompt_hash,
        bundle_key=bundle_key,
        experiment_id=experiment_id,
        selection=selection,
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
            "avg_duration": local_row.get("avg_duration", 0.0),
            "findings": local_row.get("findings", dict(_ZERO_ROLLUP)),
            "issues": local_row.get("issues", dict(_ZERO_ROLLUP)),
            "ai_findings": local_row.get("ai_findings", dict(_ZERO_ROLLUP)),
            "local_loc": local_row.get("local_loc", 0),
            "measured_sloc": local_row.get("measured_sloc"),
            "functional_pass_rate": local_row.get("functional_pass_rate"),
            "functional_n": local_row.get("functional_n", 0),
            "n_trials": local_row.get("n_trials", 0),
            "n_static": local_row.get("n_static", 0),
            "n_ai": local_row.get("n_ai", 0),
            "variance": local_row.get("variance"),
            "analysis": local_row.get("analysis"),
            "run_ids": local_row.get("run_ids", []),
            "plan_hash": selection.plan_hash,
            "scoring_version": SCORING_VERSION,
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

    rows.sort(key=lambda r: (-(r.get("composite_score") or 0.0), r["model_id"]))
    comparability = selection.coverage()
    comparability.pop("run_ids", None)
    comparability.update(
        {
            "scoring_version": SCORING_VERSION,
            "severity_weights": dict(SEVERITY_WEIGHTS),
            "density_cap": EMPIRICAL_DENSITY_CAP,
            "density_basis": "counted static issues per KLOC of measured SLOC",
            "filters": {
                "prompt_hash": prompt_hash or None,
                "bundle_key": bundle_key or None,
                "experiment_id": str(experiment_id) if experiment_id else None,
                "plan_hash": plan_hash or None,
                "user_scoped": bool(user is not None and getattr(user, "is_authenticated", False)),
            },
        },
    )
    return {"rows": rows, "comparability": comparability}


def aggregate_rankings(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
    plan_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Ranking rows sorted by composite score (see :func:`build_rankings`)."""
    return build_rankings(
        user,
        prompt_hash=prompt_hash,
        bundle_key=bundle_key,
        experiment_id=experiment_id,
        plan_hash=plan_hash,
    )["rows"]


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
