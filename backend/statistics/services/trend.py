"""Time-series trends and recent activity feeds."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Count
from django.db.models import Q
from django.db.models.functions import TruncDate

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationJob
from backend.statistics.services.helpers import _scoped

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def get_analysis_trends(
    days: int = 7,
    user: AbstractBaseUser | None = None,
) -> dict[str, Any]:
    """Daily analysis counts for the last *days* days."""
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_trends_{days}_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    days = max(1, min(days, 90))
    start = datetime.now(tz=UTC) - timedelta(days=days - 1)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)

    tasks = _scoped(AnalysisRun.objects.all(), user, "created_by").filter(
        created_at__gte=start,
    )
    rows = (
        tasks.annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            failed=Count("id", filter=Q(status="failed")),
        )
        .order_by("d")
    )
    bucket: dict[str, dict[str, int]] = {}
    for r in rows:
        key = r["d"].isoformat() if r["d"] else ""
        if key:
            bucket[key] = {
                "total": int(r["total"]),
                "completed": int(r["completed"]),
                "failed": int(r["failed"]),
            }

    series: list[dict[str, Any]] = []
    cursor = start
    today = datetime.now(tz=UTC).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    while cursor <= today:
        key = cursor.date().isoformat()
        row = bucket.get(key, {"total": 0, "completed": 0, "failed": 0})
        series.append(
            {
                "date": key,
                "label": cursor.strftime("%a"),
                "total": row["total"],
                "completed": row["completed"],
                "failed": row["failed"],
            },
        )
        cursor += timedelta(days=1)

    res = {
        "days": days,
        "total": sum(p["total"] for p in series),
        "series": series,
    }
    cache.set(cache_key, res, 15)
    return res


def get_recent_activity(
    limit: int = 20,
    user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    from django.core.cache import cache

    u_key = f"user_{user.id}" if user and getattr(user, "is_authenticated", False) else "anonymous"
    cache_key = f"stats_activity_{limit}_{u_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    limit = max(1, min(limit, 100))

    jobs = (
        _scoped(GenerationJob.objects.all(), user, "created_by")
        .select_related("model")
        .order_by("-created_at")[:limit]
    )
    tasks = _scoped(AnalysisRun.objects.all(), user, "created_by").order_by(
        "-created_at",
    )[:limit]

    items: list[dict[str, Any]] = [
        {
            "kind": "generation",
            "id": str(j.id),
            "title": (f"Generation {j.mode} job — {j.model.model_name if j.model else 'no model'}"),
            "status": j.status,
            "created_at": j.created_at.isoformat(),
        }
        for j in jobs
    ]
    items.extend(
        {
            "kind": "analysis",
            "id": str(t.id),
            "title": t.name or f"Analysis {str(t.id)[:8]}",
            "status": t.status,
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    )

    items.sort(key=lambda i: i["created_at"], reverse=True)
    res = items[:limit]
    cache.set(cache_key, res, 15)
    return res
