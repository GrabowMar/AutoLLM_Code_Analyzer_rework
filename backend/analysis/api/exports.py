"""Per-run exports: JSON (everything), CSV (findings) and SARIF 2.1.0.

SARIF: one ``runs[]`` entry per ToolResult with the real tool version from
the image manifest and ``invocations[0].executionSuccessful`` from the tool's
status (succeeded / not_applicable only), so a consumer can tell "no
findings" from "the tool did not run". AI-reviewer results are consensus
findings, one per consensus cluster (``issue_key``), not every replicate.
"""

from __future__ import annotations

import csv
import io
import logging
import shlex
from datetime import UTC
from typing import Any
from urllib.parse import quote

from django.db.models import F
from django.utils import timezone

from backend.analysis.api.serializers import finding_payload
from backend.analysis.api.serializers import run_detail
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult

logger = logging.getLogger(__name__)

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
EXPORT_SCHEMA_VERSION = 1
SARIF_LEVELS = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
# GitHub code scanning's numeric security severity scale.
SECURITY_SEVERITY = {"critical": "9.5", "high": "8.0", "medium": "5.5", "low": "3.0", "info": "0.0"}
CSV_COLUMNS = (
    "finding_id",
    "run_id",
    "run_status",
    "run_complete",
    "plan_hash",
    "generation_job_id",
    "model_slug",
    "app_slug",
    "harness",
    "experiment_id",
    "condition_id",
    "tool_slug",
    "tool_version",
    "tool_status",
    "rule_id",
    "title",
    "severity",
    "severity_rank",
    "native_severity",
    "category",
    "cwe",
    "confidence",
    "file_path",
    "line_number",
    "end_line",
    "column_number",
    "file_origin",
    "in_measured_scope",
    "counted",
    "is_ai",
    "is_consensus",
    "grounding",
    "review_sample_index",
    "fingerprint",
    "issue_key",
)
# Spreadsheet formula injection: analysed apps (and AI output about them)
# control finding text, so text cells starting with these are neutralised.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _ordered_findings(run: AnalysisRun):
    return run.findings.select_related("review_sample").order_by(
        "severity_rank",
        "file_path",
        F("line_number").asc(nulls_last=True),
        "id",
    )


def run_json(run: AnalysisRun) -> dict[str, Any]:
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": timezone.now().isoformat(),
        "run": run_detail(run),
        "sample_manifest": run.sample_manifest or {},
        "findings": [finding_payload(f) for f in _ordered_findings(run)],
    }


def _csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def run_csv(run: AnalysisRun) -> str:
    results = {r.id: r for r in run.results.all()}
    meta = run.target_meta or {}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for f in _ordered_findings(run):
        result = results.get(f.result_id)
        row = {
            "finding_id": str(f.id),
            "run_id": str(run.id),
            "run_status": run.status,
            "run_complete": run.is_complete,
            "plan_hash": run.plan_hash,
            "generation_job_id": str(run.generation_job_id or ""),
            "model_slug": meta.get("model_slug") or "",
            "app_slug": meta.get("app_slug") or "",
            "harness": meta.get("harness") or "",
            "experiment_id": meta.get("experiment_id") or "",
            "condition_id": meta.get("condition_id") or "",
            "tool_slug": f.tool_slug,
            "tool_version": result.tool_version if result else "",
            "tool_status": result.status if result else "",
            "rule_id": f.rule_id,
            "title": f.title,
            "severity": f.severity,
            "severity_rank": f.severity_rank,
            "native_severity": f.native_severity,
            "category": f.category,
            "cwe": ";".join(str(c) for c in f.cwe or []),
            "confidence": f.confidence,
            "file_path": f.file_path,
            "line_number": f.line_number if f.line_number is not None else "",
            "end_line": f.end_line if f.end_line is not None else "",
            "column_number": f.column_number if f.column_number is not None else "",
            "file_origin": f.file_origin,
            "in_measured_scope": f.in_measured_scope,
            "counted": f.counted,
            "is_ai": f.is_ai,
            "is_consensus": f.is_consensus,
            "grounding": f.grounding,
            "review_sample_index": f.review_sample.index if f.review_sample_id else "",
            "fingerprint": f.fingerprint,
            "issue_key": f.issue_key,
        }
        writer.writerow([_csv_cell(row[c]) for c in CSV_COLUMNS])
    return buffer.getvalue()


# --------------------------------------------------------------------------
# SARIF
# --------------------------------------------------------------------------


def _sarif_time(value) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _specs() -> dict[str, Any]:
    try:
        from backend.analysis.engine import catalog

        return catalog.load_specs()
    except Exception as exc:  # noqa: BLE001 - names/URIs are cosmetic in SARIF
        logger.warning("catalog unavailable for SARIF: %s", exc)
        return {}


def _invocation(result: ToolResult) -> dict[str, Any]:
    ok = result.status in ToolResult.OK_STATUSES
    invocation: dict[str, Any] = {"executionSuccessful": ok}
    if result.exit_code is not None:
        invocation["exitCode"] = result.exit_code
    argv = [str(a) for a in result.argv or []]
    if argv:
        invocation["arguments"] = argv
        invocation["commandLine"] = shlex.join(argv)
    if start := _sarif_time(result.started_at):
        invocation["startTimeUtc"] = start
    if end := _sarif_time(result.finished_at):
        invocation["endTimeUtc"] = end
    notifications = []
    if result.status == ToolResult.Status.NOT_APPLICABLE:
        notifications.append(
            {"level": "note", "message": {"text": "Not applicable: no files in scope for this tool."}},
        )
    elif not ok:
        reason = result.failure_reason or result.status
        text = f"{result.status}: {reason}"
        if result.error_message:
            text = f"{text}: {result.error_message[:2000]}"
        notification: dict[str, Any] = {"level": "error", "message": {"text": text}}
        if result.failure_reason:
            notification["descriptor"] = {"id": result.failure_reason}
        notifications.append(notification)
    if notifications:
        invocation["toolExecutionNotifications"] = notifications
    invocation["properties"] = {
        "status": result.status,
        "failure_reason": result.failure_reason,
        "attempt": result.attempt,
        "duration_seconds": result.duration_seconds,
        "config_hash": result.config_hash,
        "image_ref": result.image_ref,
        "required": result.required,
    }
    return invocation


def _location(f: Finding) -> dict[str, Any] | None:
    if not f.file_path:
        return None
    physical: dict[str, Any] = {"artifactLocation": {"uri": quote(f.file_path), "uriBaseId": "SRCROOT"}}
    if f.line_number is not None and f.line_number >= 1:
        region: dict[str, Any] = {"startLine": f.line_number}
        if f.end_line is not None and f.end_line >= f.line_number:
            region["endLine"] = f.end_line
        if f.column_number is not None and f.column_number >= 1:
            region["startColumn"] = f.column_number
        if f.code_snippet:
            region["snippet"] = {"text": f.code_snippet[:4000]}
        physical["region"] = region
    return {"physicalLocation": physical}


def _cwe_tags(cwes) -> list[str]:
    tags = []
    for cwe in cwes or []:
        number = str(cwe).upper().removeprefix("CWE-")
        if number.isdigit():
            tags.append(f"external/cwe/cwe-{number}")
    return tags


def _sarif_findings(run: AnalysisRun) -> dict[Any, list[Finding]]:
    by_result: dict[Any, list[Finding]] = {}
    seen_ai_clusters: set[tuple[Any, str]] = set()
    for f in _ordered_findings(run).order_by(
        "severity_rank",
        "file_path",
        F("line_number").asc(nulls_last=True),
        F("review_sample__index").asc(nulls_last=True),
        "id",
    ):
        if f.is_ai:
            if not f.is_consensus:
                continue
            if f.issue_key:
                key = (f.result_id, f.issue_key)
                if key in seen_ai_clusters:
                    continue
                seen_ai_clusters.add(key)
        by_result.setdefault(f.result_id, []).append(f)
    return by_result


def run_sarif(run: AnalysisRun) -> dict[str, Any]:
    specs = _specs()
    findings = _sarif_findings(run)
    sarif_runs = []
    for result in run.results.order_by("position", "tool_slug"):
        spec = specs.get(result.tool_slug)
        rules: dict[str, dict[str, Any]] = {}
        rule_index: dict[str, int] = {}
        sarif_results = []
        for f in findings.get(result.id, []):
            rule_id = f.rule_id or result.tool_slug
            if rule_id not in rules:
                tags = [f.category, *_cwe_tags(f.cwe)]
                rules[rule_id] = {
                    "id": rule_id,
                    "shortDescription": {"text": (f.title or rule_id)[:1000]},
                    "properties": {"category": f.category, "cwe": list(f.cwe or []), "tags": tags},
                }
                rule_index[rule_id] = len(rule_index)
            entry: dict[str, Any] = {
                "ruleId": rule_id,
                "ruleIndex": rule_index[rule_id],
                "level": SARIF_LEVELS.get(f.severity, "note"),
                "kind": "fail",
                "message": {"text": f.title or rule_id},
                "properties": {
                    "severity": f.severity,
                    "security-severity": SECURITY_SEVERITY.get(f.severity, "0.0"),
                    "native_severity": f.native_severity,
                    "category": f.category,
                    "cwe": list(f.cwe or []),
                    "confidence": f.confidence,
                    "file_origin": f.file_origin,
                    "in_measured_scope": f.in_measured_scope,
                    "counted": f.counted,
                    "is_ai": f.is_ai,
                    "is_consensus": f.is_consensus,
                    "grounding": f.grounding,
                    "issue_key": f.issue_key,
                },
            }
            if location := _location(f):
                entry["locations"] = [location]
            if f.fingerprint:
                entry["partialFingerprints"] = {"llmlabFingerprint/v1": f.fingerprint}
            sarif_results.append(entry)
        driver: dict[str, Any] = {"name": result.tool_slug, "rules": list(rules.values())}
        if spec is not None:
            driver["fullName"] = spec.name
            if spec.docs_url:
                driver["informationUri"] = spec.docs_url
        if result.tool_version:
            driver["version"] = result.tool_version
        sarif_runs.append(
            {
                "tool": {"driver": driver},
                "invocations": [_invocation(result)],
                "automationDetails": {"id": f"llmlab-analysis/{run.id}/{result.tool_slug}"},
                "originalUriBaseIds": {"SRCROOT": {"description": {"text": "Root of the analysed sample"}}},
                "columnKind": "unicodeCodePoints",
                "results": sarif_results,
                "properties": {
                    "llmlab": {
                        "run_id": str(run.id),
                        "run_status": run.status,
                        "plan_hash": run.plan_hash,
                        "sample_hash": run.sample_hash,
                        "tool_kind": result.tool_kind,
                        "category": result.category,
                        "generation_job_id": str(run.generation_job_id or ""),
                    },
                },
            },
        )
    return {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": sarif_runs}
