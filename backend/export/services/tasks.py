"""Analysis run ("task") exporters.

One row per AnalysisRun with its outcome, frozen plan, job identity and the
headline numbers of its stored summary/measurement. Severity counts are the
static *counted* findings (summary schema v2), and only where the static
measurement is available: runs without a summary (queued, running, failed
before measuring) and v2 runs whose static measurement is unavailable (a
failed or partial run: a required static tool failed, or no tool scanned
anything) get empty cells, never zeros or a partial count — exactly like
``total_findings`` in the same row. ``cost_usd`` is None when the AI spend
is unknown; ``cost_complete`` says whether a known cost covers every call
(None when the run records neither).
"""

from __future__ import annotations

import csv
import io
import logging
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

_SEVERITIES = ("critical", "high", "medium", "low", "info")

TASK_HEADERS = [
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
    # v2 columns, appended after the legacy ones
    "purpose",
    "is_complete",
    "canonical_for_plan",
    "plan_hash",
    "profile",
    "job_id",
    "model_id",
    "experiment_id",
    "condition_id",
    "app",
    "repeat_index",
    "sample_hash",
    "tools_run",
    "tools_completed",
    "tools_failed",
    "total_findings",
    "issues",
    "ai_findings",
    "measured_sloc",
    "total_sloc",
    "findings_per_kloc",
    "issues_per_kloc",
    "static_available",
    "cost_usd",
    "error",
    # Columns added by measurement v2 (appended so older readers keep their indexes):
    "measurement_version",
    "cost_complete",
]
# Back-compat alias for the old private name.
_TASK_HEADERS = TASK_HEADERS


def _static_available(task: Any) -> bool | None:
    """The measurement's ``static.available`` (None when the run has no measurement)."""
    m = task.measurement if isinstance(task.measurement, dict) else {}
    if not m:
        return None
    static = m.get("static") if isinstance(m.get("static"), dict) else {}
    return bool(static.get("available"))


def _task_severity_counts(task: Any) -> dict[str, int] | None:
    """Static counted severity counts, or None when they are missing data (not zeros).

    The summary zero-fills ``severity_counts`` from whatever static findings
    were counted, whatever the run status; for a v2 run they are a
    measurement only when ``measurement.static.available`` (else a failed
    run would read as a clean scan and a partial one as a low count).
    Legacy (pre-v2) summaries without a measurement keep their counts.
    """
    summary = task.summary if isinstance(task.summary, dict) else {}
    if any(sev in summary for sev in _SEVERITIES):  # legacy flat summaries
        return {sev: int(summary.get(sev, 0) or 0) for sev in _SEVERITIES}
    counts = summary.get("severity_counts")
    if not isinstance(counts, dict):
        return None
    available = _static_available(task)
    is_v2 = isinstance(summary.get("schema_version"), int) and summary["schema_version"] >= 2
    if available is False or (available is None and is_v2):
        return None
    return {sev: int(counts.get(sev, 0) or 0) for sev in _SEVERITIES}


def _task_target(task: Any) -> str:
    if task.generation_job_id:
        return f"job:{task.generation_job_id}"
    if task.source_code:
        keys = list((task.source_code or {}).keys())
        return "inline:" + ",".join(keys)
    return ""


def _canonical_ids(tasks: list[Any]) -> set:
    """Runs that are their job's canonical (latest complete finished) run under their own plan."""
    from backend.analysis.models import AnalysisRun

    job_ids = {t.generation_job_id for t in tasks if t.generation_job_id}
    if not job_ids:
        return set()
    return set(
        AnalysisRun.objects.filter(
            generation_job_id__in=job_ids,
            status__in=list(AnalysisRun.FINISHED_STATUSES),
            is_complete=True,
        )
        .exclude(purpose=AnalysisRun.Purpose.CANARY)
        .exclude(plan_hash="")
        .order_by("generation_job_id", "plan_hash", "-created_at")
        .distinct("generation_job_id", "plan_hash")
        .values_list("id", flat=True),
    )


def _cost_complete(summary: dict[str, Any], ai: dict[str, Any]) -> bool | None:
    """Whether the recorded AI cost covers every call (summary, else measurement; None if unrecorded)."""
    for source in (summary, ai):
        value = source.get("cost_complete")
        if isinstance(value, bool):
            return value
    return None


def _measurement_numbers(task: Any) -> dict[str, Any]:
    m = task.measurement if isinstance(task.measurement, dict) else {}
    static = m.get("static") if isinstance(m.get("static"), dict) else {}
    available = static.get("available") if static else None
    sloc = m.get("sloc") if isinstance(m.get("sloc"), dict) else {}
    ai = m.get("ai") if isinstance(m.get("ai"), dict) else {}
    summary = task.summary if isinstance(task.summary, dict) else {}
    return {
        "static_available": available,
        # Static totals only when the static measurement is valid: a failed
        # required tool makes them missing data, not a lower count.
        "total_findings": (static.get("findings") or {}).get("total") if available else None,
        "issues": (static.get("issues") or {}).get("total") if available else None,
        "findings_per_kloc": static.get("findings_per_kloc") if available else None,
        "issues_per_kloc": static.get("issues_per_kloc") if available else None,
        "ai_findings": (ai.get("consensus") or {}).get("total") if ai.get("available") else None,
        "measured_sloc": sloc.get("measured"),
        "total_sloc": sloc.get("total"),
        "cost_usd": summary.get("cost_usd"),
        "tools_run": summary.get("tools_run"),
        "tools_completed": summary.get("tools_completed"),
        "tools_failed": summary.get("tools_failed"),
        "measurement_version": m.get("version") or None,
        "cost_complete": _cost_complete(summary, ai),
    }


def _task_record(task: Any, canonical: set) -> dict[str, Any]:
    meta = task.target_meta if isinstance(task.target_meta, dict) else {}
    counts = _task_severity_counts(task)
    profile = (task.plan or {}).get("profile") if isinstance(task.plan, dict) else None
    return {
        "id": str(task.id),
        "name": task.name,
        "status": task.status,
        "target": _task_target(task),
        **{sev: (counts[sev] if counts is not None else None) for sev in _SEVERITIES},
        "duration": task.duration_seconds,
        "created": task.created_at.isoformat(),
        "purpose": task.purpose,
        "is_complete": task.is_complete,
        "canonical_for_plan": task.id in canonical,
        "plan_hash": task.plan_hash,
        "profile": (f"{profile.get('slug')}@{profile.get('version')}" if isinstance(profile, dict) else None),
        "job_id": str(task.generation_job_id) if task.generation_job_id else None,
        "model_id": meta.get("model_id"),
        "experiment_id": meta.get("experiment_id"),
        "condition_id": meta.get("condition_id"),
        "app": meta.get("app_slug"),
        "repeat_index": meta.get("repeat_index"),
        "sample_hash": task.sample_hash,
        **_measurement_numbers(task),
        "error": task.error_message,
    }


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def analysis_tasks_csv(queryset: QuerySet[Any]) -> str:
    tasks = list(queryset)
    canonical = _canonical_ids(tasks)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(TASK_HEADERS)
    for task in tasks:
        record = _task_record(task, canonical)
        writer.writerow([_csv_value(record[h]) for h in TASK_HEADERS])
    return buf.getvalue()


def _metrics_by_tool(task: Any) -> dict[str, Any]:
    """Metrics of the run's succeeded tool executions (legacy runs: their summary copy)."""
    from backend.analysis.models import ToolResult

    out = dict(
        task.results.filter(status=ToolResult.Status.SUCCEEDED)
        .exclude(metrics={})
        .values_list("tool_slug", "metrics"),
    )
    if not out:
        legacy = (task.summary or {}).get("metrics_by_tool")
        if isinstance(legacy, dict):
            return legacy
    return out


def _outcomes(task: Any) -> dict[str, Any] | None:
    from backend.analysis.engine import measurement as measurement_engine

    if not isinstance(task.measurement, dict) or not task.measurement:
        return None
    try:
        return measurement_engine.outcomes(task.measurement)
    except Exception:
        logger.exception("outcomes() failed for analysis run %s", task.id)
        return None


def analysis_tasks_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    tasks = list(queryset)
    canonical = _canonical_ids(tasks)
    out = []
    for task in tasks:
        record = _task_record(task, canonical)
        out.append(
            {
                **record,
                "severity_counts": _task_severity_counts(task),
                "tool_statuses": dict(task.results.values_list("tool_slug", "status")),
                "metrics_by_tool": _metrics_by_tool(task),
                "outcomes": _outcomes(task),
                "measurement": task.measurement or None,
            },
        )
    return out
