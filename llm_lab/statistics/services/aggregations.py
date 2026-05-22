"""Aggregation queries for KPIs, severity, model comparison, and tools."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Avg
from django.db.models import Count
from django.db.models import Q

from llm_lab.analysis.models import AnalysisResult
from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.services.base import AnalyzerRegistry
from llm_lab.generation.models import GenerationJob
from llm_lab.llm_models.models import LLMModel
from llm_lab.statistics.services.helpers import _percent
from llm_lab.statistics.services.helpers import _scoped

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
    tasks = _scoped(AnalysisTask.objects.all(), user, "created_by")
    findings = _scoped(
        Finding.objects.all(),
        user,
        "result__task__created_by",
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
        "result__task__created_by",
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
    res = {"total": total, "distribution": distribution}
    cache.set(cache_key, res, 15)
    return res


def get_model_comparison(
    user: AbstractBaseUser | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Per-model rollup: apps generated, success rate, avg duration, scores."""
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_models_{limit}_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    jobs = (
        _scoped(GenerationJob.objects.all(), user, "created_by")
        .exclude(model__isnull=True)
        .values(
            "model_id",
            "model__provider",
            "model__model_name",
            "model__model_id",
            "model__cost_efficiency",
        )
        .annotate(
            apps=Count("id"),
            apps_completed=Count("id", filter=Q(status="completed")),
            avg_duration=Avg(
                "duration_seconds",
                filter=Q(status="completed"),
            ),
        )
        .order_by("-apps")[:limit]
    )

    jobs_list = list(jobs)
    model_ids = [j["model_id"] for j in jobs_list]

    # Bulk fetch all finding severity counts for all target models in one query!
    sev_data = (
        Finding.objects.filter(
            result__task__generation_job__model_id__in=model_ids,
        )
        .values("result__task__generation_job__model_id", "severity")
        .annotate(c=Count("id"))
    )

    # Map by model_id -> severity -> count
    sev_map_by_model: dict[str, dict[str, int]] = {}
    for item in sev_data:
        m_id = item["result__task__generation_job__model_id"]
        sev_map_by_model.setdefault(m_id, {})[item["severity"]] = int(item["c"])

    rows: list[dict[str, Any]] = []
    for j in jobs_list:
        sev_map = sev_map_by_model.get(j["model_id"], {})
        critical = sev_map.get("critical", 0)
        high = sev_map.get("high", 0)
        medium = sev_map.get("medium", 0)
        low = sev_map.get("low", 0)

        # Composite security score: 10 = perfect; deduct weighted findings.
        weighted = critical * 5 + high * 2 + medium * 0.5 + low * 0.1
        per_app = weighted / max(1, j["apps_completed"] or 1)
        security = max(0.0, round(10.0 - min(per_app, 10.0), 2))

        # Quality and performance heuristics from completion stats.
        success = _percent(j["apps_completed"] or 0, j["apps"] or 0)
        quality = round((success / 10.0), 2)  # 0..10 scale
        performance = round(
            min(100.0, max(0.0, 100.0 - (j["avg_duration"] or 0.0) / 6)),
            1,
        )
        # Master "MSS" composite (0..100)
        mss = round(
            (security * 4 + quality * 3 + performance / 10 * 3),
            1,
        )

        rows.append(
            {
                "model_id": j["model__model_id"],
                "name": j["model__model_name"],
                "provider": j["model__provider"],
                "apps": int(j["apps"]),
                "apps_completed": int(j["apps_completed"]),
                "success_rate": success,
                "avg_duration_seconds": (round(j["avg_duration"], 1) if j["avg_duration"] else 0.0),
                "cost_efficiency": round(j["model__cost_efficiency"] or 0.0, 3),
                "security": security,
                "quality": quality,
                "performance": performance,
                "mss": mss,
                "findings": {
                    "critical": critical,
                    "high": high,
                    "medium": medium,
                    "low": low,
                    "info": sev_map.get("info", 0),
                },
            },
        )

    rows.sort(key=lambda r: r["mss"], reverse=True)
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
        AnalysisResult.objects.all(),
        user,
        "task__created_by",
    )

    by_tool = (
        results.values("analyzer_name", "analyzer_type")
        .annotate(
            scans=Count("id", distinct=True),
            findings=Count("findings"),
        )
        .order_by("-findings")
    )

    by_tool_list = list(by_tool)
    tool_names = [t["analyzer_name"] for t in by_tool_list]

    # Bulk fetch top rules for all tools in one grouped query!
    rules_data = (
        Finding.objects.filter(result__analyzer_name__in=tool_names)
        .exclude(rule_id="")
        .values("result__analyzer_name", "rule_id")
        .annotate(c=Count("id"))
        .order_by("result__analyzer_name", "-c")
    )

    # Group in Python: the first rule we see for a tool name is the top rule because it's sorted by -c!
    top_rules: dict[str, str] = {}
    for item in rules_data:
        name = item["result__analyzer_name"]
        if name not in top_rules:
            top_rules[name] = item["rule_id"]

    rows: list[dict[str, Any]] = []
    for t in by_tool_list:
        scans = int(t["scans"]) or 0
        findings = int(t["findings"]) or 0
        rows.append(
            {
                "name": t["analyzer_name"],
                "type": t["analyzer_type"],
                "scans": scans,
                "findings": findings,
                "avg_per_scan": round(findings / scans, 1) if scans else 0.0,
                "top_rule": top_rules.get(t["analyzer_name"], ""),
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
        "result__task__created_by",
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

    analyzers = AnalyzerRegistry.list_available()
    online = sum(1 for a in analyzers if a.get("available"))
    by_type: dict[str, dict[str, int]] = {}
    for a in analyzers:
        bucket = by_type.setdefault(
            a.get("type", "unknown"),
            {"online": 0, "total": 0},
        )
        bucket["total"] += 1
        if a.get("available"):
            bucket["online"] += 1
    res = {
        "total": len(analyzers),
        "online": online,
        "offline": len(analyzers) - online,
        "by_type": by_type,
        "analyzers": [
            {
                "name": a["name"],
                "type": a["type"],
                "display_name": a["display_name"],
                "available": a["available"],
                "message": a["availability_message"],
            }
            for a in analyzers
        ],
    }
    # Cache for 5 minutes (300 seconds)
    cache.set(cache_key, res, 300)
    return res
