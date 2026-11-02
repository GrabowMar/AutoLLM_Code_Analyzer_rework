"""Generation job exporters."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from typing import Any

from backend.export.services.csv_safe import safe_row

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.db.models import QuerySet


_JOB_HEADERS = [
    "id",
    "model",
    "model_id",
    "template",
    "app",
    "stack",
    "experiment_id",
    "condition_id",
    "condition_label",
    "repeat_index",
    "status",
    "started_at",
    "completed_at",
    "tokens_used",
    "duration",
]


# The columns a jobs row reads: never the generated code, bundle or prompts.
_JOB_ONLY = (
    "id",
    "model",
    "model__model_name",
    "model__model_id",
    "app_requirement",
    "app_requirement__name",
    "app_requirement__slug",
    "stack_slug",
    "experiment",
    "condition",
    "repeat_index",
    "status",
    "started_at",
    "completed_at",
    "metrics",
    "duration_seconds",
)


def condition_labels(condition_ids: Iterable[Any]) -> dict[Any, str]:
    """Per experiment condition id, its name in the study tables: the label, else ``<model> / <method>``."""
    from backend.generation.models import ExperimentCondition
    from backend.statistics.services.study import condition_label

    ids = {c for c in condition_ids if c}
    if not ids:
        return {}
    conditions = ExperimentCondition.objects.filter(id__in=ids).select_related("model", "profile", "harness_preset")
    return {c.id: condition_label(c) for c in conditions}


def _export_jobs(queryset: QuerySet[Any]) -> tuple[list[Any], dict[Any, str]]:
    jobs = list(queryset.select_related("model", "app_requirement").only(*_JOB_ONLY))
    return jobs, condition_labels(j.condition_id for j in jobs)


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


def _job_cell(job: Any, labels: dict[Any, str]) -> dict[str, Any]:
    """Which app and experiment cell the job is: ``template`` alone conflates stack and app."""
    return {
        "model_id": job.model.model_id if job.model_id else None,
        "app": job.app_requirement.slug if job.app_requirement_id else None,
        "stack": job.stack_slug or None,
        "experiment_id": str(job.experiment_id) if job.experiment_id else None,
        "condition_id": str(job.condition_id) if job.condition_id else None,
        "condition_label": labels.get(job.condition_id) if job.condition_id else None,
        "repeat_index": job.repeat_index,
    }


def generation_jobs_csv(queryset: QuerySet[Any], *, spreadsheet_safe: bool = True) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_JOB_HEADERS)
    jobs, labels = _export_jobs(queryset)
    for job in jobs:
        cell = {k: "" if v is None else v for k, v in _job_cell(job, labels).items()}
        writer.writerow(
            safe_row(
                [
                    str(job.id),
                    job.model.model_name if job.model_id else "",
                    cell["model_id"],
                    _job_template(job),
                    cell["app"],
                    cell["stack"],
                    cell["experiment_id"],
                    cell["condition_id"],
                    cell["condition_label"],
                    cell["repeat_index"],
                    job.status,
                    job.started_at.isoformat() if job.started_at else "",
                    job.completed_at.isoformat() if job.completed_at else "",
                    _job_tokens(job),
                    job.duration_seconds if job.duration_seconds is not None else "",
                ],
                enabled=spreadsheet_safe,
            ),
        )
    return buf.getvalue()


def generation_jobs_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    jobs, labels = _export_jobs(queryset)
    return [
        {
            "id": str(job.id),
            "model": job.model.model_name if job.model_id else None,
            "template": _job_template(job) or None,
            **_job_cell(job, labels),
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "tokens_used": _job_tokens(job) or None,
            "duration": job.duration_seconds,
        }
        for job in jobs
    ]
