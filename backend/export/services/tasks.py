"""Analysis task exporters.

Severity counts come in two kinds. ``reported_severity_counts`` counts the
labels exactly as the tools printed them (an open vocabulary: ``HIGH``,
``moderate``, ``warning``, CVSS scores …) and ``unrated_findings`` the
findings of tools that rate nothing. ``normalized_*`` are the heuristic
5-level buckets used for aggregate statistics. A run that has no counts
(still running, or analyzed before the counts were recorded) exports them
blank, not as zero.
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING
from typing import Any

from backend.export.services.csv_safe import safe_row

if TYPE_CHECKING:
    from django.db.models import QuerySet


_LEVELS = ("critical", "high", "medium", "low", "info")

_TASK_HEADERS = [
    "id",
    "name",
    "status",
    "target",
    "generation_run_id",
    "code_scope",
    "total_findings",
    *(f"normalized_{level}" for level in _LEVELS),
    "reported_severity_counts",
    "unrated_findings",
    "duration",
    "created",
]


def _task_severity_counts(task: Any) -> dict[str, int] | None:
    """The run's normalized (heuristic) counts per level, or ``None`` when it recorded none."""
    summary = task.summary if isinstance(task.summary, dict) else {}
    if "critical" in summary or "high" in summary:  # legacy summary shape
        return {level: summary.get(level, 0) for level in _LEVELS}
    counts = summary.get("severity_counts")
    if not isinstance(counts, dict):
        return None
    return {level: counts.get(level, 0) for level in _LEVELS}


def _reported_counts(task: Any) -> tuple[dict[str, int] | None, int | None]:
    """The tools' own labels counted, and the unrated findings (``None`` before runs recorded them)."""
    summary = task.summary if isinstance(task.summary, dict) else {}
    reported = summary.get("reported_severity_counts")
    unrated = summary.get("unrated_findings")
    return (reported if isinstance(reported, dict) else None), (unrated if isinstance(unrated, int) else None)


def _total_findings(task: Any) -> int | None:
    summary = task.summary if isinstance(task.summary, dict) else {}
    total = summary.get("total_findings")
    return total if isinstance(total, int) else None


def _task_target(task: Any) -> str:
    if task.generation_job_id:
        return f"run:{task.generation_job_id}"
    if task.source_code:
        keys = list((task.source_code or {}).keys())
        return "inline:" + ",".join(keys)
    return ""


def analysis_tasks_csv(queryset: QuerySet[Any], *, spreadsheet_safe: bool = True) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_TASK_HEADERS)
    for task in queryset:
        counts = _task_severity_counts(task)
        reported, unrated = _reported_counts(task)
        total = _total_findings(task)
        writer.writerow(
            safe_row(
                [
                    str(task.id),
                    task.name,
                    task.status,
                    _task_target(task),
                    str(task.generation_job_id) if task.generation_job_id else "",
                    task.code_scope,
                    "" if total is None else total,
                    *(("" if counts is None else counts[level]) for level in _LEVELS),
                    "" if reported is None else json.dumps(reported, sort_keys=True),
                    "" if unrated is None else unrated,
                    task.duration_seconds if task.duration_seconds is not None else "",
                    task.created_at.isoformat(),
                ],
                enabled=spreadsheet_safe,
            ),
        )
    return buf.getvalue()


def analysis_tasks_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    rows = []
    for task in queryset:
        reported, unrated = _reported_counts(task)
        rows.append(
            {
                "id": str(task.id),
                "name": task.name,
                "status": task.status,
                "target": _task_target(task),
                "generation_run_id": str(task.generation_job_id) if task.generation_job_id else None,
                # app, model or slot (blank for an inline-source analysis).
                "code_scope": task.code_scope or None,
                "total_findings": _total_findings(task),
                "normalized_severity_counts": _task_severity_counts(task),
                "reported_severity_counts": reported,
                "unrated_findings": unrated,
                "metrics_by_tool": (task.summary or {}).get("metrics_by_tool", {}),
                "duration": task.duration_seconds,
                "created": task.created_at.isoformat(),
            },
        )
    return rows
