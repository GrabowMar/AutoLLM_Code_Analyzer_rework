"""Build the rankings list: the MSS v2 frame (one row per canonical model) plus local stats.

Rows are (a) one per canonical model of the MSS v2 frame, keyed by its catalog
(anchor) row's ``model_id`` and carrying ``canonical_id``, and (b) every other
``LLMModel`` with compared runs (route copies, dated members), whose MSS
fields are inherited from its canonical model (``mss_inherited_from``).
Empirical quality and the composite stay per row: rows are never merged and
statistics are never pooled across rows.
"""

from __future__ import annotations

import datetime as dt
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
from backend.rankings.services.constants import SEVERITY_WEIGHTS
from backend.rankings.services.scoring import compute_composite
from backend.rankings.services.scoring import compute_empirical_quality
from backend.statistics.services.runs import study_current_runs

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

_ZERO_ROLLUP = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
# Usable outcomes that never reached the tests: the model's failure, 0 pass (intention-to-treat).
_MODEL_FAILURES = frozenset({Outcome.BUILD_FAIL, Outcome.START_FAIL, Outcome.OUTPUT_INVALID})


def _is_test_path(path: str) -> bool:
    """``reports.services.loc.is_outside_app``: test code and, under the default scope, scratch scripts
    (the study tables' rule; imported per call, like ``tool_coverage``)."""
    from backend.reports.services.loc import is_outside_app

    return is_outside_app(path)


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


def mss_context(as_of: dt.date | None = None) -> dict[str, Any]:
    """The MSS v2 frame scored on the nominal specification (read-only; no network).

    Returns ``{"as_of", "inputs", "scored": {canonical_id: row}, "member_of":
    {model_id: canonical_id}, "tiers": {canonical_id: tier}, "tier_info"}``.
    """
    from django.utils import timezone

    from backend.rankings.services.mss import score_frame
    from backend.rankings.services.selection import assign_tiers
    from backend.rankings.services.study_selection import build_inputs

    as_of = as_of or timezone.now().date()
    inputs = build_inputs(as_of)
    scored = score_frame(inputs["frame"]["candidates"], as_of=as_of, market=inputs.get("market"))
    member_of: dict[str, str] = {}
    for row in scored:
        for member in row.get("members") or []:
            member_of[member["model_id"]] = row["canonical_id"]
    eligible = {r["canonical_id"]: r["raw"]["run_cost_usd"] for r in scored if r["eligible"]}
    tiers, tier_info = assign_tiers(eligible) if eligible else ({}, {})
    return {
        "as_of": as_of.isoformat(),
        "inputs": inputs,
        "scored": {r["canonical_id"]: r for r in scored},
        "member_of": member_of,
        "tiers": tiers,
        "tier_info": tier_info,
    }


def preferred_route(routes: dict[str, Any], vendor: str) -> str | None:
    """Claude on the Anthropic key, else OpenCode Go when served, else OpenRouter (no budgets here)."""
    paid = {k for k, v in (routes or {}).items() if v.get("paid")}
    if vendor == "anthropic" and "anthropic" in paid:
        return "anthropic"
    for kind in ("go", "openrouter", "anthropic"):
        if kind in paid:
            return kind
    return None


def _mss_fields(r: dict[str, Any] | None, tiers: dict[str, str]) -> dict[str, Any]:
    """The MSS v2 fields of one rankings row (all None when the model is not in the frame)."""
    if r is None:
        return {
            "canonical_id": None,
            "vendor": None,
            "origin": None,
            "open_weights": None,
            "release_date": None,
            "pinned_version": None,
            "routes": {},
            "preferred_route": None,
            "eligible": False,
            "exclusion": {
                "step": "K",
                "code": "not_in_frame",
                "detail": "router, alias, :batch or unlinked local row",
            },
            "popularity_score": None,
            "price_score": None,
            "capability_score": None,
            "recency_score": None,
            "mss_score": None,
            "mss_coverage": None,
            "mss_missing": [],
            "mss_flags": [],
            "run_cost_usd": None,
            "planning_cost_usd": None,
            "price_tier": None,
            "popularity": None,
            "webdev_rating": None,
            "list_price": None,
        }
    raw = r.get("raw") or {}
    pop = raw.get("popularity") or None
    webdev = raw.get("webdev") or None
    price = (r.get("inputs") or {}).get("list_price") or None
    scores = r.get("scores") or {}
    return {
        "canonical_id": r["canonical_id"],
        "vendor": r.get("vendor"),
        "origin": r.get("origin"),
        "open_weights": r.get("open_weights"),
        "release_date": r.get("release_date"),
        "line_key": r.get("line_key"),
        "pinned_version": r.get("pinned_version"),
        "routes": {
            kind: {k: v.get(k) for k in ("model_id", "available", "paid", "prerelease", "served_name", "permaslug")}
            for kind, v in (r.get("routes") or {}).items()
        },
        "preferred_route": preferred_route(r.get("routes") or {}, r.get("vendor") or ""),
        "eligible": bool(r.get("eligible")),
        "exclusion": r.get("exclusion"),
        "popularity_score": scores.get("popularity"),
        "price_score": scores.get("price"),
        "capability_score": scores.get("capability"),
        "recency_score": scores.get("recency"),
        "mss_score": r.get("mss"),
        "mss_coverage": r.get("coverage") if r.get("mss") is not None else None,
        "mss_missing": r.get("missing") or [],
        "mss_flags": r.get("flags") or [],
        "run_cost_usd": raw.get("run_cost_usd"),
        "planning_cost_usd": raw.get("planning_cost_usd"),
        "price_tier": tiers.get(r["canonical_id"]),
        "popularity": (
            {
                "source": raw.get("popularity_source"),
                "definition": pop.get("definition"),
                "volume_share": pop.get("volume_share"),
                "value_share": pop.get("value_share"),
                "days_available": pop.get("days_available"),
                "days_listed": pop.get("days_listed"),
                "days_censored": pop.get("days_censored"),
                "censored": pop.get("censored"),
                "short_exposure": pop.get("short_exposure"),
                "window": pop.get("window"),
                "as_of": pop.get("as_of"),
            }
            if pop
            else ({"source": raw.get("popularity_source")} if raw.get("popularity_source") else None)
        ),
        "webdev_rating": (
            {
                k: webdev.get(k)
                for k in (
                    "rating",
                    "rating_lower",
                    "rating_upper",
                    "arena_name",
                    "publish_date",
                    "match",
                    "from_model",
                )
            }
            if webdev
            else None
        ),
        "list_price": (
            {
                k: price.get(k)
                for k in (
                    "input",
                    "output",
                    "cache_read",
                    "cache_write",
                    "unit",
                    "source",
                    "fetched_at",
                    "discount_undone",
                )
            }
            if price
            else None
        ),
    }


def aggregate_rankings(
    user: AbstractBaseUser | None = None,
    *,
    prompt_hash: str | None = None,
    config_fingerprint: str | None = None,
    study_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the ranking rows: the MSS v2 frame plus locally measured quality.

    With ``user`` given, local stats (apps, findings, empirical scores) are
    scoped to that user's generation runs; the MSS fields are global either
    way. ``prompt_hash``/``config_fingerprint``/``study_id`` further narrow
    local stats to runs generated from one prompt version, one configuration
    or one study: comparisons across models are only apples-to-apples when
    they used the same prompt material. ``context`` is a precomputed
    :func:`mss_context` (tests, callers that already built one).

    A model without MSS (insufficient evidence, not in the frame) has no
    composite either; rows are sorted by composite with those last.
    """

    local = _local_app_stats(
        user,
        prompt_hash=prompt_hash,
        config_fingerprint=config_fingerprint,
        study_id=study_id,
    )
    ctx = context or mss_context()
    scored, tiers, member_of = ctx["scored"], ctx["tiers"], ctx["member_of"]
    models = {m.model_id: m for m in LLMModel.objects.all()}

    def base_entry(m: LLMModel, local_row: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_id": m.model_id,
            "model_name": m.model_name,
            "provider": m.provider,
            "is_free": m.is_free,
            "context_length": m.context_window,
            # The catalog's cheapest-endpoint price (reference only; MSS uses the list price).
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

    def finish(entry: dict[str, Any]) -> dict[str, Any]:
        entry["empirical_quality_score"] = compute_empirical_quality(entry)
        # MSS blended with locally measured quality; models never exercised
        # here keep composite == MSS, models without MSS have no composite.
        entry["composite_score"] = compute_composite(entry["mss_score"], entry["empirical_quality_score"])
        return entry

    rows: list[dict[str, Any]] = []
    anchors: dict[str, str] = {}
    for canon, r in scored.items():
        m = models.get(r["anchor_model_id"])
        if m is None:
            continue
        anchors[canon] = m.model_id
        entry = base_entry(m, local.get(m.model_id, {}))
        entry.update(_mss_fields(r, tiers))
        entry["display_name"] = r.get("display_name")
        entry["context_length"] = r.get("context_window") or m.context_window
        entry["members"] = [mm["model_id"] for mm in r.get("members") or []]
        entry["mss_inherited_from"] = None
        rows.append(finish(entry))
    anchor_ids = set(anchors.values())
    for model_id, local_row in local.items():
        if model_id in anchor_ids or model_id not in models:
            continue
        m = models[model_id]
        entry = base_entry(m, local_row)
        canon = member_of.get(model_id)
        r = scored.get(canon) if canon else None
        entry.update(_mss_fields(r, tiers))
        entry["mss_inherited_from"] = anchors.get(canon) if r is not None else None
        rows.append(finish(entry))

    rows.sort(key=lambda r: (r.get("composite_score") is None, -(r.get("composite_score") or 0.0), r["model_id"]))
    return rows


def mss_sources(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Provenance of every MSS v2 input: as_of, fetched_at, window, license, citation and coverage."""
    from backend.rankings.services.study_selection import sources_for

    ctx = context or mss_context()
    scored = list(ctx["scored"].values())
    eligible = [r for r in scored if r["eligible"]]
    frame_ok = [r for r in scored if r.get("exclusion") is None or (r.get("exclusion") or {}).get("step") == "E8"]

    def count(rows: list[dict[str, Any]], component: str) -> int:
        return sum(1 for r in rows if (r.get("scores") or {}).get(component) is not None)

    return {
        "as_of": ctx["as_of"],
        "sources": sources_for(ctx["inputs"], scored),
        "coverage": {
            "canonical_models": len(scored),
            "passing_filters_e1_e7": len(frame_ok),
            "eligible": len(eligible),
            "with_popularity": count(frame_ok, "popularity"),
            "with_price": count(frame_ok, "price"),
            "with_capability": count(frame_ok, "capability"),
            "with_recency": count(frame_ok, "recency"),
            "popularity_proxy": sum(1 for r in frame_ok if "lmarena_proxy" in (r.get("flags") or [])),
        },
        "tiers": ctx["tier_info"],
        "flow": [*ctx["inputs"]["frame"]["flow"]],
    }
