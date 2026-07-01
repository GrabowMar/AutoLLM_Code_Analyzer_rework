"""Analysis task exporters."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.db.models import QuerySet


_TASK_HEADERS = [
    "id",
    "name",
    "status",
    "target",
    "critical",
    "high",
    "medium",
    "low",
    "info",
    "duration",
    "created",
]


def _task_severity_counts(task: Any) -> dict[str, int]:
    summary = task.summary or {}
    if "critical" in summary or "high" in summary:
        return {
            "critical": summary.get("critical", 0),
            "high": summary.get("high", 0),
            "medium": summary.get("medium", 0),
            "low": summary.get("low", 0),
            "info": summary.get("info", 0),
        }
    counts = summary.get("severity_counts", {})
    return {
        "critical": counts.get("critical", 0),
        "high": counts.get("high", 0),
        "medium": counts.get("medium", 0),
        "low": counts.get("low", 0),
        "info": counts.get("info", 0),
    }


def _task_target(task: Any) -> str:
    if task.generation_job_id:
        return f"job:{task.generation_job_id}"
    if task.source_code:
        keys = list((task.source_code or {}).keys())
        return "inline:" + ",".join(keys)
    return ""


def analysis_tasks_csv(queryset: QuerySet[Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_TASK_HEADERS)
    for task in queryset:
        counts = _task_severity_counts(task)
        writer.writerow(
            [
                str(task.id),
                task.name,
                task.status,
                _task_target(task),
                counts["critical"],
                counts["high"],
                counts["medium"],
                counts["low"],
                counts["info"],
                task.duration_seconds if task.duration_seconds is not None else "",
                task.created_at.isoformat(),
            ],
        )
    return buf.getvalue()


def analysis_tasks_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(task.id),
            "name": task.name,
            "status": task.status,
            "target": _task_target(task),
            "severity_counts": _task_severity_counts(task),
            "duration": task.duration_seconds,
            "created": task.created_at.isoformat(),
        }
        for task in queryset
    ]
