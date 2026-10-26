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
    # appended (v2): join keys into the analysis exports
    "model_id",
    "mode",
    "experiment_id",
    "condition_id",
    "repeat_index",
    "prompt_hash",
    "bundle_key",
    "analysis_run_id",
    "analysis_plan_hash",
]


def _canonical_runs(jobs: list[Any]) -> dict[Any, tuple[str, str]]:
    """job id -> (run id, plan hash) of its latest complete measured run (any plan)."""
    from backend.analysis.engine import measurement as measurement_engine
    from backend.analysis.models import AnalysisRun

    run_ids = measurement_engine.canonical_run_ids([j.id for j in jobs], complete_only=True)
    return {
        job_id: (str(run_id), plan_hash)
        for run_id, job_id, plan_hash in AnalysisRun.objects.filter(id__in=run_ids).values_list(
            "id",
            "generation_job_id",
            "plan_hash",
        )
    }


def _job_extra(job: Any, canonical: dict[Any, tuple[str, str]]) -> dict[str, Any]:
    run_id, plan_hash = canonical.get(job.id, (None, None))
    return {
        "model_id": job.model.model_id if job.model_id else None,
        "mode": job.mode,
        "experiment_id": str(job.experiment_id) if job.experiment_id else None,
        "condition_id": job.condition_id,
        "repeat_index": job.repeat_index,
        "prompt_hash": job.prompt_hash or None,
        "bundle_key": job.bundle_key or None,
        "analysis_run_id": run_id,
        "analysis_plan_hash": plan_hash,
    }


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
    jobs = list(queryset.select_related("model", "app_requirement"))
    canonical = _canonical_runs(jobs)
    for job in jobs:
        extra = _job_extra(job, canonical)
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
                *("" if extra[k] is None else extra[k] for k in _JOB_HEADERS[8:]),
            ],
        )
    return buf.getvalue()


def generation_jobs_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    jobs = list(queryset.select_related("model", "app_requirement"))
    canonical = _canonical_runs(jobs)
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
            **_job_extra(job, canonical),
        }
        for job in jobs
    ]
