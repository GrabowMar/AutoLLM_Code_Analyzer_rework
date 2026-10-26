"""Generation-side SLOC of jobs, through the analysis sample resolver.

Reports never count lines with rules of their own: a job's code is resolved
exactly as an analysis run would see it (``sample.resolve_job``: lockfiles,
build output and assets excluded, SLOC by ``sloc-v1``, model-written lines as
the measured scope). Jobs whose code cannot be resolved (failed, no code) are
counted as unavailable — missing, never 0 lines. Analysed jobs' reports use
the stored measurement instead (``generators._loc_block``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine import sample as sample_engine
from backend.analysis.engine.types import SampleUnavailable

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

_FRONTEND_PREFIX = "frontend/"


def loc_from_job(job: GenerationJob) -> dict[str, Any]:
    """SLOC of one job's resolved sample; every value None when it cannot be resolved."""
    try:
        resolved = sample_engine.resolve_job(job)
    except SampleUnavailable as exc:
        return {
            "backend_loc": None,
            "frontend_loc": None,
            "total_loc": None,
            "measured_loc": None,
            "files": None,
            "unavailable": getattr(exc, "code", "no_code"),
        }
    included = resolved.included()
    frontend = sum(f.sloc for f in included if f.path.startswith(_FRONTEND_PREFIX))
    total = sum(f.sloc for f in included)
    return {
        "backend_loc": total - frontend,
        "frontend_loc": frontend,
        "total_loc": total,
        "measured_loc": sum(f.measured_sloc for f in included if f.measured),
        "files": len(included),
    }


def loc_for_jobs(jobs) -> dict[str, Any]:
    """Aggregate SLOC across a queryset / iterable of GenerationJobs (resolvable ones only)."""

    keys = ("backend_loc", "frontend_loc", "total_loc", "measured_loc")
    totals = dict.fromkeys(keys, 0)
    per_job: list[dict[str, Any]] = []
    counted = 0
    unavailable = 0
    for job in jobs:
        loc = loc_from_job(job)
        per_job.append({"job_id": str(job.id), **loc})
        if loc["total_loc"] is None:
            unavailable += 1
            continue
        counted += 1
        for k in keys:
            totals[k] += loc[k]
    out: dict[str, Any] = {k: (totals[k] if counted else None) for k in keys}
    out["counted"] = counted
    out["unavailable"] = unavailable
    out["per_job"] = per_job
    return out
