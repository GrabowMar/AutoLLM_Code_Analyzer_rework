"""LOC counting from generation job result_data text fields."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob


def _count_lines(text: str | None, comment_prefix: str = "#") -> int:
    if not text:
        return 0
    return sum(1 for line in str(text).splitlines() if line.strip() and not line.strip().startswith(comment_prefix))


def loc_from_job(job: GenerationJob) -> dict[str, int]:
    """Count non-empty, non-comment lines from a job's generated code."""

    data = job.result_data or {}
    backend = data.get("backend_code") or data.get("backend") or ""
    frontend = data.get("frontend_code") or data.get("frontend") or ""
    backend_loc = _count_lines(backend, "#") if backend else 0
    frontend_loc = _count_lines(frontend, "//") if frontend else 0
    return {
        "backend_loc": backend_loc,
        "frontend_loc": frontend_loc,
        "total_loc": backend_loc + frontend_loc,
    }


def loc_for_jobs(jobs) -> dict[str, Any]:
    """Aggregate LOC across a queryset / iterable of GenerationJobs."""

    totals = {"backend_loc": 0, "frontend_loc": 0, "total_loc": 0}
    per_job: list[dict[str, Any]] = []
    counted = 0
    for job in jobs:
        loc = loc_from_job(job)
        if loc["total_loc"] > 0:
            counted += 1
        for k in totals:
            totals[k] += loc[k]
        per_job.append({"job_id": str(job.id), **loc})
    totals["counted"] = counted
    totals["per_job"] = per_job
    return totals
