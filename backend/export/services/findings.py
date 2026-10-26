"""Findings exporters: CSV, JSON, and SARIF 2.1.0.

Rows carry the research context of each finding (job, model, experiment,
condition, run status and plan, tool version, code origin, AI/counted flags,
fingerprint, dedup issue key, CWE) so an export can be analysed on its own.
Column names are a public contract for researchers' scripts: legacy names
(``task_id``, ``analyzer``, ``message``) are kept and new columns are only
ever appended.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Iterator

    from django.db.models import QuerySet


_SARIF_LEVEL: dict[str, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}
_SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
_FINGERPRINT_KEY = "llmlab/v1"

FINDING_HEADERS = [
    "id",
    "task_id",
    "analyzer",
    "severity",
    "file_path",
    "line",
    "rule_id",
    "message",
    "cwe",
    "created_at",
    # appended (v2) — research context
    "severity_rank",
    "category",
    "confidence",
    "end_line",
    "column",
    "file_origin",
    "in_measured_scope",
    "counted",
    "is_ai",
    "is_consensus",
    "grounding",
    "fingerprint",
    "issue_key",
    "tool_version",
    "tool_status",
    "run_status",
    "run_complete",
    "run_purpose",
    "plan_hash",
    "job_id",
    "model_id",
    "experiment_id",
    "condition_id",
    "app",
    "repeat_index",
    "mode",
]

_SELECT_RELATED = ("result", "run", "run__generation_job", "run__generation_job__model")


def _cwe(finding: Any) -> list[str]:
    """CWE ids from the normalized field (legacy rows: ``tool_specific_data['cwe']``)."""
    if isinstance(finding.cwe, list) and finding.cwe:
        return [str(c) for c in finding.cwe if c]
    legacy = (finding.tool_specific_data or {}).get("cwe") if isinstance(finding.tool_specific_data, dict) else None
    if isinstance(legacy, list):
        return [str(c) for c in legacy if c]
    return [str(legacy)] if legacy else []


def _job_context(run: Any) -> dict[str, Any]:
    """Job identity from the run's submission snapshot, falling back to the live job."""
    meta = run.target_meta if isinstance(run.target_meta, dict) else {}
    job = run.generation_job if run.generation_job_id else None
    model_id = meta.get("model_id") or (job.model.model_id if job is not None and job.model_id else "")
    return {
        "job_id": str(run.generation_job_id) if run.generation_job_id else (meta.get("job_id") or ""),
        "model_id": model_id or "",
        "experiment_id": meta.get("experiment_id") or (str(job.experiment_id) if job and job.experiment_id else ""),
        "condition_id": meta.get("condition_id") or (job.condition_id if job and job.condition_id else ""),
        "app": meta.get("app_slug") or "",
        "repeat_index": meta.get("repeat_index") if meta.get("repeat_index") is not None else "",
        "mode": meta.get("mode") or (job.mode if job is not None else ""),
    }


def finding_record(finding: Any) -> dict[str, Any]:
    """One finding as a flat dict keyed by FINDING_HEADERS (JSON-friendly values)."""
    result = finding.result
    run = finding.run
    cwe = _cwe(finding)
    return {
        "id": str(finding.id),
        "task_id": str(run.id),
        "analyzer": finding.tool_slug or result.tool_slug,
        "severity": finding.severity,
        "file_path": finding.file_path,
        "line": finding.line_number,
        "rule_id": finding.rule_id,
        "message": finding.title,
        "cwe": ";".join(cwe),
        "created_at": finding.created_at.isoformat(),
        "severity_rank": finding.severity_rank,
        "category": finding.category,
        "confidence": finding.confidence,
        "end_line": finding.end_line,
        "column": finding.column_number,
        "file_origin": finding.file_origin,
        "in_measured_scope": finding.in_measured_scope,
        "counted": finding.counted,
        "is_ai": finding.is_ai,
        "is_consensus": finding.is_consensus,
        "grounding": finding.grounding,
        "fingerprint": finding.fingerprint,
        "issue_key": finding.issue_key,
        "tool_version": result.tool_version,
        "tool_status": result.status,
        "run_status": run.status,
        "run_complete": run.is_complete,
        "run_purpose": run.purpose,
        "plan_hash": run.plan_hash,
        **_job_context(run),
    }


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def finding_row(finding: Any) -> list[Any]:
    record = finding_record(finding)
    return [_csv_value(record[h]) for h in FINDING_HEADERS]


def iter_findings(queryset: QuerySet[Any] | Iterable[Any]) -> Iterator[Any]:
    if hasattr(queryset, "select_related"):
        queryset = queryset.select_related(*_SELECT_RELATED)
    yield from queryset


def findings_csv(queryset: QuerySet[Any]) -> str:
    """Return CSV string of findings."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(FINDING_HEADERS)
    for f in iter_findings(queryset):
        writer.writerow(finding_row(f))
    return buf.getvalue()


def findings_json(queryset: QuerySet[Any]) -> list[dict[str, Any]]:
    """Return list of finding dicts (``cwe`` as a list)."""
    out = []
    for f in iter_findings(queryset):
        record = finding_record(f)
        record["cwe"] = _cwe(f)
        out.append(record)
    return out


# ── SARIF ─────────────────────────────────────────────────────────────────────


def _invocation(result: Any) -> dict[str, Any]:
    """SARIF invocation for one tool execution — a failed tool says so explicitly."""
    from backend.analysis.models import ToolResult

    invocation: dict[str, Any] = {
        # not_applicable: nothing in scope, which ran correctly; anything
        # else that is not 'succeeded' (failed, timed out, skipped, cancelled,
        # still pending) is not a successful execution.
        "executionSuccessful": result.status in ToolResult.OK_STATUSES,
        "properties": {
            "analysis_run_id": str(result.run_id),
            "status": result.status,
            "failure_reason": result.failure_reason or None,
            "image": result.image_ref or None,
            "config_hash": result.config_hash or None,
            "attempt": result.attempt,
            "duration_seconds": result.duration_seconds,
        },
    }
    if result.exit_code is not None:
        invocation["exitCode"] = result.exit_code
    if isinstance(result.argv, list) and result.argv:
        invocation["arguments"] = [str(a) for a in result.argv]
        invocation["commandLine"] = " ".join(str(a) for a in result.argv)
    if result.started_at:
        invocation["startTimeUtc"] = result.started_at.isoformat()
    if result.finished_at:
        invocation["endTimeUtc"] = result.finished_at.isoformat()
    if result.error_message:
        invocation["toolExecutionNotifications"] = [
            {"level": "error", "message": {"text": result.error_message[:2000]}},
        ]
    return invocation


def _sarif_result(f: Any) -> dict[str, Any]:
    cwe = _cwe(f)
    entry: dict[str, Any] = {
        "ruleId": f.rule_id or "unknown",
        "level": _SARIF_LEVEL.get(f.severity, "warning"),
        "message": {"text": f.title},
        "locations": [],
        # SARIF levels collapse the 5-bucket scale; keep the original and the
        # research flags alongside.
        "properties": {
            "severity": f.severity,
            "category": f.category,
            "confidence": f.confidence,
            "cwe": cwe,
            "tags": cwe,
            "file_origin": f.file_origin,
            "counted": f.counted,
            "in_measured_scope": f.in_measured_scope,
            "is_ai": f.is_ai,
            "is_consensus": f.is_consensus,
            "grounding": f.grounding or None,
            "issue_key": f.issue_key or None,
            "analysis_run_id": str(f.run_id),
        },
    }
    if f.fingerprint:
        entry["partialFingerprints"] = {_FINGERPRINT_KEY: f.fingerprint}
    if f.file_path:
        location: dict[str, Any] = {"physicalLocation": {"artifactLocation": {"uri": f.file_path}}}
        if f.line_number:
            region: dict[str, Any] = {"startLine": f.line_number}
            if f.end_line and f.end_line >= f.line_number:
                region["endLine"] = f.end_line
            if f.column_number:
                region["startColumn"] = f.column_number
            location["physicalLocation"]["region"] = region
        entry["locations"].append(location)
    return entry


def findings_sarif(
    queryset: QuerySet[Any],
    tool_results: QuerySet[Any] | Iterable[Any] | None = None,
) -> dict[str, Any]:
    """SARIF 2.1.0: one SARIF run per (tool, tool version).

    Each SARIF run lists the tool executions it covers as ``invocations``
    with ``executionSuccessful`` — including executions that produced no
    findings or failed, so an empty result list is never mistaken for a
    clean scan. *tool_results* defaults to the results of the exported
    findings; pass the full execution scope to include tools without findings.
    """
    from backend.analysis.models import AnalyzerTool

    findings = list(iter_findings(queryset))
    if tool_results is None:
        seen: dict[Any, Any] = {}
        for f in findings:
            seen.setdefault(f.result_id, f.result)
        results = list(seen.values())
    else:
        results = list(tool_results)

    groups: dict[tuple[str, str], dict[str, Any]] = {}

    def group(slug: str, version: str, kind: str) -> dict[str, Any]:
        return groups.setdefault(
            (slug, version),
            {"invocations": [], "results": [], "result_ids": set(), "kind": kind},
        )

    for result in results:
        bucket = group(result.tool_slug, result.tool_version or "", result.tool_kind)
        if result.id not in bucket["result_ids"]:
            bucket["result_ids"].add(result.id)
            bucket["invocations"].append(_invocation(result))
    for f in findings:
        bucket = group(f.result.tool_slug, f.result.tool_version or "", f.result.tool_kind)
        if f.result_id not in bucket["result_ids"]:
            bucket["result_ids"].add(f.result_id)
            bucket["invocations"].append(_invocation(f.result))
        bucket["results"].append(_sarif_result(f))

    docs = dict(AnalyzerTool.objects.filter(slug__in={k[0] for k in groups}).values_list("slug", "docs_url"))
    runs = []
    for (slug, version), bucket in sorted(groups.items()):
        driver: dict[str, Any] = {"name": slug}
        if version:
            # The version the image manifest recorded for this execution —
            # never a placeholder. Unknown versions are simply omitted.
            driver["version"] = version
        if docs.get(slug):
            driver["informationUri"] = docs[slug]
        runs.append(
            {
                "tool": {"driver": driver},
                "invocations": bucket["invocations"],
                "results": bucket["results"],
                # AI-reviewer results are opinions, not measurements.
                "properties": {"tool_kind": bucket["kind"]},
            },
        )

    return {
        "version": "2.1.0",
        "$schema": _SARIF_SCHEMA,
        "runs": runs,
    }
