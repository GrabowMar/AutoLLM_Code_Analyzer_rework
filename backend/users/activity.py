"""Per-user activity summary for the profile page: counts and recent work."""

from __future__ import annotations

from heapq import nlargest
from typing import TYPE_CHECKING

from backend.analysis.models import AnalysisRun
from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models.choices import RunStatus
from backend.generation.runs.view import run_views
from backend.reports.models import Report

if TYPE_CHECKING:
    from backend.generation.runs.view import RunView
    from backend.users.models import User

RECENT_PER_KIND = 8


def _sample_title(view: RunView) -> str:
    task = view.task.title if view.task is not None else ""
    return " · ".join(p for p in (task, view.model_slug or "") if p) or f"{view.kind} run"


def user_activity(user: User, limit: int = RECENT_PER_KIND) -> dict:
    samples = GenerationRun.objects.filter(created_by=user)
    runs = AnalysisRun.objects.filter(created_by=user)
    studies = Study.objects.filter(created_by=user)
    reports = Report.objects.filter(created_by=user)

    recent_samples = list(samples.order_by("-created_at")[:limit])
    recent = [
        {
            "kind": "application",
            "id": view.id,
            "title": _sample_title(view),
            "status": view.status,
            "created_at": sample.created_at,
            "href": f"/samples/{view.id}",
        }
        for sample, view in zip(recent_samples, run_views(recent_samples), strict=True)
    ]
    recent += [
        {
            "kind": "analysis",
            "id": str(r.id),
            "title": r.name or f"Analysis {str(r.id)[:8]}",
            "status": r.status,
            "created_at": r.created_at,
            "href": f"/analysis/{r.id}",
        }
        for r in runs.order_by("-created_at")[:limit]
    ]
    recent += [
        {
            "kind": "study",
            "id": str(e.id),
            "title": e.name,
            "status": e.status,
            "created_at": e.created_at,
            "href": f"/studies/{e.id}",
        }
        for e in studies.order_by("-created_at")[:limit]
    ]
    recent += [
        {
            "kind": "report",
            "id": r.report_id,
            "title": r.title,
            "status": r.status,
            "created_at": r.created_at,
            "href": f"/reports/{r.report_id}",
        }
        for r in reports.order_by("-created_at")[:limit]
    ]

    return {
        "date_joined": user.date_joined,
        "last_login": user.last_login,
        "counts": {
            "applications": samples.count(),
            "applications_completed": samples.filter(status=RunStatus.COMPLETED).count(),
            "analyses": runs.count(),
            "studies": studies.count(),
            "reports": reports.count(),
        },
        "recent": nlargest(limit, recent, key=lambda item: item["created_at"]),
    }
