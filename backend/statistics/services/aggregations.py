"""Aggregation queries for KPIs, severity, model comparison, and tools.

Analysis numbers are deduplicated to one measured run per job and read from
its stored measurement (``rankings.services.measured.descriptive_runs``):
runs are never counted twice, a narrower re-run (AI-only review, quick
check) never hides a job's full static measurement, and AI opinions are
totalled apart. A total over no measured job is None (no data), never 0:
``total_findings`` needs a job with an available static measurement,
``total_ai_findings`` a job with a successful AI review.

Tool effectiveness (the Statistics page's tool card) describes tool
*behaviour*, not code quality: per tool, over each job's latest finished
*or failed* v2 run (cancelled runs and self-tests excluded), how often it
ran, succeeded, failed or had nothing to scan, and what it reported when it
succeeded. A failed execution is never a "scan", so a failure that failed
the whole run still counts against the tool and ``avg_per_scan`` is None
(not 0) for a tool that never scanned anything.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Min
from django.db.models import Q

from backend.analysis.engine import canary as canary_engine
from backend.analysis.engine import sandbox as sandbox_engine
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.rankings.services.measured import SEVERITIES
from backend.rankings.services.measured import add_severities
from backend.rankings.services.measured import descriptive_runs
from backend.rankings.services.measured import zero_severities
from backend.statistics.services.helpers import _percent
from backend.statistics.services.helpers import _scoped

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from backend.rankings.services.measured import DescriptiveRuns

logger = logging.getLogger(__name__)

_TERMINAL_RUN_STATUSES = [str(s) for s in AnalysisRun.TERMINAL_STATUSES]
# Runs whose tool executions describe tool behaviour: finished runs and runs
# that FAILED (every tool failed, or an infra error). Cancelled runs say
# nothing about the tools; queued/running ones have no outcome yet.
_TOOL_BEHAVIOUR_RUN_STATUSES = [
    str(AnalysisRun.Status.COMPLETED),
    str(AnalysisRun.Status.PARTIAL),
    str(AnalysisRun.Status.FAILED),
]


def _user_key(user: AbstractBaseUser | None) -> str:
    return f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"


def _known_total(values: list[int | None]) -> int | None:
    """Sum of the known values; None when there is none (no measured job is no data, not zero)."""
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def _analysis_runs(user: AbstractBaseUser | None):
    """The user's analysis runs, without tool self-tests (canaries analyse fixtures, not samples)."""
    return _scoped(AnalysisRun.objects.exclude(purpose=AnalysisRun.Purpose.CANARY), user, "created_by")


def _measured_runs(user: AbstractBaseUser | None) -> DescriptiveRuns:
    """The user's jobs' representative + AI-reviewed complete runs (descriptive totals only)."""
    job_ids = _scoped(GenerationJob.objects.filter(status="completed"), user, "created_by").values_list(
        "id",
        flat=True,
    )
    return descriptive_runs(job_ids)


def _tool_behaviour_run_ids(user: AbstractBaseUser | None) -> list:
    """Each of the user's jobs' latest finished-or-failed run (tool behaviour).

    A run is FAILED exactly when no tool succeeded (or an infra error hit
    it), so leaving failed runs out would hide the failures that matter most
    and report a 100% success rate for a tool that failed in most runs.
    """
    job_ids = _scoped(GenerationJob.objects.all(), user, "created_by").values_list("id", flat=True)
    return list(
        AnalysisRun.objects.filter(
            generation_job_id__in=job_ids,
            status__in=_TOOL_BEHAVIOUR_RUN_STATUSES,
        )
        .exclude(purpose=AnalysisRun.Purpose.CANARY)
        # v1 runs (migrated, no frozen plan) were fail-open: never counted.
        .exclude(plan_hash="")
        .order_by("generation_job_id", "-created_at")
        .distinct("generation_job_id")
        .values_list("id", flat=True),
    )


def get_system_overview(user: AbstractBaseUser | None = None) -> dict[str, Any]:
    """High-level KPIs for dashboard cards & the statistics page."""
    from django.core.cache import cache

    cache_key = f"stats_overview_v3_{_user_key(user)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    jobs = _scoped(GenerationJob.objects.all(), user, "created_by")
    tasks = _analysis_runs(user)

    job_counts = jobs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        pending=Count("id", filter=Q(status="pending")),
    )

    task_counts = tasks.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=AnalysisRun.Status.COMPLETED)),
        partial=Count("id", filter=Q(status=AnalysisRun.Status.PARTIAL)),
        failed=Count("id", filter=Q(status=AnalysisRun.Status.FAILED)),
        cancelled=Count("id", filter=Q(status=AnalysisRun.Status.CANCELLED)),
        running=Count("id", filter=Q(status=AnalysisRun.Status.RUNNING)),
        queued=Count("id", filter=Q(status=AnalysisRun.Status.QUEUED)),
        terminal=Count("id", filter=Q(status__in=_TERMINAL_RUN_STATUSES)),
        avg_duration=Avg("duration_seconds", filter=Q(status=AnalysisRun.Status.COMPLETED)),
    )

    unique_models_used = jobs.exclude(model__isnull=True).values_list("model_id", flat=True).distinct().count()
    described = _measured_runs(user)
    static = list(described.static.values())
    reviewed = list(described.ai.values())

    res = {
        "total_models": LLMModel.objects.count(),
        "models_in_use": unique_models_used,
        "total_apps": job_counts["total"] or 0,
        "apps_completed": job_counts["completed"] or 0,
        "apps_failed": job_counts["failed"] or 0,
        "apps_running": job_counts["running"] or 0,
        "apps_pending": job_counts["pending"] or 0,
        "apps_success_rate": _percent(
            job_counts["completed"] or 0,
            job_counts["total"] or 0,
        ),
        "total_analyses": task_counts["total"] or 0,
        "analyses_completed": task_counts["completed"] or 0,
        "analyses_partial": task_counts["partial"] or 0,
        "analyses_failed": task_counts["failed"] or 0,
        "analyses_cancelled": task_counts["cancelled"] or 0,
        "analyses_running": task_counts["running"] or 0,
        "analyses_pending": task_counts["queued"] or 0,
        # Share of finished analyses in which every required tool worked;
        # queued/running runs are neither successes nor failures yet.
        "analyses_success_rate": _percent(task_counts["completed"] or 0, task_counts["terminal"] or 0),
        "avg_analysis_duration_seconds": (
            round(task_counts["avg_duration"], 1) if task_counts["avg_duration"] else 0.0
        ),
        # Counted static findings of each analysed job's representative run —
        # a re-analysis never adds to it (nor hides it), and AI opinions are
        # counted apart. ``static_measured_jobs`` / ``ai_reviewed_jobs`` are
        # their denominators; with none the total is None ("no data"), never
        # a 0 that would read as clean code.
        "total_findings": _known_total([r.findings_total() for r in static]),
        "total_ai_findings": _known_total([r.ai_total() for r in reviewed]),
        "measured_jobs": len(described.runs),
        "static_measured_jobs": len(static),
        "ai_reviewed_jobs": len(reviewed),
    }
    cache.set(cache_key, res, 15)
    return res


def get_severity_distribution(
    user: AbstractBaseUser | None = None,
) -> dict[str, Any]:
    """Severity mix of counted findings in each job's canonical measured run.

    ``by_source.static`` is the measurement (counted static findings);
    ``by_source.ai`` the AI reviewer's consensus opinions, kept apart.
    ``static_measured_jobs`` / ``ai_reviewed_jobs`` are the jobs each side
    covers: zero counts over zero jobs are no data, not clean code.
    """
    from django.core.cache import cache

    cache_key = f"stats_severity_v3_{_user_key(user)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    static_counts, ai_counts = zero_severities(), zero_severities()
    described = _measured_runs(user)
    for run in described.static.values():
        add_severities(static_counts, run.findings_by_severity())
    for run in described.ai.values():
        add_severities(ai_counts, run.ai_by_severity())
    counts = {s: static_counts[s] + ai_counts[s] for s in SEVERITIES}
    total = sum(counts.values())
    res = {
        "total": total,
        "distribution": [
            {
                "severity": s,
                "count": counts[s],
                "percent": _percent(counts[s], total),
            }
            for s in SEVERITIES
        ],
        "by_source": {"static": static_counts, "ai": ai_counts},
        "static_measured_jobs": len(described.static),
        "ai_reviewed_jobs": len(described.ai),
    }
    cache.set(cache_key, res, 15)
    return res


def get_model_comparison(
    user: AbstractBaseUser | None = None,
    limit: int = 25,
    *,
    prompt_hash: str | None = None,
    bundle_key: str | None = None,
    experiment_id: str | None = None,
    plan_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Per-model rollup on the shared rankings scoring (0..1 scales).

    Delegates to the rankings aggregator so this page can never contradict
    /rankings (which is scoped to the same user): one analysis plan, each
    job's canonical measured run, AI-reviewer findings kept out of the
    empirical score, ``n_trials`` = analysed complete jobs. As on the
    rankings rows, ``findings`` / ``issues`` are zero-filled: they cover the
    ``n_static`` trials with an available static measurement, and
    ``ai_findings`` the ``n_ai`` trials with a successful AI review — read
    those counts before the severities.
    """
    from django.core.cache import cache

    from backend.rankings.services import aggregate_rankings

    cache_key = (
        f"stats_models_v4_{limit}_{_user_key(user)}_{prompt_hash or ''}_{bundle_key or ''}_"
        f"{experiment_id or ''}_{plan_hash or ''}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows: list[dict[str, Any]] = []
    for r in aggregate_rankings(
        user=user,
        prompt_hash=prompt_hash,
        bundle_key=bundle_key,
        experiment_id=experiment_id,
        plan_hash=plan_hash,
    ):  # sorted by composite desc
        if not r.get("apps"):
            continue
        rows.append(
            {
                "model_id": r["model_id"],
                "name": r["model_name"],
                "provider": r["provider"],
                "apps": r["apps"],
                "apps_completed": r["apps_completed"],
                "success_rate": _percent(r["apps_completed"], r["apps"]),
                "avg_duration_seconds": r["avg_duration"],
                "mss": r["mss_score"],
                "empirical_quality": r["empirical_quality_score"],
                "composite": r["composite_score"],
                # Legacy name: the conformance pass rate (as rankings'
                # ``functional_pass_rate``; the smoke probe is never counted).
                "smoke_pass_rate": r["functional_pass_rate"],
                "functional_pass_rate": r["functional_pass_rate"],
                "functional_n": r.get("functional_n", 0),
                "n_trials": r["n_trials"],
                "n_static": r.get("n_static", 0),
                "n_ai": r.get("n_ai", 0),
                "findings": r["findings"],
                "ai_findings": r["ai_findings"],
                "issues": r.get("issues"),
                "measured_sloc": r.get("measured_sloc"),
                "plan_hash": r.get("plan_hash"),
            },
        )
        if len(rows) >= limit:
            break

    cache.set(cache_key, rows, 15)
    return rows


def get_tool_effectiveness(
    user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    """Per-analyzer behaviour over each job's latest finished-or-failed run (the tool card).

    Population: for every job of the user, its newest COMPLETED, PARTIAL or
    FAILED non-canary v2 run (one run per job; cancelled runs say nothing
    about the tools). Per tool:

    * ``runs``: executions in those runs, whatever their status;
      ``failed`` / ``timed_out`` / ``not_applicable`` / ``skipped``: by status.
    * ``scans``: *succeeded* executions only. A failed or timed-out tool did
      not scan anything and a not-applicable one had no file in scope, so
      their zero findings never dilute ``avg_per_scan``.
    * ``success_rate``: scans / (scans + failed + timed_out); None when the
      tool never attempted a scan.
    * ``findings``: every finding the succeeded executions reported (raw tool
      output, counted or not: style, out-of-scope and scaffold findings
      included; for the AI reviewer, every replicate's findings).
      ``counted_findings``: those the measurement counts.
    * ``avg_per_scan``: findings / scans; None when ``scans`` is 0.
    * ``top_rule``: the most frequent rule among ``findings``.
    """
    from django.core.cache import cache

    cache_key = f"stats_tools_v4_{_user_key(user)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    results = ToolResult.objects.filter(run_id__in=_tool_behaviour_run_ids(user))
    by_tool: dict[str, dict[str, Any]] = {}
    for row in results.values("tool_slug", "category", "status").annotate(n=Count("id")):
        bucket = by_tool.setdefault(
            row["tool_slug"],
            {"category": row["category"], "statuses": {}},
        )
        bucket["category"] = bucket["category"] or row["category"]
        bucket["statuses"][row["status"]] = bucket["statuses"].get(row["status"], 0) + int(row["n"])

    succeeded = results.filter(status=ToolResult.Status.SUCCEEDED)
    finding_counts: dict[str, int] = {}
    counted_counts: dict[str, int] = {}
    for slug, total, counted in (
        Finding.objects.filter(result__in=succeeded)
        .values_list("tool_slug")
        .annotate(c=Count("id"), counted=Count("id", filter=Q(counted=True)))
    ):
        finding_counts[slug] = int(total)
        counted_counts[slug] = int(counted)
    versions: dict[str, set[str]] = {}
    for slug, version in succeeded.exclude(tool_version="").values_list("tool_slug", "tool_version").distinct():
        versions.setdefault(slug, set()).add(version)

    # Top rule per tool among the same (user-scoped, deduplicated) findings.
    top_rules: dict[str, str] = {}
    for item in (
        Finding.objects.filter(result__in=succeeded)
        .exclude(rule_id="")
        .values("tool_slug", "rule_id")
        .annotate(c=Count("id"))
        .order_by("tool_slug", "-c", "rule_id")
    ):
        top_rules.setdefault(item["tool_slug"], item["rule_id"])

    rows: list[dict[str, Any]] = []
    for slug, bucket in by_tool.items():
        statuses = bucket["statuses"]
        scans = statuses.get(ToolResult.Status.SUCCEEDED, 0)
        failed = statuses.get(ToolResult.Status.FAILED, 0)
        timed_out = statuses.get(ToolResult.Status.TIMED_OUT, 0)
        findings = int(finding_counts.get(slug, 0))
        rows.append(
            {
                "name": slug,
                "type": bucket["category"],
                "scans": scans,
                "findings": findings,
                # No scan, no average: None, not a 0 that reads as "finds nothing".
                "avg_per_scan": round(findings / scans, 1) if scans else None,
                "counted_findings": int(counted_counts.get(slug, 0)),
                "top_rule": top_rules.get(slug, ""),
                "runs": sum(statuses.values()),
                "failed": failed,
                "timed_out": timed_out,
                "not_applicable": statuses.get(ToolResult.Status.NOT_APPLICABLE, 0),
                "skipped": statuses.get(ToolResult.Status.SKIPPED, 0),
                "success_rate": round(scans / (scans + failed + timed_out), 4) if scans + failed + timed_out else None,
                "versions": sorted(versions.get(slug, set())),
            },
        )
    rows.sort(key=lambda r: (-r["findings"], r["name"]))
    cache.set(cache_key, rows, 15)
    return rows


def get_top_findings(
    limit: int = 10,
    user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    """Most frequent counted findings (by tool + rule): static ones of each job's
    representative run, AI ones of its latest AI-reviewed run."""
    from django.core.cache import cache

    cache_key = f"stats_top_findings_v3_{limit}_{_user_key(user)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    described = _measured_runs(user)
    static_run_ids = [r.run_id for r in described.runs.values()]
    ai_run_ids = [r.run_id for r in described.ai.values()]
    rows = (
        Finding.objects.filter(
            Q(run_id__in=static_run_ids, is_ai=False) | Q(run_id__in=ai_run_ids, is_ai=True),
            counted=True,
        )
        .values("tool_slug", "rule_id", "severity", "is_ai")
        .annotate(count=Count("id"), title=Min("title"))
        .order_by("-count", "tool_slug", "rule_id", "severity")[:limit]
    )
    res = [
        {
            # Grouped by rule, not by free-text title (AI titles vary per sample).
            "title": r["title"],
            "severity": r["severity"],
            "count": int(r["count"]),
            "rule_id": r["rule_id"],
            "tool": r["tool_slug"],
            "is_ai": r["is_ai"],
        }
        for r in rows
    ]
    cache.set(cache_key, res, 15)
    return res


def get_code_generation_stats(
    user: AbstractBaseUser | None = None,
) -> dict[str, Any]:
    from django.core.cache import cache

    cache_key = f"stats_code_gen_v2_{_user_key(user)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    jobs = _scoped(GenerationJob.objects.all(), user, "created_by")

    counts = jobs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        avg_duration=Avg("duration_seconds", filter=Q(status="completed")),
    )

    total_tokens = 0
    total_cost = 0.0
    for raw_metrics in jobs.filter(status="completed").values_list("metrics", flat=True).iterator():
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        tokens = metrics.get("total_tokens")
        if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
            total_tokens += int(tokens)
        cost = metrics.get("cost") or metrics.get("total_cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            total_cost += float(cost)

    # Lines of code as the analysis measured them (lockfiles, build output and
    # assets excluded) for the jobs that have a measurement — not the raw
    # newline count of every collected file.
    measured = _measured_runs(user).runs
    total_sloc = [r.total_sloc() for r in measured.values() if r.total_sloc() is not None]
    measured_sloc = [r.measured_sloc() for r in measured.values() if r.measured_sloc() is not None]

    res = {
        "total_apps": counts["total"] or 0,
        "completed": counts["completed"] or 0,
        "failed": counts["failed"] or 0,
        "running": counts["running"] or 0,
        "success_rate": _percent(counts["completed"] or 0, counts["total"] or 0),
        "avg_duration_seconds": (round(counts["avg_duration"], 1) if counts["avg_duration"] else 0.0),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "total_lines_of_code": sum(total_sloc),
        "measured_lines_of_code": sum(measured_sloc),
        "loc_jobs_covered": len(total_sloc),
        "unique_templates": jobs.exclude(app_requirement__isnull=True)
        .values_list("app_requirement_id", flat=True)
        .distinct()
        .count(),
        "unique_stacks": jobs.exclude(stack_slug="").values_list("stack_slug", flat=True).distinct().count(),
    }
    cache.set(cache_key, res, 15)
    return res


def _selftest_tool_row(tool: AnalyzerTool, entry: dict[str, Any] | None, *, has_selftest: bool) -> dict[str, Any]:
    if not tool.is_enabled:
        return {"available": False, "status": "disabled", "message": "Disabled in the catalog"}
    if entry is None:
        message = "Not covered by the latest self-test" if has_selftest else "No self-test has run yet"
        return {"available": False, "status": "untested", "message": message}
    if entry.get("passed"):
        return {"available": True, "status": "passed", "message": "Self-test passed"}
    missing = entry.get("missing") or []
    unexpected = entry.get("unexpected_in_clean") or []
    detail = []
    if entry.get("status") and entry.get("status") != "succeeded":
        detail.append(f"execution {entry['status']}")
    if missing:
        detail.append(f"missed {len(missing)} expected finding(s)")
    if unexpected:
        detail.append(f"{len(unexpected)} finding(s) on clean code")
    return {
        "available": False,
        "status": "failed",
        "message": "Self-test failed" + (f": {', '.join(detail)}" if detail else ""),
    }


def _selftest_state() -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Per-tool self-test verdicts for the current analyzer image, and the latest self-test.

    A self-test may cover only some tools, so each tool's verdict comes from
    the newest canary run that included it; runs on an older image do not
    vouch for the current one.
    """
    try:
        image_tag = sandbox_engine.image_tag()
    except Exception:
        logger.exception("Could not compute the analyzer image tag")
        image_tag = None
    try:
        latest = canary_engine.latest(image_tag)
        per_tool = getattr(canary_engine, "latest_per_tool", None)
        tools = per_tool(image_tag) if per_tool is not None else (latest or {}).get("tools") or {}
    except Exception:
        # Health must render even when the self-test record cannot be read;
        # every tool is then reported untested, never "online" by default.
        logger.exception("Could not read the latest analyzer self-test")
        return {}, None
    return dict(tools or {}), latest


def get_analyzer_health() -> dict[str, Any]:
    """Tool health from the canary self-tests, not from catalog flags.

    A tool is "online" only when it is enabled AND passed its most recent
    self-test on the current analyzer image (positive + negative controls
    through the real pipeline).
    """
    from django.core.cache import cache

    cache_key = "analyzer_health_status_v2"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    selftest_tools, latest = _selftest_state()
    tools = list(AnalyzerTool.objects.all())
    analyzers = []
    by_type: dict[str, dict[str, int]] = {}
    for t in tools:
        verdict = _selftest_tool_row(t, selftest_tools.get(t.slug), has_selftest=latest is not None)
        analyzers.append(
            {
                "name": t.slug,
                "type": t.category,
                "display_name": t.name,
                "version": t.version,
                **verdict,
            },
        )
        bucket = by_type.setdefault(t.category, {"online": 0, "total": 0})
        bucket["total"] += 1
        if verdict["available"]:
            bucket["online"] += 1
    online = sum(1 for a in analyzers if a["available"])
    res = {
        "total": len(tools),
        "online": online,
        "offline": len(tools) - online,
        "by_type": by_type,
        "analyzers": analyzers,
        "selftest": (
            {
                "passed": latest.get("passed"),
                "run_id": str(latest["run_id"]) if latest.get("run_id") else None,
                "image_tag": latest.get("image_tag"),
                "evaluated_at": latest.get("evaluated_at") or latest.get("created_at"),
            }
            if latest
            else None
        ),
    }
    # Short cache: a fresh self-test should show up quickly.
    cache.set(cache_key, res, 60)
    return res
