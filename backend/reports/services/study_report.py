"""The study report: every table a paper needs from one scope of jobs, with its uncertainty.

Sections (each a JSON object/list in ``report_data``):

* ``protocol`` -- what was run: scope, models, templates, repeats, stacks,
  prompt versions, tool and evaluator versions, healing modes, seeds, the
  pre-registered analysis plan and its hash, and a fingerprint of the data.
* ``definitions`` -- what "generated", "deployed" and "functionally
  correct" mean here, so the three are never conflated.
* ``funnel`` -- generated -> analyzed -> evaluated -> deployed -> passes
  every check, per model and template, with Wilson 95% intervals.
* ``issues`` -- cross-tool deduplicated issues per model/template/tool and
  the tool-overlap table (``statistics.services.study``), read from each
  job's canonical analysis run under ONE frozen analysis plan (its stored
  measurement and the engine's ``issue_key`` deduplication), with the
  analysis ``coverage`` funnel (jobs not measured under the plan, and why).
* ``security_bounds`` -- per severity: issues, apps affected, and for a zero
  count the exact 95% upper bound on the per-app rate.
* ``code_metrics`` -- size, backend share, complexity per app, model and
  template; the spread across models on the same template; app-level
  correlations of size/complexity with issue density.
* ``correlations`` -- model parameters (nominal and effective context
  window, output limit, prices) vs outcomes: Spearman rho, permutation p,
  bootstrap CI, Holm and BH across the whole family, leave-one-out influence.
* ``ranking`` -- TOPSIS/WSM with weight and data sensitivity
  (``statistics.services.mcdm``).
* ``planned_comparisons`` -- for an experiment with a pre-registered plan:
  every declared metric, each condition vs the baseline, app-blocked,
  sign + Wilcoxon tests corrected as the plan says.
* ``healing``, ``accounting`` -- the build-healing ablation and what ran.
* ``validity`` -- threats detected in this data, each with the section it
  affects.

Missing data stays ``None`` (an app whose static measurement is unavailable
has no issue counts and is left out of sums, rates and densities, never
counted as clean); issue densities divide by measured SLOC (the code the
model wrote, the scope the counts cover); AI-reviewer consensus is reported
in its own ``ai_*`` columns and never added to static issues.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections import defaultdict
from typing import Any

from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.generation.services.analysis_plan import METRICS as PLAN_METRICS
from backend.generation.services.analysis_plan import declared_metrics
from backend.generation.services.evaluation.runner import NON_SAMPLE_STATUSES
from backend.generation.services.experiment_results import sign_test_p
from backend.generation.services.experiment_results import wilcoxon_p
from backend.rankings.services.measured import default_rules
from backend.rankings.services.measured import select_measured
from backend.reports.services.loc import loc_from_job
from backend.statistics.services import inference as inf
from backend.statistics.services.accounting import run_accounting
from backend.statistics.services.accounting import tool_inventory
from backend.statistics.services.mcdm import criteria_from_config
from backend.statistics.services.mcdm import rank_models
from backend.statistics.services.study import SEVERITIES
from backend.statistics.services.study import healing_impact
from backend.statistics.services.study import issue_breakdowns
from backend.statistics.services.study import job_labels
from backend.statistics.services.study import study_jobs

# 2: issue tables from v2 measurements (canonical runs under one plan,
# engine dedup, measured SLOC, None for missing data, AI apart).
REPORT_VERSION = 2
SMALL_N_MODELS = 10
MIN_APPS_PER_MODEL = 5
# Eligibility rules a report config may override (as the other report generators).
RULE_KEYS = ("exclude_invalid", "exclude_empty_output", "exclude_truncated", "exclude_run_limits")
# Conformance statuses whose app never deployed: pass rate 0 by definition.
_NOT_DEPLOYED = ("build_failed", "start_failed")

DEFINITIONS = {
    "generated": "The generation job completed and returned code (not a measure of quality).",
    "truncated": "The model's output hit max_tokens; the app is incomplete by construction.",
    "analyzed": (
        "The app has a complete analysis run under the report's analysis plan (its canonical run is counted); "
        "apps analysed only under another plan, incompletely, or not at all are listed in issues.coverage."
    ),
    "static_measured": "The analyzed app's static measurement is available (no required static tool failed).",
    "evaluated": "The conformance evaluator produced an outcome about the sample (infra errors excluded).",
    "deployed": "The app built in Docker and answered its health check (status passed or failed).",
    "functionally_correct": "The app passed every conformance check (status passed).",
    "pass_rate": "Share of conformance checks passed (0..1); 0 for apps that did not deploy.",
    "unique_issue": (
        "One deduplicated static issue: counted findings of equivalent rules (shared CWE or rule class) "
        "from several tools at one location, as the analysis engine grouped them (issue_key)."
    ),
    "measured_loc": "Measured SLOC: non-blank, non-comment lines the model wrote (the scope issue counts cover).",
    "ai_consensus_issue": (
        "An AI-reviewer finding reported by a majority of the review's replicates and grounded in the code; "
        "an opinion, reported apart from static issues."
    ),
}


# ── per-app table ────────────────────────────────────────────────────────


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _pass_rate(status: str | None, summary: dict[str, Any]) -> float | None:
    """The conformance pass rate; 0 for an app that never deployed, None when not recorded."""
    rate = _number(summary.get("pass_rate"))
    if rate is not None:
        return rate
    return 0.0 if status in _NOT_DEPLOYED else None


def _rules(config: dict[str, Any]) -> dict[str, Any]:
    rules = default_rules()
    for key in RULE_KEYS:
        if key in config:
            rules[key] = bool(config[key])
    return rules


def app_table(
    jobs,
    *,
    include_ai: bool = False,
    line_tolerance: int = 2,
    plan_hash: str | None = None,
    rules: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One row per job with every per-app quantity the report aggregates; plus the issue breakdowns.

    Analysis columns come from the job's canonical run under one plan
    (``issue_breakdowns``); they are None for jobs not measured under it
    (``analysis_status`` says why) and for an unavailable static measurement.
    """
    selection = select_measured([j.id for j in jobs], plan_hash=plan_hash, rules=rules)
    issues = issue_breakdowns(jobs, include_ai=include_ai, line_tolerance=line_tolerance, selection=selection)
    by_job = {a["job_id"]: a for a in issues["per_app"]}

    rows = []
    for job in jobs:
        jid = str(job.id)
        data = job.result_data if isinstance(job.result_data, dict) else {}
        metrics = job.metrics if isinstance(job.metrics, dict) else {}
        evaluation = data.get("evaluation") if isinstance(data.get("evaluation"), dict) else {}
        status = evaluation.get("status")
        evaluated = bool(status) and status not in NON_SAMPLE_STATUSES
        summary = evaluation.get("summary") if isinstance(evaluation.get("summary"), dict) else {}
        loc = loc_from_job(job)
        backend, frontend, total = loc["backend_loc"], loc["frontend_loc"], loc["total_loc"]
        analyzed = by_job.get(jid)
        measured = selection.measured.get(job.id)
        complexity = ((measured.measurement.get("metrics") or {}).get("complexity") if measured else None) or {}
        healing = ((evaluation.get("build") or {}).get("healing")) or {}
        static_ok = bool(analyzed and analyzed["static_available"])
        rows.append(
            {
                **job_labels(job),
                "condition_id": job.condition_id,
                "app_id": job.app_requirement_id,
                "stack": job.stack_slug,
                "prompt_hash": job.prompt_hash,
                "status": job.status,
                "generated": job.status == GenerationJob.Status.COMPLETED,
                "truncated": any(data.get(f"{p}_truncated") for p in ("backend", "frontend")),
                "files": loc.get("files"),
                "loc_backend": backend,
                "loc_frontend": frontend,
                "loc": total,
                "backend_share": round(backend / total, 4) if total else None,
                "evaluation_status": status or "",
                "evaluated": evaluated,
                "deployed": status in ("passed", "failed") if evaluated else None,
                "functional_passed": status == "passed" if evaluated else None,
                "pass_rate": _pass_rate(status, summary) if evaluated else None,
                "analysis_status": selection.status_of(job.id),
                "analysis_run_id": analyzed["run_id"] if analyzed else None,
                "analyzed": analyzed is not None,
                "static_measured": static_ok,
                "measured_loc": analyzed["loc"] if analyzed else None,
                "raw_findings": analyzed["raw_findings"] if analyzed else None,
                "unique_issues": analyzed["unique_issues"] if analyzed else None,
                "security_unique": analyzed["security_unique"] if analyzed else None,
                "issues_per_kloc": analyzed["unique_per_kloc"] if analyzed else None,
                **{f"unique_{s}": (analyzed or {}).get(f"unique_{s}") for s in SEVERITIES},
                "ai_consensus_issues": analyzed["ai_consensus_issues"] if analyzed else None,
                "ai_consensus_per_kloc": analyzed["ai_consensus_per_kloc"] if analyzed else None,
                "ai_cost_usd": analyzed["ai_cost_usd"] if analyzed else None,
                "ai_cost_complete": analyzed["ai_cost_complete"] if analyzed else None,
                "avg_complexity": _number(complexity.get("mean")),
                "max_complexity": _number(complexity.get("max")),
                "complexity_source": complexity.get("source"),
                "cost": _number(metrics.get("cost")),
                "tokens": metrics.get("total_tokens"),
                "healing_changed": healing.get("changed"),
            },
        )
    return rows, issues


def _mean(values) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _share(values) -> float | None:
    vals = [bool(v) for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def model_criteria(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """The plan metrics for one group of app rows (``analysis_plan.METRICS``)."""
    # Static outcomes only over apps whose static measurement is available;
    # issues and SLOC of the same apps (measured scope).
    analyzed = [r for r in rows if r["unique_issues"] is not None]
    loc = sum(r["measured_loc"] or 0 for r in analyzed)
    unique = sum(r["unique_issues"] for r in analyzed if r["measured_loc"])
    return {
        "deploy_rate": _share(r["deployed"] for r in rows),
        "pass_rate": _mean(r["pass_rate"] for r in rows),
        "functional_passed": _share(r["functional_passed"] for r in rows),
        "unique_issues_per_app": _mean(r["unique_issues"] for r in analyzed),
        "issues_per_kloc": round(unique * 1000 / loc, 4) if loc else None,
        "security_per_app": _mean(r["security_unique"] for r in analyzed),
        "cost_per_app": _mean(r["cost"] for r in rows if r["generated"]),
    }


def _group(rows, key):
    groups: dict[Any, list] = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return dict(sorted(groups.items(), key=lambda kv: str(kv[0])))


# ── sections ─────────────────────────────────────────────────────────────


def _rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": inf.wilson_interval(k, n)}


def _funnel_row(rows) -> dict[str, Any]:
    generated = [r for r in rows if r["generated"]]
    evaluated = [r for r in rows if r["evaluated"]]
    deployed = [r for r in evaluated if r["deployed"]]
    passed = [r for r in evaluated if r["functional_passed"]]
    return {
        "jobs": len(rows),
        "generated": len(generated),
        "truncated": sum(1 for r in generated if r["truncated"]),
        "analyzed": sum(1 for r in rows if r["analyzed"]),
        "static_measured": sum(1 for r in rows if r["static_measured"]),
        "evaluated": len(evaluated),
        "deployed": len(deployed),
        "functionally_correct": len(passed),
        "generation_rate": _rate(len(generated), len(rows)),
        "deploy_rate": _rate(len(deployed), len(evaluated)),
        "functional_rate": _rate(len(passed), len(evaluated)),
        # Of the apps that run, how many do what was asked.
        "functional_given_deployed": _rate(len(passed), len(deployed)),
        "mean_pass_rate": _mean(r["pass_rate"] for r in evaluated),
        "mean_pass_rate_deployed": _mean(r["pass_rate"] for r in deployed),
    }


def funnel_section(rows) -> dict[str, Any]:
    return {
        "overall": _funnel_row(rows),
        "by_model": [{"model": k, **_funnel_row(v)} for k, v in _group(rows, "model").items()],
        "by_template": [{"template": k, **_funnel_row(v)} for k, v in _group(rows, "template").items()],
    }


def security_bounds_section(rows) -> dict[str, Any]:
    # Apps without an available static measurement are unknown, not "no issues".
    analyzed = [r for r in rows if r["static_measured"]]
    n = len(analyzed)

    def severity_rows(items):
        out = []
        for s in SEVERITIES:
            issues = sum(r[f"unique_{s}"] or 0 for r in items)
            apps = sum(1 for r in items if (r[f"unique_{s}"] or 0) > 0)
            out.append(
                {
                    "severity": s,
                    "issues": issues,
                    "apps_affected": apps,
                    "apps": len(items),
                    "apps_affected_ci95": inf.wilson_interval(apps, len(items)),
                    # Zero is not "none": with n apps the rate is only bounded.
                    "zero_rate_upper95": inf.zero_count_upper_bound(len(items)) if apps == 0 else None,
                },
            )
        return out

    by_model = []
    for model, items in _group(analyzed, "model").items():
        sev = {r["severity"]: r for r in severity_rows(items)}
        by_model.append(
            {
                "model": model,
                "apps": len(items),
                "critical_high_issues": sev["critical"]["issues"] + sev["high"]["issues"],
                "apps_with_critical_or_high": sum(
                    1 for r in items if (r["unique_critical"] or 0) + (r["unique_high"] or 0) > 0
                ),
                "zero_critical_upper95": sev["critical"]["zero_rate_upper95"],
                "zero_high_upper95": sev["high"]["zero_rate_upper95"],
            },
        )
    return {"apps_analyzed": n, "overall": severity_rows(analyzed), "by_model": by_model}


def _app_correlation(rows, x, y, seed) -> dict[str, Any]:
    pairs = [(r[x], r[y]) for r in rows if r[x] is not None and r[y] is not None]
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    return {
        "x": x,
        "y": y,
        "n_apps": len(pairs),
        "rho": inf.spearman(xs, ys),
        "p_permutation": inf.spearman_permutation_p(xs, ys, seed=seed, draws=2000),
        "ci95": inf.spearman_bootstrap_ci(xs, ys, seed=seed, draws=1000),
    }


def code_metrics_section(rows, seed: int) -> dict[str, Any]:
    generated = [r for r in rows if r["generated"]]

    def summary(items):
        return {
            "apps": len(items),
            "loc": inf.describe([r["loc"] for r in items]),
            "measured_loc": inf.describe([r["measured_loc"] for r in items]),
            "loc_backend": inf.describe([r["loc_backend"] for r in items]),
            "loc_frontend": inf.describe([r["loc_frontend"] for r in items]),
            "files": inf.describe([r["files"] for r in items]),
            "backend_share": inf.describe([r["backend_share"] for r in items if r["backend_share"] is not None]),
            "avg_complexity": inf.describe([r["avg_complexity"] for r in items if r["avg_complexity"] is not None]),
            "max_complexity": inf.describe([r["max_complexity"] for r in items if r["max_complexity"] is not None]),
        }

    spread = []
    for template, items in _group(generated, "template").items():
        per_model = {m: _mean(r["loc"] for r in v) for m, v in _group(items, "model").items()}
        cx = {m: _mean(r["avg_complexity"] for r in v) for m, v in _group(items, "model").items()}
        spread.append(
            {
                "template": template,
                "models": len(per_model),
                "loc_cv_across_models": inf.coefficient_of_variation([v for v in per_model.values() if v is not None]),
                "complexity_cv_across_models": inf.coefficient_of_variation([v for v in cx.values() if v is not None]),
                "loc_min_model": min(known, key=lambda m: known[m]) if (known := _known(per_model)) else None,
                "loc_max_model": max(known, key=lambda m: known[m]) if known else None,
            },
        )
    analyzed = [r for r in generated if r["static_measured"]]
    return {
        "overall": summary(generated),
        "by_model": [{"model": k, **summary(v)} for k, v in _group(generated, "model").items()],
        "by_template": [{"template": k, **summary(v)} for k, v in _group(generated, "template").items()],
        "spread_across_models": spread,
        # App-level, so n = apps; apps of one model are not independent.
        "app_level_correlations": [
            _app_correlation(analyzed, "measured_loc", "unique_issues", seed),
            _app_correlation(analyzed, "avg_complexity", "issues_per_kloc", seed),
            _app_correlation([r for r in generated if r["evaluated"]], "loc", "pass_rate", seed),
        ],
        "note": "App-level correlations treat apps as independent; apps of one model or template are clustered.",
    }


def _known(values: dict[Any, float | None]) -> dict[Any, float]:
    return {k: v for k, v in values.items() if v is not None}


def _model_parameters(jobs) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    for job in jobs:
        m = job.model
        if m is None or m.model_id in params:
            continue
        caps = m.capabilities_json if isinstance(m.capabilities_json, dict) else {}
        nominal = caps.get("context_length") or m.context_window or None
        effective = m.num_ctx_override or m.context_window or nominal
        params[m.model_id] = {
            "context_window_nominal": float(nominal) if nominal else None,
            "context_window_effective": float(effective) if effective else None,
            "max_output_tokens": float(m.max_output_tokens) if m.max_output_tokens else None,
            "input_price_per_mtok": round(m.input_price_per_token * 1e6, 4) if m.input_price_per_token else None,
            "output_price_per_mtok": round(m.output_price_per_token * 1e6, 4) if m.output_price_per_token else None,
        }
    return params


PREDICTORS = (
    "context_window_nominal",
    "context_window_effective",
    "max_output_tokens",
    "input_price_per_mtok",
    "output_price_per_mtok",
)
OUTCOMES = ("deploy_rate", "pass_rate", "issues_per_kloc", "security_per_app")


def correlations_section(params, criteria_by_model, seed: int) -> dict[str, Any]:
    models = sorted(set(params) & set(criteria_by_model))
    tests = []
    for predictor in PREDICTORS:
        for outcome in OUTCOMES:
            pairs = [
                (params[m][predictor], criteria_by_model[m][outcome], m)
                for m in models
                if params[m][predictor] is not None and criteria_by_model[m][outcome] is not None
            ]
            xs, ys, labels = [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs]
            tests.append(
                {
                    "predictor": predictor,
                    "outcome": outcome,
                    "n_models": len(pairs),
                    "rho": inf.spearman(xs, ys),
                    "p_permutation": inf.spearman_permutation_p(xs, ys, seed=seed),
                    "ci95_bootstrap": inf.spearman_bootstrap_ci(xs, ys, seed=seed),
                    "leave_one_out": inf.leave_one_out(xs, ys, labels),
                },
            )
    p_values = [t["p_permutation"] for t in tests]
    for t, holm_p, bh_q in zip(tests, inf.holm(p_values), inf.benjamini_hochberg(p_values), strict=True):
        t["p_holm"] = holm_p
        t["q_bh"] = bh_q
    differs = sorted(
        m
        for m in models
        if params[m]["context_window_nominal"]
        and params[m]["context_window_effective"]
        and params[m]["context_window_nominal"] != params[m]["context_window_effective"]
    )
    return {
        "status": "exploratory",
        "family_size": sum(1 for p in p_values if p is not None),
        "n_models": len(models),
        "tests": tests,
        "model_parameters": [{"model": m, **params[m]} for m in models],
        "effective_context_differs": differs,
        "notes": [
            "Observational, model-level (n = number of models): association, not cause.",
            "Predictors are not independent (larger, newer models tend to have larger windows and prices).",
            (
                "p_holm controls the familywise error across all predictor x outcome tests; "
                "q_bh the false discovery rate."
            ),
            "leave_one_out.sign_flips marks a correlation that rests on a single model.",
        ],
    }


def _resampler(rows_by_model):
    def resample(rng: random.Random) -> dict[str, dict[str, float | None]]:
        out = {}
        for model, rows in rows_by_model.items():
            # Resample the model's apps (templates), keeping their repeats together.
            by_app = defaultdict(list)
            for r in rows:
                by_app[r["app_id"]].append(r)
            apps = list(by_app.values())
            draw = [row for _ in apps for row in rng.choice(apps)]
            out[model] = model_criteria(draw)
        return out

    return resample


def planned_comparisons_section(rows, experiment: Experiment | None, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Every plan metric, each condition vs the pre-registered baseline, blocked on the app."""
    if experiment is None or not plan or experiment.baseline_condition_id is None:
        return None
    metrics = [plan["primary_metric"], *plan.get("secondary_metrics", [])]
    by_condition = _group([r for r in rows if r["condition_id"] is not None], "condition_id")
    base_rows = by_condition.get(experiment.baseline_condition_id, [])
    alpha = plan.get("alpha", 0.05)
    correction = plan.get("correction", "holm")
    labels = {
        c.id: c.label or str(c) for c in experiment.conditions.select_related("model", "profile", "harness_preset")
    }

    def per_app(items, metric):
        out = {}
        for app, rs in _group(items, "app_id").items():
            value = model_criteria(rs)[metric]
            if value is not None:
                out[app] = value
        return out

    families = []
    for metric in metrics:
        base = per_app(base_rows, metric)
        comparisons = []
        for cid, items in by_condition.items():
            if cid == experiment.baseline_condition_id:
                continue
            other = per_app(items, metric)
            common = sorted(set(base) & set(other))
            deltas = [other[a] - base[a] for a in common]
            wins = sum(1 for d in deltas if d > 0)
            losses = sum(1 for d in deltas if d < 0)
            comparisons.append(
                {
                    "condition_id": cid,
                    "condition": labels.get(cid, str(cid)),
                    "n_pairs": len(common),
                    "mean_delta": _mean(deltas),
                    "median_delta": inf.percentile(deltas, 0.5),
                    "wins": wins,
                    "losses": losses,
                    "p_sign": round(sign_test_p(wins, losses), 4) if sign_test_p(wins, losses) is not None else None,
                    "p_wilcoxon": round(wilcoxon_p(deltas), 4) if wilcoxon_p(deltas) is not None else None,
                },
            )
        ps = [c["p_wilcoxon"] for c in comparisons]
        adjusted = inf.holm(ps) if correction == "holm" else inf.benjamini_hochberg(ps) if correction == "bh" else ps
        for c, adj in zip(comparisons, adjusted, strict=True):
            c["p_adjusted"] = adj
            c["significant"] = adj is not None and adj < alpha
        families.append(
            {
                "metric": metric,
                "role": "primary" if metric == plan["primary_metric"] else "secondary",
                "status": "confirmatory",
                "comparisons": comparisons,
            },
        )
    return {
        "baseline_condition_id": experiment.baseline_condition_id,
        "alpha": alpha,
        "correction": correction,
        "interim": experiment.status
        not in (Experiment.Status.COMPLETED, Experiment.Status.CANCELLED, Experiment.Status.ARCHIVED),
        "families": families,
    }


def _fingerprint(rows) -> str:
    payload = json.dumps(
        sorted(
            [r["job_id"], r["status"], r["evaluation_status"], r["analysis_run_id"], r["unique_issues"]] for r in rows
        ),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _analysis_plan_block(plan_hash: str | None, coverage: dict[str, Any]) -> dict[str, Any]:
    """The frozen analysis plan the issue tables read: tools + versions, reviewer, image, engine versions."""
    plan = {}
    if plan_hash:
        plan = (
            AnalysisRun.objects.filter(plan_hash=plan_hash)
            .exclude(purpose=AnalysisRun.Purpose.CANARY)
            .order_by("-created_at")
            .values_list("plan", flat=True)
            .first()
        ) or {}
    reviewer = plan.get("reviewer") if isinstance(plan.get("reviewer"), dict) else {}
    model = reviewer.get("model") if isinstance(reviewer.get("model"), dict) else {}
    return {
        "plan_hash": plan_hash,
        "plan_rule": coverage.get("plan_rule"),
        "profile": plan.get("profile"),
        "tools": [
            {
                "slug": t.get("slug"),
                "version": t.get("version") or "",
                "kind": t.get("kind"),
                "category": t.get("category"),
                "required": t.get("required"),
                "config_hash": t.get("config_hash"),
            }
            for t in plan.get("tools") or []
            if isinstance(t, dict)
        ],
        "reviewer": None
        if not reviewer.get("enabled")
        else {
            "engine": reviewer.get("engine"),
            "harness": reviewer.get("harness"),
            "model": model.get("canonical_slug") or model.get("slug"),
            "samples": reviewer.get("samples"),
            "prompt_sha256": reviewer.get("prompt_sha256"),
        },
        "analyzer_image": (plan.get("analyzer_image") or {}).get("tag"),
        "engine_versions": plan.get("engine_versions") or {},
        "mapping_hashes": plan.get("mapping_hashes") or {},
    }


def protocol_section(
    jobs,
    rows,
    experiment: Experiment | None,
    config: dict[str, Any],
    *,
    issues: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = (issues or {}).get("coverage") or {}
    analysis = _analysis_plan_block((issues or {}).get("plan_hash"), coverage)
    evaluators = Counter()
    for job in jobs:
        ev = (job.result_data or {}).get("evaluation") if isinstance(job.result_data, dict) else None
        if isinstance(ev, dict) and ev.get("tester_sha256"):
            evaluators[
                f"v{ev.get('evaluator_version')}/tester-{ev.get('tester_version')}/{ev['tester_sha256'][:12]}"
            ] += 1
    repeats = Counter(len(v) for v in _group(rows, "model").values())
    cells = Counter((r["model"], r["template"]) for r in rows)
    return {
        "report_version": REPORT_VERSION,
        "generated_at": timezone.now().isoformat(),
        "scope": {k: config.get(k) for k in ("experiment_id", "batch_id")},
        "experiment": None
        if experiment is None
        else {
            "id": str(experiment.id),
            "name": experiment.name,
            "hypothesis": experiment.hypothesis,
            "status": experiment.status,
            "mode": experiment.mode,
            "repeats": experiment.repeats,
            "base_seed": experiment.base_seed,
            "baseline_condition_id": experiment.baseline_condition_id,
            "build_healing": experiment.build_healing,
            "analysis_plan": experiment.analysis_plan or None,
            "analysis_plan_hash": experiment.analysis_plan_hash or None,
            "analysis_plan_frozen_at": experiment.analysis_plan_frozen_at.isoformat()
            if experiment.analysis_plan_frozen_at
            else None,
        },
        "jobs": len(rows),
        "models": sorted({r["model"] for r in rows if r["model"]}),
        "templates": sorted({r["template"] for r in rows if r["template"]}),
        "modes": dict(Counter(r["mode"] for r in rows)),
        "stacks": dict(Counter(r["stack"] or "(default)" for r in rows)),
        "build_healing": dict(Counter(r["build_healing"] for r in rows)),
        "prompt_versions": len({r["prompt_hash"] for r in rows if r["prompt_hash"]}),
        "apps_per_model": dict(repeats),
        "trials_per_cell": dict(Counter(cells.values())),
        # The analysis plan's tools with the versions it froze (from the analyzer image manifest).
        "tools": [{"slug": t["slug"], "version": t["version"]} for t in analysis["tools"]],
        "analysis_plan": analysis,
        "evaluators": dict(evaluators),
        "settings": {
            "include_ai": bool(config.get("include_ai")),
            "line_tolerance": int(config.get("line_tolerance", 2)),
            "exclude_truncated": bool(config.get("exclude_truncated", True)),
            "plan_hash": config.get("plan_hash") or None,
            "seed": int(config.get("seed", 0)),
        },
        "data_fingerprint": _fingerprint(rows),
    }


def validity_section(
    rows,
    *,
    protocol,
    issues,
    healing,
    accounting,
    correlations,
    security,
    plan,
    config,
) -> list[dict[str, Any]]:
    """Threats to validity detected in this data (``level``: warning | info)."""
    out = []

    def add(code, level, section, message):
        out.append({"code": code, "level": level, "section": section, "message": message})

    n_models = len(protocol["models"])
    if n_models and n_models < SMALL_N_MODELS:
        add(
            "small_n_models",
            "warning",
            "correlations, ranking",
            f"Only {n_models} model{'' if n_models == 1 else 's'}: "
            "model-level correlations and rankings are exploratory.",
        )
    by_model = _group(rows, "model")
    thin = sorted(m for m, v in by_model.items() if sum(1 for r in v if r["evaluated"]) < MIN_APPS_PER_MODEL)
    if thin:
        add(
            "few_apps",
            "warning",
            "funnel, ranking",
            f"Fewer than {MIN_APPS_PER_MODEL} evaluated apps for: {', '.join(thin)}.",
        )
    counts = {len(v) for v in by_model.values()}
    if len(counts) > 1:
        add("unbalanced", "warning", "all", "Models have unequal numbers of apps; compare rates, not totals.")
    truncated = protocol.get("truncated_excluded", 0) + sum(1 for r in rows if r["truncated"])
    if truncated:
        add(
            "truncated",
            "warning",
            "all",
            f"{truncated} generation(s) hit max_tokens"
            + (" and were excluded." if config.get("exclude_truncated", True) else "; their code is incomplete."),
        )
    infra = sum(1 for r in rows if r["evaluation_status"] == "infra_error")
    if infra:
        add(
            "infra_errors",
            "info",
            "funnel",
            f"{infra} evaluation(s) failed for platform reasons and are excluded; re-evaluate them.",
        )
    not_evaluated = sum(1 for r in rows if r["generated"] and not r["evaluated"])
    if not_evaluated:
        add(
            "not_evaluated",
            "warning",
            "funnel",
            f"{not_evaluated} generated app(s) have no evaluation; deploy rates exclude them.",
        )
    if protocol["prompt_versions"] > len(protocol["templates"] or [1]):
        add(
            "mixed_prompts",
            "warning",
            "all",
            "Some templates ran under more than one prompt version; results are pooled across them.",
        )
    if len(protocol["modes"]) > 1:
        add(
            "mixed_modes",
            "warning",
            "all",
            f"Generation modes are pooled ({', '.join(sorted(protocol['modes']))}); "
            "scope the report to one experiment.",
        )
    if len(protocol["stacks"]) == 1:
        add(
            "single_stack",
            "info",
            "all",
            f"One stack only ({next(iter(protocol['stacks']))}): results may not generalise to others.",
        )
    healed = [r for r in rows if r["healing_changed"]]
    if healed:
        evaluated = sum(1 for r in rows if r["evaluated"])
        msg = f"Build healing changed {len(healed)} of {evaluated} evaluated apps before deployment."
        if "none" not in healing.get("modes", []):
            msg += " No unhealed ablation exists: run manage.py healing_ablation to separate model from pipeline."
        add("healing", "warning", "funnel, ranking", msg)
    zero = [s["severity"] for s in security["overall"] if s["severity"] in ("critical", "high") and s["issues"] == 0]
    if zero and security["apps_analyzed"]:
        bound = inf.zero_count_upper_bound(security["apps_analyzed"])
        add(
            "zero_severe",
            "warning",
            "security_bounds",
            f"No {' or '.join(zero)} issues in {security['apps_analyzed']} apps: the per-app rate is bounded at "
            f"{bound:.1%} (95%), not zero; the templates may not exercise these classes.",
        )
    add(
        "ai_separate",
        "info",
        "issues",
        "AI-reviewer findings are never part of static issue counts (opinions, not measurements); "
        "their consensus is reported in the ai_* columns.",
    )
    coverage = issues.get("coverage") or {}
    unmeasured = {
        key: coverage.get(key, 0)
        for key in ("not_analysed", "analysis_failed", "in_progress", "incomplete", "other_plan", "excluded")
        if coverage.get(key)
    }
    if unmeasured:
        add(
            "not_measured",
            "warning",
            "issues, security_bounds, ranking",
            f"{sum(unmeasured.values())} app(s) have no measurement under the analysis plan ("
            + ", ".join(f"{k}: {v}" for k, v in unmeasured.items())
            + "); issue tables leave them out rather than count them as clean.",
        )
    if coverage.get("mixed_plans"):
        add(
            "mixed_analysis_plans",
            "info",
            "issues",
            f"Apps were analysed under {len(coverage.get('plans') or {})} analysis plans; only runs of plan "
            f"{(coverage.get('plan_hash') or '')[:12]} ({coverage.get('plan_rule')}) are compared. "
            "Pin plan_hash to choose another.",
        )
    static_missing = sum(1 for r in rows if r["analyzed"] and not r["static_measured"])
    if static_missing:
        add(
            "static_unavailable",
            "warning",
            "issues, security_bounds",
            f"{static_missing} analysed app(s) have no static measurement (a required tool failed); "
            "their issue counts are missing, not zero.",
        )
    if any(r["ai_cost_complete"] is False for r in rows):
        add(
            "ai_cost_incomplete",
            "info",
            "accounting",
            "The AI review cost of some apps is a lower bound (a replicate's spend is unknown).",
        )
    failed = sum(t["failed"] + t["timed_out"] for t in accounting["tools"]["latest"])
    if failed:
        add(
            "tool_failures",
            "warning",
            "issues",
            f"{failed} tool execution(s) failed or timed out; a failed required tool makes that app's "
            "static measurement unavailable (missing data), an optional one is listed per run.",
        )
    if correlations["effective_context_differs"]:
        add(
            "effective_context",
            "warning",
            "correlations",
            "Effective (endpoint-limited) context differs from the nominal one for: "
            + ", ".join(correlations["effective_context_differs"])
            + ". Both are reported; do not cite one without the other.",
        )
    unstable = [t for t in correlations["tests"] if t["leave_one_out"].get("sign_flips")]
    if unstable:
        add(
            "loo_unstable",
            "warning",
            "correlations",
            f"{len(unstable)} correlation(s) change sign when one model is removed.",
        )
    if issues["totals"]["raw_findings"] and issues["totals"]["duplicate_findings"]:  # both None-safe
        share = issues["totals"]["duplicate_findings"] / issues["totals"]["raw_findings"]
        add(
            "duplicates",
            "info",
            "issues",
            f"{share:.0%} of raw findings were duplicates of another tool's report and count once.",
        )
    if not plan:
        add("no_plan", "info", "all", "No pre-registered analysis plan: every analysis here is exploratory.")
    return out


# ── generator ────────────────────────────────────────────────────────────


def _is_truncated(job: GenerationJob) -> bool:
    data = job.result_data if isinstance(job.result_data, dict) else {}
    return any(data.get(f"{p}_truncated") for p in ("backend", "frontend"))


def _experiment(config, user) -> Experiment | None:
    if not config.get("experiment_id"):
        return None
    from backend.reports.services.generators import owned_experiment

    return owned_experiment(config["experiment_id"], user)


def generate_study_report(config: dict[str, Any], *, user=None) -> dict[str, Any]:
    experiment = _experiment(config, user)
    seed = int(config.get("seed", 0))
    include_ai = bool(config.get("include_ai", False))
    line_tolerance = int(config.get("line_tolerance", 2))
    jobs_qs = study_jobs(user, experiment_id=config.get("experiment_id"), batch_id=config.get("batch_id"))
    jobs = list(jobs_qs)
    total_jobs = len(jobs)
    if config.get("exclude_truncated", True):
        # Truncated output measures the token ceiling, not the model.
        jobs = [j for j in jobs if not _is_truncated(j)]
        jobs_qs = jobs_qs.filter(id__in=[j.id for j in jobs])
    plan = (experiment.analysis_plan if experiment else None) or {}

    rows, issues = app_table(
        jobs,
        include_ai=include_ai,
        line_tolerance=line_tolerance,
        plan_hash=config.get("plan_hash") or None,
        rules=_rules(config),
    )

    by_model = _group([r for r in rows if r["model"]], "model")
    criteria_by_model = {m: model_criteria(v) for m, v in by_model.items()}
    criteria = criteria_from_config(config.get("ranking_criteria") or plan.get("ranking_criteria"))
    ranking = rank_models(
        {m: {c.key: criteria_by_model[m][c.key] for c in criteria} for m in criteria_by_model},
        criteria,
        seed=seed,
        resample=_resampler(by_model) if config.get("bootstrap", True) else None,
    )
    declared = declared_metrics(plan)
    ranking["status"] = (
        "confirmatory" if plan.get("ranking_criteria") and not config.get("ranking_criteria") else "exploratory"
    )

    correlations = correlations_section(_model_parameters(jobs), criteria_by_model, seed)
    security = security_bounds_section(rows)
    healing = healing_impact(jobs_qs)
    accounting = run_accounting(jobs_qs, run_ids=[r["analysis_run_id"] for r in rows if r["analysis_run_id"]])
    protocol = protocol_section(jobs, rows, experiment, config, issues=issues)
    protocol["truncated_excluded"] = total_jobs - len(jobs)

    issues_out = {k: v for k, v in issues.items() if k not in ("issues", "per_app", "ai_issues")}
    return {
        "protocol": protocol,
        "definitions": DEFINITIONS,
        "metric_definitions": PLAN_METRICS,
        "funnel": funnel_section(rows),
        "model_outcomes": [
            {"model": m, "apps": len(by_model[m]), **c, "status": "confirmatory" if declared else "exploratory"}
            for m, c in criteria_by_model.items()
        ],
        "issues": issues_out,
        "security_bounds": security,
        "code_metrics": code_metrics_section(rows, seed),
        "correlations": correlations,
        "ranking": ranking,
        "planned_comparisons": planned_comparisons_section(rows, experiment, plan),
        "healing": {
            k: healing[k]
            for k in ("baseline", "modes", "not_applicable", "totals", "by_model", "added_requirements", "patches")
        },
        "tool_inventory": tool_inventory()["counts"],
        "accounting": {k: accounting[k] for k in ("apps", "analysis_runs", "executions", "evaluations")},
        "per_app": rows,
        "validity": validity_section(
            rows,
            protocol=protocol,
            issues=issues,
            healing=healing,
            accounting=accounting,
            correlations=correlations,
            security=security,
            plan=plan,
            config=config,
        ),
    }
