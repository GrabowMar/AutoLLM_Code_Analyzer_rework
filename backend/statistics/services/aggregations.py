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
from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.statistics.services.helpers import _percent
from backend.statistics.services.helpers import _scoped

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def get_system_overview(user: AbstractBaseUser | None = None) -> dict[str, Any]:
    """High-level KPIs for dashboard cards & the statistics page."""
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_overview_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    jobs = _scoped(GenerationJob.objects.all(), user, "created_by")
    tasks = _scoped(AnalysisRun.objects.all(), user, "created_by")
    findings = _scoped(
        Finding.objects.all(),
        user,
        "result__run__created_by",
    )

    job_counts = jobs.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        pending=Count("id", filter=Q(status="pending")),
    )

    task_counts = tasks.aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status="completed")),
        failed=Count("id", filter=Q(status="failed")),
        running=Count("id", filter=Q(status="running")),
        pending=Count("id", filter=Q(status="pending")),
        avg_duration=Avg("duration_seconds", filter=Q(status="completed")),
    )

    unique_models_used = jobs.exclude(model__isnull=True).values_list("model_id", flat=True).distinct().count()

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
        "analyses_failed": task_counts["failed"] or 0,
        "analyses_running": task_counts["running"] or 0,
        "analyses_pending": task_counts["pending"] or 0,
        "analyses_success_rate": _percent(
            task_counts["completed"] or 0,
            task_counts["total"] or 0,
        ),
        "avg_analysis_duration_seconds": (
            round(task_counts["avg_duration"], 1) if task_counts["avg_duration"] else 0.0
        ),
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
    bundle_key: str | None = None,
) -> list[dict[str, Any]]:
    """Per-model rollup on the shared rankings scoring (0..1 scales).

    Delegates to the rankings aggregator so this page can never contradict
    /rankings: findings are deduped to each job's latest run, AI-reviewer
    findings are excluded from the empirical score, and MSS/empirical/
    composite mean the same thing everywhere. ``prompt_hash``/``bundle_key``
    narrow the comparison to one prompt version — see aggregate_rankings.
    """
    from django.core.cache import cache

    from backend.rankings.services import aggregate_rankings

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_models_v2_{limit}_{u_key}_{prompt_hash or ''}_{bundle_key or ''}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rows: list[dict[str, Any]] = []
    for r in aggregate_rankings(user=user, prompt_hash=prompt_hash, bundle_key=bundle_key):  # sorted by composite desc
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
                "smoke_pass_rate": r["functional_pass_rate"],
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
    """Per-analyzer scan and finding totals."""
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

    by_tool = (
        results.values("tool_slug", "category")
        .annotate(
            scans=Count("id", distinct=True),
            findings=Count("findings"),
        )
        .order_by("-findings")
    )

    by_tool_list = list(by_tool)
    tool_names = [t["tool_slug"] for t in by_tool_list]

    # Bulk fetch top rules for all tools in one grouped query!
    rules_data = (
        Finding.objects.filter(result__tool_slug__in=tool_names)
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
                "findings": findings,
                "avg_per_scan": round(findings / scans, 1) if scans else 0.0,
                "top_rule": top_rules.get(t["tool_slug"], ""),
            },
        )
    cache.set(cache_key, rows, 15)
    return rows


def get_top_findings(
    limit: int = 10,
    user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_top_findings_{limit}_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    findings = _scoped(
        Finding.objects.all(),
        user,
        "result__run__created_by",
    )
    rows = findings.values("title", "severity").annotate(count=Count("id")).order_by("-count")[:limit]
    res = [
        {
            "title": r["title"],
            "severity": r["severity"],
            "count": int(r["count"]),
        }
        for r in rows
    ]
    cache.set(cache_key, res, 15)
    return res


def get_code_generation_stats(
    user: AbstractBaseUser | None = None,
) -> dict[str, Any]:
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_code_gen_{u_key}"
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

    completed = jobs.filter(status="completed").only("metrics")
    total_tokens = 0
    total_cost = 0.0
    total_loc = 0
    unique_templates = (
        jobs.exclude(scaffolding_template__isnull=True)
        .values_list("scaffolding_template_id", flat=True)
        .distinct()
        .count()
    )
    for j in completed.iterator():
        metrics = j.metrics or {}
        total_tokens += int(metrics.get("total_tokens", 0) or 0)
        cost = metrics.get("cost") or metrics.get("total_cost")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)

        loc = metrics.get("lines_of_code")
        if loc is not None:
            total_loc += int(loc)
        else:
            # Fallback for old/legacy jobs: lazily loads result_data (Django will hit DB)
            result = j.result_data or {}
            job_loc = 0
            for key in ("backend", "frontend", "backend_code", "frontend_code", "content"):
                blob = result.get(key)
                if isinstance(blob, str):
                    job_loc += blob.count("\n") + 1
            # Also handle copilot files dict if present
            files = result.get("files")
            if isinstance(files, dict):
                for code in files.values():
                    if isinstance(code, str):
                        job_loc += code.count("\n") + 1
            total_loc += job_loc

    res = {
        "total_apps": counts["total"] or 0,
        "completed": counts["completed"] or 0,
        "failed": counts["failed"] or 0,
        "running": counts["running"] or 0,
        "success_rate": _percent(counts["completed"] or 0, counts["total"] or 0),
        "avg_duration_seconds": (round(counts["avg_duration"], 1) if counts["avg_duration"] else 0.0),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "total_lines_of_code": total_loc,
        "unique_templates": unique_templates,
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
