"""Aggregation queries for KPIs, severity, model comparison, and tools."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.services.base import ADVISORY_TOOLS
from backend.generation.models import GenerationRun
from backend.llm_models.models import LLMModel
from backend.statistics.services.helpers import _percent
from backend.statistics.services.helpers import _scoped

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def _finished_rate(succeeded: int, finished: int) -> float | None:
    """Success percent over the work that finished; None (not 0) when nothing finished yet.

    Pending, running and cancelled work is no outcome: counting it in the
    denominator made a running study look like a wave of failures.
    """
    return _percent(succeeded, finished) if finished > 0 else None


def _rounded(value: float | None) -> float | None:
    """An average rounded for display; None (not 0.0) when nothing was measured."""
    return round(value, 1) if value is not None else None


def get_system_overview(user: AbstractBaseUser | None = None) -> dict[str, Any]:
    """High-level KPIs for dashboard cards & the statistics page."""
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_overview_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    runs = _scoped(GenerationRun.objects.all(), user, "created_by")
    tasks = _scoped(AnalysisRun.objects.all(), user, "created_by")
    findings = _scoped(
        Finding.objects.all(),
        user,
        "result__run__created_by",
    )

    run_counts = runs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        pending=Count("id", filter=Q(status="pending")),
        cancelled=Count("id", filter=Q(status="cancelled")),
    )

    task_counts = tasks.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        partial=Count("id", filter=Q(status="partial")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        pending=Count("id", filter=Q(status="pending")),
        cancelled=Count("id", filter=Q(status="cancelled")),
        avg_duration=Avg("duration_seconds", filter=Q(status="completed")),
    )

    unique_models_used = runs.exclude(model__isnull=True).values_list("model_id", flat=True).distinct().count()

    res = {
        "total_models": LLMModel.objects.count(),
        "models_in_use": unique_models_used,
        "total_apps": run_counts["total"] or 0,
        "apps_completed": run_counts["completed"] or 0,
        "apps_failed": run_counts["failed"] or 0,
        "apps_running": run_counts["running"] or 0,
        "apps_pending": run_counts["pending"] or 0,
        "apps_cancelled": run_counts["cancelled"] or 0,
        "apps_success_rate": _finished_rate(
            run_counts["completed"] or 0,
            (run_counts["completed"] or 0) + (run_counts["failed"] or 0),
        ),
        "total_analyses": task_counts["total"] or 0,
        "analyses_completed": task_counts["completed"] or 0,
        "analyses_partial": task_counts["partial"] or 0,
        "analyses_failed": task_counts["failed"] or 0,
        "analyses_running": task_counts["running"] or 0,
        "analyses_pending": task_counts["pending"] or 0,
        "analyses_cancelled": task_counts["cancelled"] or 0,
        # A partial run finished with some tools failed: finished, not a success.
        "analyses_success_rate": _finished_rate(
            task_counts["completed"] or 0,
            (task_counts["completed"] or 0) + (task_counts["partial"] or 0) + (task_counts["failed"] or 0),
        ),
        # None (not 0.0) until an analysis completed: no duration was measured.
        "avg_analysis_duration_seconds": _rounded(task_counts["avg_duration"]),
        "total_findings": findings.count(),
    }
    cache.set(cache_key, res, 15)
    return res


def get_severity_distribution(
    user: AbstractBaseUser | None = None,
) -> dict[str, Any]:
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_severity_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    findings = _scoped(
        Finding.objects.all(),
        user,
        "result__run__created_by",
    )

    raw = dict(
        findings.values_list("severity").annotate(c=Count("id")).values_list("severity", "c"),
    )
    severities = ["critical", "high", "medium", "low", "info"]
    counts = {s: int(raw.get(s, 0)) for s in severities}
    total = sum(counts.values())
    distribution = [
        {
            "severity": s,
            "count": counts[s],
            "percent": _percent(counts[s], total),
        }
        for s in severities
    ]
    # Split deterministic-tool findings from AI-reviewer opinions so the
    # chart can say how much of the pool is measurement vs judgment.
    ai_slugs = AnalyzerTool.ai_slugs()
    ai_raw = dict(
        findings.filter(result__tool_slug__in=ai_slugs)
        .values_list("severity")
        .annotate(c=Count("id"))
        .values_list("severity", "c"),
    )
    ai_counts = {s: int(ai_raw.get(s, 0)) for s in severities}
    static_counts = {s: counts[s] - ai_counts[s] for s in severities}
    res = {
        "total": total,
        "distribution": distribution,
        "by_source": {"static": static_counts, "ai": ai_counts},
    }
    cache.set(cache_key, res, 15)
    return res


def get_model_comparison(
    user: AbstractBaseUser | None = None,
    limit: int = 25,
    *,
    prompt_hash: str | None = None,
    config_fingerprint: str | None = None,
    study_id: str | None = None,
) -> list[dict[str, Any]]:
    """Per-model rollup on the shared rankings scoring (0..1 scales).

    Delegates to the rankings aggregator so this page can never contradict
    /rankings: findings are deduped to each run's latest analysis, AI-reviewer
    findings are excluded from the empirical score, and MSS/empirical/
    composite mean the same thing everywhere. ``prompt_hash``/``config_fingerprint``/
    ``study_id`` narrow the comparison to one prompt version, one configuration
    or one study — see aggregate_rankings.
    """
    from django.core.cache import cache

    from backend.rankings.services import aggregate_rankings

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_models_v3_{limit}_{u_key}_{prompt_hash or ''}_{config_fingerprint or ''}_{study_id or ''}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows: list[dict[str, Any]] = []
    for r in aggregate_rankings(
        user=user,
        prompt_hash=prompt_hash,
        config_fingerprint=config_fingerprint,
        study_id=study_id,
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
                "apps_failed": r.get("apps_failed", 0),
                # Over finished generations: pending, running and cancelled runs are no outcome.
                "success_rate": _finished_rate(r["apps_completed"], r["apps_completed"] + r.get("apps_failed", 0)),
                "avg_duration_seconds": r["avg_duration"],
                "mss": r["mss_score"],
                "empirical_quality": r["empirical_quality_score"],
                "composite": r["composite_score"],
                # The latest evaluation's functional pass rate (None when no run was measured).
                "pass_rate": r["functional_pass_rate"],
                "n_trials": r["n_trials"],
                "findings": r["findings"],
                "ai_findings": r["ai_findings"],
            },
        )
        if len(rows) >= limit:
            break

    cache.set(cache_key, rows, 15)
    return rows


def get_tool_effectiveness(
    user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    """Per-analyzer scan and finding totals.

    ``scans`` counts executions that measured the app (completed, without a
    ``not_measured`` record). Not-applicable, failed, cancelled and
    app-not-running results measured nothing (spec section 8.8); they are
    ``not_measured``, and stay out of ``avg_per_scan`` so a tool that often
    fails does not look quieter than it is.
    """
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_tools_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    results = _scoped(
        ToolResult.objects.all(),
        user,
        "run__created_by",
    )

    from backend.analysis.services.runner import NOT_MEASURED

    # A missing ``measurement`` key is NULL in SQL: spelled out, or NOT(...) would drop those rows too.
    measured = Q(status=ToolResult.Status.COMPLETED) & (
        Q(summary__measurement__isnull=True) | ~Q(summary__measurement=NOT_MEASURED)
    )
    by_tool = (
        results.values("tool_slug", "category")
        .annotate(
            scans=Count("id", filter=measured, distinct=True),
            # Pending and running results are neither: they have no outcome yet.
            not_measured=Count(
                "id",
                filter=Q(status__in=[ToolResult.Status.FAILED, ToolResult.Status.SKIPPED]) & ~measured,
                distinct=True,
            ),
            findings=Count("findings"),
        )
        .order_by("-findings")
    )

    by_tool_list = list(by_tool)
    tool_names = [t["tool_slug"] for t in by_tool_list]

    # Bulk fetch top rules for all tools in one grouped query!
    rules_data = (
        _scoped(Finding.objects.all(), user, "result__run__created_by")
        .filter(result__tool_slug__in=tool_names)
        .exclude(rule_id="")
        .values("result__tool_slug", "rule_id")
        .annotate(c=Count("id"))
        .order_by("result__tool_slug", "-c")
    )

    # Group in Python: the first rule we see for a tool name is the top rule because it's sorted by -c!
    top_rules: dict[str, str] = {}
    for item in rules_data:
        name = item["result__tool_slug"]
        if name not in top_rules:
            top_rules[name] = item["rule_id"]

    rows: list[dict[str, Any]] = []
    for t in by_tool_list:
        scans = int(t["scans"]) or 0
        findings = int(t["findings"]) or 0
        rows.append(
            {
                "name": t["tool_slug"],
                "type": t["category"],
                "scans": scans,
                "not_measured": int(t["not_measured"]) or 0,
                "findings": findings,
                # None, not 0.0, when no execution measured anything.
                "avg_per_scan": round(findings / scans, 1) if scans else None,
                "top_rule": top_rules.get(t["tool_slug"], ""),
            },
        )
    cache.set(cache_key, rows, 15)
    return rows


def get_top_findings(
    limit: int = 10,
    user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    """Most frequent findings: ``title``, ``tool``, ``tool_severity`` (as the tool printed it) and ``count``.

    ``dependency_advisory`` marks rows of known-vulnerability advisories
    (packages, mostly the starter's pins), which are not code issues (P2).
    """
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_top_findings_v2_{limit}_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    findings = _scoped(
        Finding.objects.all(),
        user,
        "result__run__created_by",
    )
    # One row per (title, tool, the tool's own label): the level shown is the
    # one the tool printed ("" when it rates nothing), never the normalized
    # severity, and two tools reporting the same title stay apart.
    rows = (
        findings.values("title", "result__tool_slug", "tool_severity")
        .annotate(
            count=Count("id"),
            advisories=Count("id", filter=Q(result__tool_slug__in=ADVISORY_TOOLS, category="dependency")),
        )
        .order_by("-count", "title", "result__tool_slug", "tool_severity")[:limit]
    )
    res = [
        {
            "title": r["title"],
            "tool": r["result__tool_slug"] or "",
            "tool_severity": r["tool_severity"] or "",
            "count": int(r["count"]),
            "dependency_advisory": int(r["advisories"]) == int(r["count"]),
        }
        for r in rows
    ]
    cache.set(cache_key, res, 15)
    return res


def get_code_generation_stats(
    user: AbstractBaseUser | None = None,
) -> dict[str, Any]:
    """Generation-run counts, tokens, cost and source LOC from the ``GenerationRun.metrics`` contract keys.

    ``total_tokens``/``total_cost_usd``/``total_lines_of_code`` sum the completed runs that recorded the
    value (``*_runs`` says how many); each is ``None`` when none did (unmeasured is not 0). LOC is the
    model scope's non-test source LOC (``metrics.loc_model``).
    """
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_code_gen_v3_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    runs = _scoped(GenerationRun.objects.all(), user, "created_by")

    counts = runs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        pending=Count("id", filter=Q(status="pending")),
        cancelled=Count("id", filter=Q(status="cancelled")),
        avg_duration=Avg("duration_seconds", filter=Q(status="completed")),
    )

    sums: dict[str, float] = {"tokens": 0.0, "cost": 0.0, "loc": 0.0}
    seen: dict[str, int] = {"tokens": 0, "cost": 0, "loc": 0}
    for tokens, cost, loc in (
        runs.filter(status="completed")
        .values_list("metrics__total_tokens", "metrics__cost_usd", "metrics__loc_model")
        .iterator(chunk_size=500)
    ):
        for key, value in (("tokens", tokens), ("cost", cost), ("loc", loc)):
            if isinstance(value, int | float) and not isinstance(value, bool):
                sums[key] += float(value)
                seen[key] += 1
    unique_stacks = runs.exclude(stack_profile__isnull=True).values_list("stack_profile__slug", flat=True).distinct()

    res = {
        "total_apps": counts["total"] or 0,
        "completed": counts["completed"] or 0,
        "failed": counts["failed"] or 0,
        "running": counts["running"] or 0,
        "pending": counts["pending"] or 0,
        "cancelled": counts["cancelled"] or 0,
        "success_rate": _finished_rate(counts["completed"] or 0, (counts["completed"] or 0) + (counts["failed"] or 0)),
        "avg_duration_seconds": _rounded(counts["avg_duration"]),
        "total_tokens": int(sums["tokens"]) if seen["tokens"] else None,
        "tokens_runs": seen["tokens"],
        "total_cost_usd": round(sums["cost"], 4) if seen["cost"] else None,
        "cost_runs": seen["cost"],
        "total_lines_of_code": int(sums["loc"]) if seen["loc"] else None,
        "loc_runs": seen["loc"],
        "unique_stacks": unique_stacks.count(),
    }
    cache.set(cache_key, res, 15)
    return res


def get_analyzer_health() -> dict[str, Any]:
    from django.core.cache import cache

    cache_key = "analyzer_health_status"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Health now reflects the data-driven catalog: an enabled tool is "online"
    # (installable). Per-user runtime availability is reported by the analyzers
    # workspace API, not here.
    tools = list(AnalyzerTool.objects.all())
    online = sum(1 for t in tools if t.is_enabled)
    by_type: dict[str, dict[str, int]] = {}
    for t in tools:
        bucket = by_type.setdefault(t.category, {"online": 0, "total": 0})
        bucket["total"] += 1
        if t.is_enabled:
            bucket["online"] += 1
    res = {
        "total": len(tools),
        "online": online,
        "offline": len(tools) - online,
        "by_type": by_type,
        "analyzers": [
            {
                "name": t.slug,
                "type": t.category,
                "display_name": t.name,
                "available": t.is_enabled,
                "message": "Enabled" if t.is_enabled else "Disabled",
            }
            for t in tools
        ],
    }
    # Cache for 5 minutes (300 seconds)
    cache.set(cache_key, res, 300)
    return res
