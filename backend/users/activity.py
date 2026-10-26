"""Per-user activity summary for the profile page: counts and recent work."""

from __future__ import annotations

from heapq import nlargest
from typing import TYPE_CHECKING

from backend.analysis.models import AnalysisRun
from backend.generation.models import Experiment
from backend.generation.models import GenerationJob
from backend.reports.models import Report

if TYPE_CHECKING:
    from backend.users.models import User

RECENT_PER_KIND = 8


def _job_title(job: GenerationJob) -> str:
    app = job.app_requirement.name if job.app_requirement else ""
    model = job.model.model_name if job.model else ""
    return " · ".join(p for p in (app, model) if p) or f"{job.get_mode_display()} job"


def user_activity(user: User, limit: int = RECENT_PER_KIND) -> dict:
    jobs = GenerationJob.objects.filter(created_by=user)
    runs = AnalysisRun.objects.filter(created_by=user)
    experiments = Experiment.objects.filter(created_by=user)
    reports = Report.objects.filter(created_by=user)

    recent = [
        {
            "kind": "application",
            "id": str(j.id),
            "title": _job_title(j),
            "status": j.status,
            "created_at": j.created_at,
            "href": f"/applications/{j.id}",
        }
        for j in jobs.select_related("app_requirement", "model").order_by("-created_at")[:limit]
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
            "kind": "experiment",
            "id": str(e.id),
            "title": e.name,
            "status": e.status,
            "created_at": e.created_at,
            "href": f"/experiments/{e.id}",
        }
        for e in experiments.order_by("-created_at")[:limit]
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
            "applications": jobs.count(),
            "applications_completed": jobs.filter(status=GenerationJob.Status.COMPLETED).count(),
            "analyses": runs.count(),
            "experiments": experiments.count(),
            "reports": reports.count(),
        },
        "recent": nlargest(limit, recent, key=lambda item: item["created_at"]),
    }
