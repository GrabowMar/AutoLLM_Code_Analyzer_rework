"""Generation job exporters."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.db.models import QuerySet


_JOB_HEADERS = [
    "id",
    "model",
    "template",
    "status",
    "started_at",
    "completed_at",
    "tokens_used",
    "duration",
]


def _job_tokens(job: Any) -> int | str:
    metrics = job.metrics or {}
    return metrics.get("total_tokens", metrics.get("tokens_used", ""))


def _job_template(job: Any) -> str:
    if job.stack_slug:
        return job.stack_slug
    if job.app_requirement_id:
        try:
            return job.app_requirement.name
        except AttributeError:
            return str(job.app_requirement_id)
    return ""


def generation_jobs_csv(queryset: QuerySet[Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_JOB_HEADERS)
    for job in queryset.select_related(
        "model",
        "app_requirement",
    ):
        writer.writerow(
            [
                str(job.id),
                job.model.model_name if job.model_id else "",
                _job_template(job),
                job.status,
                job.started_at.isoformat() if job.started_at else "",
                job.completed_at.isoformat() if job.completed_at else "",
                _job_tokens(job),
                job.duration_seconds if job.duration_seconds is not None else "",
            ],
        )
    return buf.getvalue()


def generation_jobs_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(job.id),
            "model": job.model.model_name if job.model_id else None,
            "template": _job_template(job) or None,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "tokens_used": _job_tokens(job) or None,
            "duration": job.duration_seconds,
        }
        for job in queryset.select_related(
            "model",
            "app_requirement",
        )
    ]
